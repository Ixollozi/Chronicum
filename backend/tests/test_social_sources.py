import json

from app.pipelines.instagram import collect_instagram_assets, parse_instagram_html_assets
from app.pipelines.tiktok import parse_tiktok_image_urls, ytdlp_tiktok_url
from app.services.sources import classify_url
from app.services.storage import url_sha256


def test_classify_tiktok():
    assert classify_url("https://www.tiktok.com/@u/video/123") == "tiktok"
    assert classify_url("https://www.tiktok.com/@u/photo/123") == "tiktok"


def test_tiktok_video_hash():
    a = url_sha256("https://www.tiktok.com/@u/video/1234567890")
    b = url_sha256("https://www.tiktok.com/@other/video/1234567890?lang=en")
    assert a == b
    c = url_sha256("https://www.tiktok.com/@u/photo/1234567890")
    assert a == c


def test_tiktok_photo_url_for_ytdlp():
    assert (
        ytdlp_tiktok_url("https://www.tiktok.com/@u/photo/123?lang=en")
        == "https://www.tiktok.com/@u/video/123?lang=en"
    )


def test_parse_tiktok_image_post():
    html = r'{"imagePost":{"images":[{"imageURL":{"urlList":["https://cdn.example/a.jpg"]}},{"imageURL":{"urlList":["https://cdn.example/b.jpg"]}}]},"foo":1}'
    urls = parse_tiktok_image_urls(html)
    assert urls == ["https://cdn.example/a.jpg", "https://cdn.example/b.jpg"]


def test_parse_tiktok_universal_data():
    payload = {
        "__DEFAULT_SCOPE__": {
            "webapp.video-detail": {
                "itemInfo": {
                    "itemStruct": {
                        "imagePost": {
                            "images": [
                                {"imageURL": {"urlList": ["https://cdn.example/lo.jpg", "https://cdn.example/hi.jpg"]}}
                            ]
                        }
                    }
                }
            }
        }
    }
    html = f'<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">{json.dumps(payload)}</script>'
    assert parse_tiktok_image_urls(html) == ["https://cdn.example/hi.jpg"]


def test_instagram_sidecar_assets():
    media = {
        "edge_sidecar_to_children": {
            "edges": [
                {"node": {"is_video": False, "display_url": "https://ig/a.jpg"}},
                {"node": {"is_video": True, "video_url": "https://ig/b.mp4"}},
            ]
        }
    }
    assets = collect_instagram_assets(media)
    assert assets == [("image", "https://ig/a.jpg"), ("video", "https://ig/b.mp4")]


def test_parse_instagram_html_sidecar():
    html = (
        '"edge_sidecar_to_children":{"edges":['
        '{"node":{"is_video":false,"display_url":"https://ig/a.jpg"}},'
        '{"node":{"is_video":false,"display_url":"https://ig/b.jpg"}}'
        "]}"
    )
    assert parse_instagram_html_assets(html) == [
        ("image", "https://ig/a.jpg"),
        ("image", "https://ig/b.jpg"),
    ]


def test_parse_instagram_html_carousel_media():
    html = '"carousel_media":[{"display_url":"https://ig/1.jpg"},{"video_url":"https://ig/2.mp4","is_video":true}]'
    assert parse_instagram_html_assets(html) == [
        ("image", "https://ig/1.jpg"),
        ("video", "https://ig/2.mp4"),
    ]


def test_ytdlp_cookie_lock_retries_without_browser_cookies(monkeypatch):
    """Chrome cookie DB lock must degrade to a second attempt without cookiesfrombrowser."""
    from app.pipelines import youtube as yt

    calls: list[dict] = []

    class FakeYDL:
        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def extract_info(self, url, download=True):
            calls.append(dict(self.opts))
            if self.opts.get("cookiesfrombrowser"):
                raise PermissionError("Could not copy Chrome cookie database: Permission denied")
            return {"id": "ok", "ext": "mp4", "title": "ok"}

        def prepare_filename(self, info):
            return "/tmp/ok.mp4"

    class FakeYtDlp:
        YoutubeDL = FakeYDL

    monkeypatch.setitem(__import__("sys").modules, "yt_dlp", FakeYtDlp)
    monkeypatch.setattr(yt, "_ffmpeg_available", lambda: False)

    path, info = yt._ytdlp_run(
        "https://www.instagram.com/reel/x/",
        {
            "outtmpl": "/tmp/%(id)s.%(ext)s",
            "format": "b",
            "quiet": True,
            "cookiesfrombrowser": ("chrome",),
        },
    )
    assert path.endswith("ok.mp4")
    assert info and info.get("id") == "ok"
    assert len(calls) == 2
    assert "cookiesfrombrowser" in calls[0]
    assert "cookiesfrombrowser" not in calls[1]


def test_cookie_lock_error_detection():
    from app.pipelines.youtube import _cookie_lock_error

    assert _cookie_lock_error("Could not copy Chrome cookie database: Permission denied")
    assert _cookie_lock_error("database is locked")
    assert not _cookie_lock_error("HTTP Error 404: Not Found")
