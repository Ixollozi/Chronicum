from __future__ import annotations

import asyncio
import re
import shutil
import subprocess
import sys
import tempfile
from functools import lru_cache
from pathlib import Path

from app.logging_setup import get_logger

log = get_logger(__name__)


def win_no_window_kwargs() -> dict[str, int]:
    """Suppress console window flashes for child processes (ffmpeg/ffprobe) on Windows."""
    if sys.platform == "win32":
        flag = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        if flag:
            return {"creationflags": flag}
    return {}


@lru_cache
def ffmpeg_exe() -> str:
    """System ffmpeg, or bundled binary from imageio-ffmpeg."""
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg

        path = imageio_ffmpeg.get_ffmpeg_exe()
        if path and Path(path).exists():
            log.info("using_bundled_ffmpeg", path=path)
            return path
    except Exception as e:
        log.warning("bundled_ffmpeg_unavailable", error=str(e)[:200])
    return "ffmpeg"


@lru_cache
def ffprobe_exe() -> str:
    found = shutil.which("ffprobe")
    if found:
        return found
    # imageio-ffmpeg ships ffmpeg only; probe via ffmpeg -i as fallback returns 0 duration
    return "ffprobe"


async def run_cmd(args: list[str], timeout: int = 600) -> subprocess.CompletedProcess[str]:
    def _run() -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
                **win_no_window_kwargs(),
            )
        except FileNotFoundError as e:
            tool = args[0] if args else "cmd"
            return subprocess.CompletedProcess(args, 127, "", f"{tool} not found: {e}")

    return await asyncio.to_thread(_run)


async def probe_duration_seconds(path: str) -> float:
    result = await run_cmd(
        [
            ffprobe_exe(),
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            path,
        ]
    )
    if result.returncode == 0:
        try:
            return float(result.stdout.strip())
        except ValueError:
            pass
    # ffmpeg -i fallback (handles audio-only files when ffprobe is unavailable)
    ff_res = await run_cmd([ffmpeg_exe(), "-i", path], timeout=60)
    m = re.search(r"Duration:\s*(\d{2,}):(\d{2}):(\d{2}(?:\.\d+)?)", ff_res.stderr or "")
    if m:
        try:
            return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
        except ValueError:
            pass
    # OpenCV fallback
    try:

        def _cv_dur() -> float:
            import cv2

            cap = cv2.VideoCapture(path)
            fps = float(cap.get(cv2.CAP_PROP_FPS) or 0)
            frames = float(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            cap.release()
            if fps > 0 and frames > 0:
                return frames / fps
            return 0.0

        return await asyncio.to_thread(_cv_dur)
    except Exception as e:
        log.warning("ffprobe_failed", stderr=(result.stderr or "")[:500], cv=str(e)[:120])
        return 0.0


async def extract_audio(video_path: str, out_path: str | None = None) -> str:
    out = out_path or str(Path(video_path).with_suffix(".mp3"))
    ff = ffmpeg_exe()
    result = await run_cmd([ff, "-y", "-i", video_path, "-vn", "-acodec", "libmp3lame", "-q:a", "4", out])
    if result.returncode != 0:
        err = (result.stderr or "")[:800]
        if "not found" in err.lower() or result.returncode == 127:
            raise RuntimeError(
                "ffmpeg не найден. Установи: winget install Gyan.FFmpeg "
                "или pip install imageio-ffmpeg."
            )
        raise RuntimeError(f"ffmpeg audio extract failed: {err}")
    return out


async def extract_audio_segment(video_path: str, start: float, duration: float, out_path: str) -> str:
    ff = ffmpeg_exe()
    result = await run_cmd(
        [
            ff,
            "-y",
            "-ss",
            str(start),
            "-t",
            str(duration),
            "-i",
            video_path,
            "-vn",
            "-acodec",
            "libmp3lame",
            "-q:a",
            "4",
            out_path,
        ]
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg segment failed: {(result.stderr or '')[:800]}")
    return out_path


async def split_audio_chunks(
    audio_path: str,
    out_dir: str,
    *,
    chunk_seconds: int = 600,
    bitrate: str = "64k",
) -> list[str]:
    """Split/transcode audio into 16kHz mono MP3 chunks sized for Whisper API limits."""
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    seg_time = max(5, int(chunk_seconds))
    pattern = str(Path(out_dir) / "chunk_%04d.mp3")
    ff = ffmpeg_exe()
    result = await run_cmd(
        [
            ff,
            "-y",
            "-i",
            audio_path,
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-acodec",
            "libmp3lame",
            "-b:a",
            bitrate,
            "-f",
            "segment",
            "-segment_time",
            str(seg_time),
            "-reset_timestamps",
            "1",
            pattern,
        ],
        timeout=900,
    )
    if result.returncode != 0:
        err = (result.stderr or "")[:800]
        if "not found" in err.lower() or result.returncode == 127:
            raise RuntimeError(
                "ffmpeg не найден — невозможно нарезать длинное аудио для Whisper. "
                "Установи: winget install Gyan.FFmpeg или pip install imageio-ffmpeg."
            )
        raise RuntimeError(f"ffmpeg audio chunk split failed: {err}")
    chunks = sorted(str(p) for p in Path(out_dir).glob("chunk_*.mp3") if p.stat().st_size > 0)
    if not chunks:
        raise RuntimeError("ffmpeg не создал ни одного аудио-чанка")
    return chunks


async def extract_frame_at(video_path: str, t_sec: float, out_path: str) -> bool:
    """Grab one frame via bundled/system ffmpeg, else OpenCV. Retries nearby timestamps."""
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    base = max(0.0, float(t_sec))
    candidates: list[float] = []
    for delta in (0.0, 0.2, -0.35, 0.55, -0.8, 1.2):
        t = max(0.0, base + delta)
        if not any(abs(t - x) < 0.05 for x in candidates):
            candidates.append(t)

    ff = ffmpeg_exe()

    def _file_ok(path: str) -> bool:
        p = Path(path)
        if not p.exists() or p.stat().st_size < 1500:
            return False
        try:
            import cv2
            import numpy as np

            img = cv2.imread(path)
            if img is None:
                return False
            # Reject near-black / near-white empty frames
            mean = float(np.mean(img))
            std = float(np.std(img))
            if mean < 8 and std < 6:
                return False
            if mean > 248 and std < 6:
                return False
            return True
        except Exception:
            return p.stat().st_size > 4000

    async def _ffmpeg(t: float) -> bool:
        # Fast seek first, then accurate
        for args in (
            [ff, "-y", "-ss", str(t), "-i", video_path, "-frames:v", "1", "-q:v", "2", out_path],
            [ff, "-y", "-i", video_path, "-ss", str(t), "-frames:v", "1", "-q:v", "2", out_path],
        ):
            result = await run_cmd(args, timeout=180)
            if result.returncode == 0 and await asyncio.to_thread(_file_ok, out_path):
                return True
            try:
                Path(out_path).unlink(missing_ok=True)
            except Exception:
                pass
        return False

    def _cv(t: float) -> bool:
        import cv2

        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            return False
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 25) or 25.0
        nframes = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        # Clamp to last readable frame
        max_t = max(0.0, (nframes - 2) / fps) if nframes > 2 else t
        t_use = min(t, max_t) if max_t > 0 else t

        cap.set(cv2.CAP_PROP_POS_MSEC, t_use * 1000.0)
        ok, frame = cap.read()
        if not ok or frame is None:
            idx = max(0, int(t_use * fps))
            if nframes > 0:
                idx = min(idx, nframes - 1)
            cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
            ok, frame = cap.read()
        # Walk forward a few frames if still blank/fail
        if (not ok or frame is None) and nframes > 0:
            for step in range(1, 8):
                cap.set(cv2.CAP_PROP_POS_FRAMES, min(nframes - 1, int(t_use * fps) + step))
                ok, frame = cap.read()
                if ok and frame is not None:
                    break
        cap.release()
        if not ok or frame is None:
            return False
        ok_w = bool(cv2.imwrite(out_path, frame, [int(cv2.IMWRITE_JPEG_QUALITY), 90]))
        return ok_w and _file_ok(out_path)

    for t in candidates:
        try:
            if await _ffmpeg(t):
                return True
        except Exception as e:
            log.warning("ffmpeg_frame_error", t=t, error=str(e)[:200])
        try:
            ok = await asyncio.to_thread(_cv, t)
            if ok:
                log.info("frame_via_opencv", path=out_path, t=t)
                return True
        except Exception as e:
            log.warning("opencv_frame_error", t=t, error=str(e)[:200])

    log.warning("frame_extract_failed", path=video_path, t=t_sec)
    return False


def score_frame_file(path: str, *, ocr_text: str | None = None) -> float:
    """Higher = more useful screenshot (detail + on-screen text)."""
    try:
        import cv2
        import numpy as np

        img = cv2.imread(path)
        if img is None:
            return 0.0
        h, w = img.shape[:2]
        if h < 40 or w < 40:
            return 0.0
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        sharp = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        mean = float(np.mean(gray))
        std = float(np.std(gray))
        if std < 12:
            return sharp * 0.01
        if mean < 12 or mean > 245:
            return 1.0

        text_bonus = 0.0
        try:
            raw_ocr = ocr_text
            if raw_ocr is None:
                from app.services.local_ocr import ocr_image_file

                raw_ocr = ocr_image_file(path)
            cleaned = re.sub(r"\s+", " ", (raw_ocr or "").strip())
            words = re.findall(r"[A-Za-zА-Яа-яЁё0-9]{2,}", cleaned)
            text_bonus = min(180.0, len(cleaned) * 0.8 + len(words) * 6.0)
        except Exception:
            edges = cv2.Canny(gray, 80, 160)
            text_bonus = float(np.mean(edges)) * 1.5

        return sharp * 0.05 + text_bonus + min(40.0, std)
    except Exception:
        try:
            return float(Path(path).stat().st_size) / 5000.0
        except Exception:
            return 0.0


_PTS_TIME_RE = re.compile(r"pts_time:([0-9]+(?:\.[0-9]+)?)")


async def detect_scene_timestamps(
    video_path: str,
    *,
    threshold: float = 0.28,
    max_scenes: int = 36,
    min_gap: float = 1.5,
    duration: float = 0.0,
) -> list[float]:
    """
    Detect visual scene/slide transitions via ffmpeg's `select='gt(scene,T)',showinfo`.
    Returns sorted timestamps in seconds, downsampled across the timeline if needed.
    """
    if not Path(video_path).exists():
        return []
    thr = max(0.05, min(0.95, float(threshold)))
    ff = ffmpeg_exe()
    vf = f"select='gt(scene,{thr:.2f})',showinfo"
    result = await run_cmd(
        [ff, "-hide_banner", "-i", video_path, "-vf", vf, "-an", "-f", "null", "-"],
        timeout=300,
    )
    if result.returncode != 0 and not result.stderr:
        return []

    raw_ts: list[float] = []
    for m in _PTS_TIME_RE.finditer(result.stderr or ""):
        try:
            t = float(m.group(1))
        except ValueError:
            continue
        if t <= 0.1:
            continue
        if duration > 0 and t >= max(0.0, duration - 0.2):
            continue
        if not raw_ts or (t - raw_ts[-1]) >= min_gap:
            raw_ts.append(round(t, 2))

    if len(raw_ts) <= max_scenes or max_scenes <= 0:
        return raw_ts

    # Uniformly downsample across full timeline so late UI transitions are kept
    step = (len(raw_ts) - 1) / float(max(1, max_scenes - 1))
    picked: list[float] = []
    for idx in range(max_scenes):
        t = raw_ts[min(len(raw_ts) - 1, int(round(idx * step)))]
        if not picked or t > picked[-1]:
            picked.append(t)
    return picked


async def extract_best_frame_near(
    video_path: str,
    t_sec: float,
    out_path: str,
    *,
    duration: float = 0.0,
    offsets: list[float] | None = None,
) -> tuple[bool, float]:
    """Try several nearby timestamps; keep the most informative frame. Returns (ok, chosen_t)."""
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    base = max(0.0, float(t_sec))
    if offsets is None:
        offsets = [0.6, 1.2, 0.0, 2.0, 2.8, -0.3, 3.8]
    max_t = max(0.0, duration - 0.35) if duration and duration > 0 else None

    best_score = -1.0
    best_t = base

    with tempfile.TemporaryDirectory(prefix="chronicum_frame_") as tmp:
        for off in offsets:
            t = base + off
            if t < 0:
                continue
            if max_t is not None:
                t = min(t, max_t)
            candidate = str(Path(tmp) / f"c_{abs(off):.2f}_{'p' if off >= 0 else 'm'}.jpg")
            ok = await extract_frame_at(video_path, t, candidate)
            if not ok:
                continue
            score = await asyncio.to_thread(score_frame_file, candidate)
            if score > best_score:
                best_score = score
                best_t = t
                Path(out_path).write_bytes(Path(candidate).read_bytes())

        if best_score < 0:
            t0 = base if max_t is None else min(base, max_t)
            ok = await extract_frame_at(video_path, t0, out_path)
            return ok, t0

    return Path(out_path).exists() and Path(out_path).stat().st_size > 0, best_t


async def extract_keyframes(
    video_path: str,
    out_dir: str,
    every_seconds: int = 15,
    max_frames: int = 40,
    duration_limit: float | None = None,
) -> list[str]:
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    pattern = str(Path(out_dir) / "frame_%04d.jpg")
    ff = ffmpeg_exe()
    cmd = [ff, "-y"]
    if duration_limit and duration_limit > 0:
        cmd.extend(["-t", str(duration_limit)])
    cmd.extend(
        [
            "-i",
            video_path,
            "-vf",
            f"fps=1/{every_seconds}",
            "-frames:v",
            str(max_frames),
            pattern,
        ]
    )
    result = await run_cmd(cmd)
    paths = sorted(str(p) for p in Path(out_dir).glob("frame_*.jpg"))
    if paths:
        return paths

    log.warning("keyframes_ffmpeg_failed", stderr=(result.stderr or "")[:500])
    # OpenCV uniform sampling
    def _cv_keys() -> list[str]:
        import cv2

        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            return []
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 25)
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        if duration_limit and duration_limit > 0 and fps > 0:
            total = min(total, int(fps * duration_limit)) if total > 0 else int(fps * duration_limit)
        step = max(1, int(fps * every_seconds))
        out: list[str] = []
        i = 0
        frame_i = 0
        while frame_i < total and len(out) < max_frames:
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_i)
            ok, frame = cap.read()
            if ok and frame is not None:
                fp = str(Path(out_dir) / f"frame_{i:04d}.jpg")
                if cv2.imwrite(fp, frame):
                    out.append(fp)
                    i += 1
            frame_i += step
        cap.release()
        return out

    try:
        return await asyncio.to_thread(_cv_keys)
    except Exception as e:
        log.warning("keyframes_opencv_failed", error=str(e)[:300])
        return []
