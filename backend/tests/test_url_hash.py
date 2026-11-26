from app.services.storage import url_sha256


def test_youtube_urls_share_hash():
    a = url_sha256("https://www.youtube.com/watch?v=dQw4w9wgGcQ&t=12")
    b = url_sha256("https://youtu.be/dQw4w9wgGcQ")
    c = url_sha256("https://www.youtube.com/shorts/dQw4w9wgGcQ")
    assert a == b == c


def test_instagram_urls_share_hash():
    a = url_sha256("https://www.instagram.com/reel/AbCdEfGhIjK/?igsh=abc")
    b = url_sha256("https://www.instagram.com/p/AbCdEfGhIjK/")
    assert a == b


def test_article_urls_not_collapsed():
    assert url_sha256("https://example.com/a") != url_sha256("https://example.com/b")
