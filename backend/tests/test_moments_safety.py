from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import moments as moments_mod


def _make_material(mat_id: str = "mat-test-1", **overrides) -> SimpleNamespace:
    defaults = dict(
        id=mat_id,
        source_type="youtube",
        source_url="https://youtu.be/dQw4w9WgXcQ",
        file_path=None,
        title="Test Video",
        duration_seconds=120.0,
        raw_text="[0:10] Step one explain\n[0:45] Step two explain",
        summary="",
        key_moments=[],
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_session() -> SimpleNamespace:
    return SimpleNamespace(
        commit=AsyncMock(),
        refresh=AsyncMock(),
    )


async def test_missing_video_preserves_existing_screenshots_and_moments(tmp_path, monkeypatch):
    mat = _make_material()
    out_dir = tmp_path / "materials" / mat.id / "moments"
    out_dir.mkdir(parents=True)
    img1 = out_dir / "moment_01_10.jpg"
    img2 = out_dir / "moment_02_45.jpg"
    img1.write_bytes(b"original-jpg-1")
    img2.write_bytes(b"original-jpg-2")

    existing_moments = [
        {"t_sec": 10.0, "caption": "Old 1", "image_relpath": f"materials/{mat.id}/moments/moment_01_10.jpg"},
        {"t_sec": 45.0, "caption": "Old 2", "image_relpath": f"materials/{mat.id}/moments/moment_02_45.jpg"},
    ]
    mat.key_moments = list(existing_moments)

    monkeypatch.setattr(moments_mod, "_moments_dir", lambda _mid: out_dir)
    monkeypatch.setattr(moments_mod, "_atlas_dir", lambda _mid: tmp_path / "atlas")
    monkeypatch.setattr(moments_mod, "resolve_video_path", AsyncMock(return_value=None))
    monkeypatch.setattr("app.services.key_pool.get_app_settings", AsyncMock(return_value={}))

    llm_spy = AsyncMock(side_effect=AssertionError("LLM must not be called when preserving existing moments"))
    monkeypatch.setattr(moments_mod, "with_llm_fallback", llm_spy)

    session = _make_session()
    res = await moments_mod.build_key_moments(session, mat)

    assert res is mat
    assert img1.read_bytes() == b"original-jpg-1"
    assert img2.read_bytes() == b"original-jpg-2"
    assert mat.key_moments == existing_moments
    assert llm_spy.await_count == 0
    assert session.commit.await_count == 0


async def test_missing_video_without_existing_images_saves_text_only_without_deleting_files(
    tmp_path, monkeypatch
):
    mat = _make_material(key_moments=[])
    out_dir = tmp_path / "materials" / mat.id / "moments"
    out_dir.mkdir(parents=True)
    stray = out_dir / "keep_me.jpg"
    stray.write_bytes(b"stray-bytes")

    monkeypatch.setattr(moments_mod, "_moments_dir", lambda _mid: out_dir)
    monkeypatch.setattr(moments_mod, "_atlas_dir", lambda _mid: tmp_path / "atlas")
    monkeypatch.setattr(moments_mod, "resolve_video_path", AsyncMock(return_value=None))
    monkeypatch.setattr(moments_mod, "load_atlas_for_material", lambda **_kw: [])
    monkeypatch.setattr("app.services.key_pool.get_app_settings", AsyncMock(return_value={}))
    monkeypatch.setattr(
        moments_mod,
        "with_llm_fallback",
        AsyncMock(
            return_value=[
                {"t_sec": 10, "caption": "First step", "quote": "Step one explain", "why": "Key point"}
            ]
        ),
    )

    session = _make_session()
    await moments_mod.build_key_moments(session, mat)

    assert stray.exists() and stray.read_bytes() == b"stray-bytes"
    assert len(mat.key_moments) >= 1
    assert all(m["image_relpath"] is None for m in mat.key_moments)
    assert session.commit.await_count == 1


async def test_failed_frame_capture_preserves_existing_screenshots_and_raises(
    tmp_path, monkeypatch
):
    mat = _make_material()
    video = tmp_path / "video.mp4"
    video.write_bytes(b"fake-mp4")
    mat.file_path = str(video)

    out_dir = tmp_path / "materials" / mat.id / "moments"
    out_dir.mkdir(parents=True)
    img1 = out_dir / "moment_01_10.jpg"
    img1.write_bytes(b"precious-existing-frame")
    existing_moments = [
        {"t_sec": 10.0, "caption": "Existing", "image_relpath": f"materials/{mat.id}/moments/moment_01_10.jpg"}
    ]
    mat.key_moments = list(existing_moments)

    monkeypatch.setattr(moments_mod, "_moments_dir", lambda _mid: out_dir)
    monkeypatch.setattr(moments_mod, "_atlas_dir", lambda _mid: tmp_path / "atlas")
    monkeypatch.setattr(moments_mod, "probe_duration_seconds", AsyncMock(return_value=120.0))
    monkeypatch.setattr(
        moments_mod,
        "_ensure_timed_segments",
        AsyncMock(return_value=[{"start": 10.0, "text": "Step one explain"}]),
    )
    monkeypatch.setattr(moments_mod, "load_atlas_for_material", lambda **_kw: [])
    monkeypatch.setattr(moments_mod, "build_screen_atlas", AsyncMock(return_value=[]))
    monkeypatch.setattr(moments_mod, "extract_best_frame_near", AsyncMock(return_value=(False, 10.0)))
    monkeypatch.setattr(moments_mod, "extract_keyframes", AsyncMock(return_value=[]))
    monkeypatch.setattr("app.services.key_pool.get_app_settings", AsyncMock(return_value={}))
    monkeypatch.setattr(
        moments_mod,
        "with_llm_fallback",
        AsyncMock(
            return_value=[
                {"t_sec": 10, "caption": "First step", "quote": "Step one", "why": "Useful"}
            ]
        ),
    )

    session = _make_session()
    with pytest.raises(RuntimeError, match="не удалось вытащить кадры из видео"):
        await moments_mod.build_key_moments(session, mat, video_path=str(video))

    assert img1.exists() and img1.read_bytes() == b"precious-existing-frame"
    assert mat.key_moments == existing_moments
    assert session.commit.await_count == 0
    assert not list(out_dir.parent.glob("moments_staging_*"))


async def test_successful_rebuild_atomically_replaces_and_prunes_obsolete_screenshots(
    tmp_path, monkeypatch
):
    mat = _make_material()
    video = tmp_path / "video.mp4"
    video.write_bytes(b"fake-mp4")
    mat.file_path = str(video)

    out_dir = tmp_path / "materials" / mat.id / "moments"
    out_dir.mkdir(parents=True)
    (out_dir / "moment_00_11.jpg").write_bytes(b"old-1")
    (out_dir / "moment_01_46.jpg").write_bytes(b"old-2")
    (out_dir / "moment_99_obsolete.jpg").write_bytes(b"obsolete-3")

    async def _fake_extract_best(_vpath, t_sec, out_path, **_kw):
        p = Path(out_path)
        p.write_bytes(f"new-{p.name}".encode())
        return True, float(t_sec)

    monkeypatch.setattr(moments_mod, "_moments_dir", lambda _mid: out_dir)
    monkeypatch.setattr(moments_mod, "_atlas_dir", lambda _mid: tmp_path / "atlas")
    monkeypatch.setattr(moments_mod, "probe_duration_seconds", AsyncMock(return_value=120.0))
    monkeypatch.setattr(
        moments_mod,
        "_ensure_timed_segments",
        AsyncMock(
            return_value=[
                {"start": 10.0, "text": "Step one explain"},
                {"start": 45.0, "text": "Step two explain"},
            ]
        ),
    )
    monkeypatch.setattr(moments_mod, "load_atlas_for_material", lambda **_kw: [])
    monkeypatch.setattr(moments_mod, "build_screen_atlas", AsyncMock(return_value=[]))
    monkeypatch.setattr(moments_mod, "extract_best_frame_near", _fake_extract_best)
    monkeypatch.setattr("app.services.local_ocr.ocr_image_file", lambda _p: "")
    monkeypatch.setattr("app.services.key_pool.get_app_settings", AsyncMock(return_value={}))
    monkeypatch.setattr(
        moments_mod,
        "with_llm_fallback",
        AsyncMock(
            return_value=[
                {"t_sec": 10, "caption": "First step", "quote": "Step one explain", "why": "Why 1"},
                {"t_sec": 45, "caption": "Second step", "quote": "Step two explain", "why": "Why 2"},
            ]
        ),
    )

    session = _make_session()
    await moments_mod.build_key_moments(session, mat, video_path=str(video))

    assert len(mat.key_moments) == 2
    saved_names = {Path(m["image_relpath"]).name for m in mat.key_moments}
    assert saved_names == {"moment_00_11.jpg", "moment_01_46.jpg"}
    assert (out_dir / "moment_00_11.jpg").read_bytes() == b"new-moment_00_11.jpg"
    assert (out_dir / "moment_01_46.jpg").read_bytes() == b"new-moment_01_46.jpg"
    assert not (out_dir / "moment_99_obsolete.jpg").exists()
    assert session.commit.await_count == 1
    assert not list(out_dir.parent.glob("moments_staging_*"))
