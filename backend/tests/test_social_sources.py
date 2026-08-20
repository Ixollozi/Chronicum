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
