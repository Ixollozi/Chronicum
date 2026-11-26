"""Tests for P2.6 (security hardening) and P2.7 (Netscape cookies management)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException

from app.api.deps import require_web_token
from app.bot.main import _is_owner_user
from app.config import Settings, get_settings
from app.services import cookies, secrets


VALID_NETSCAPE_COOKIES = """# Netscape HTTP Cookie File
#HttpOnly_.instagram.com\tTRUE\t/\tTRUE\t1893456000\tsessionid\tsecret_ig_value
.tiktok.com\tTRUE\t/\tTRUE\t1893456000\tsessionid_ss\tsecret_tt_value
.youtube.com\tTRUE\t/\tFALSE\t1893456000\tPREF\tvalue123
"""


def test_secrets_dedicated_key_and_rotation_fallback(monkeypatch):
    # Encrypt under legacy WEB_API_TOKEN first
    monkeypatch.setenv("WEB_API_TOKEN", "legacy-web-token-123")
    monkeypatch.delenv("SECRETS_ENCRYPTION_KEY", raising=False)
    get_settings.cache_clear()
    ct_legacy = secrets.encrypt_secret("gsk_secret_value_1234567890")
    assert secrets.decrypt_secret(ct_legacy) == "gsk_secret_value_1234567890"

    # Now enable dedicated SECRETS_ENCRYPTION_KEY (native Fernet key)
    fernet_key = Fernet.generate_key().decode("ascii")
    monkeypatch.setenv("SECRETS_ENCRYPTION_KEY", fernet_key)
    get_settings.cache_clear()

    # Existing legacy ciphertext still decrypts seamlessly via fallback!
    assert secrets.decrypt_secret(ct_legacy) == "gsk_secret_value_1234567890"

    # New ciphertexts use dedicated SECRETS_ENCRYPTION_KEY and survive WEB_API_TOKEN rotation
    ct_new = secrets.encrypt_secret("AIzaNewSecretKey987654321")
    monkeypatch.setenv("WEB_API_TOKEN", "completely-rotated-web-token")
    get_settings.cache_clear()
    assert secrets.decrypt_secret(ct_new) == "AIzaNewSecretKey987654321"
    get_settings.cache_clear()


def test_security_posture_validation_and_warnings():
    # Hardened mode rejects default token
    bad = Settings(
        _env_file=None,
        allow_placeholder_secrets=False,
        web_api_token="chronicum-dev-token",
    )
    with pytest.raises(RuntimeError, match="WEB_API_TOKEN"):
        bad.validate_security_posture()
    warnings = bad.security_warnings()
    assert any("WEB_API_TOKEN" in w for w in warnings)
    assert any("SECRETS_ENCRYPTION_KEY" in w for w in warnings)

    # Hardened mode with strong token and dedicated key passes cleanly
    good = Settings(
        _env_file=None,
        allow_placeholder_secrets=False,
        web_api_token="super-strong-random-token-42",
        secrets_encryption_key="dedicated-encryption-passphrase",
        allow_query_token=False,
        telegram_owner_id=123456789,
    )
    good.validate_security_posture()
    assert good.security_warnings() == []


@pytest.mark.asyncio
async def test_require_web_token_query_param_toggle_and_fail_closed(monkeypatch):
    monkeypatch.setenv("WEB_API_TOKEN", "strong-token-abc")
    monkeypatch.setenv("ALLOW_QUERY_TOKEN", "false")
    monkeypatch.setenv("ALLOW_PLACEHOLDER_SECRETS", "false")
    get_settings.cache_clear()

    # Header works
    await require_web_token(authorization="Bearer strong-token-abc", token=None)

    # Query param is rejected when ALLOW_QUERY_TOKEN=false
    with pytest.raises(HTTPException) as exc_info:
        await require_web_token(authorization=None, token="strong-token-abc")
    assert exc_info.value.status_code == 401

    # Placeholder token fails closed with 503 when ALLOW_PLACEHOLDER_SECRETS=false
    monkeypatch.setenv("WEB_API_TOKEN", "chronicum-dev-token")
    get_settings.cache_clear()
    with pytest.raises(HTTPException) as exc_503:
        await require_web_token(authorization="Bearer chronicum-dev-token", token=None)
    assert exc_503.value.status_code == 503
    get_settings.cache_clear()


def test_telegram_owner_guard_fail_closed_in_hardened_mode(monkeypatch):
    # Hardened mode + owner_id=0 + allow_public=False => fail closed
    monkeypatch.setenv("ALLOW_PLACEHOLDER_SECRETS", "false")
    monkeypatch.setenv("TELEGRAM_OWNER_ID", "0")
    monkeypatch.setenv("TELEGRAM_ALLOW_PUBLIC", "false")
    get_settings.cache_clear()
    assert _is_owner_user(999) is False

    # Explicit opt-in allows public access when owner_id=0
    monkeypatch.setenv("TELEGRAM_ALLOW_PUBLIC", "true")
    get_settings.cache_clear()
    assert _is_owner_user(999) is True

    # Specific owner_id allows only that user
    monkeypatch.setenv("TELEGRAM_OWNER_ID", "777")
    monkeypatch.setenv("TELEGRAM_ALLOW_PUBLIC", "false")
    get_settings.cache_clear()
    assert _is_owner_user(777) is True
    assert _is_owner_user(888) is False
    assert _is_owner_user(None) is False
    get_settings.cache_clear()


def test_cookies_validation_save_inspect_precedence_and_delete(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "files"))
    monkeypatch.delenv("MEDIA_COOKIES_FILE", raising=False)
    monkeypatch.delenv("MEDIA_COOKIES_FROM_BROWSER", raising=False)
    get_settings.cache_clear()

    # Rejects empty and non-Netscape garbage
    with pytest.raises(ValueError):
        cookies.validate_netscape_cookies("")
    with pytest.raises(ValueError):
        cookies.validate_netscape_cookies("just some random text without tabs")

    # Initial status is unconfigured and writable
    init_status = cookies.inspect_cookies_file()
    assert init_status.configured is False
    assert init_status.source == "none"
    assert init_status.writable is True

    # Save valid cookies -> stored in managed file with 0600 permissions
    saved = cookies.save_cookies_content(VALID_NETSCAPE_COOKIES)
    assert saved.configured is True
    assert saved.source == "managed"
    assert saved.entries == 3
    assert saved.platforms == {"instagram": True, "tiktok": True, "youtube": True}
    assert saved.path is not None
    assert Path(saved.path).is_file()
    assert cookies.resolve_cookies_file() == saved.path
    # Sensitive cookie values are never leaked in inspection metadata
    assert "secret_ig_value" not in str(saved)

    # Env MEDIA_COOKIES_FILE takes precedence over managed file
    env_file = tmp_path / "env_cookies.txt"
    env_file.write_text(".instagram.com\tTRUE\t/\tTRUE\t1893456000\tsid\tx\n", encoding="utf-8")
    monkeypatch.setenv("MEDIA_COOKIES_FILE", str(env_file))
    get_settings.cache_clear()
    env_status = cookies.inspect_cookies_file()
    assert env_status.source == "env"
    assert env_status.writable is False
    assert env_status.platforms["instagram"] is True
    assert env_status.platforms["tiktok"] is False
    assert cookies.resolve_cookies_file() == str(env_file)

    # Clear env override and delete managed file
    monkeypatch.delenv("MEDIA_COOKIES_FILE", raising=False)
    get_settings.cache_clear()
    after_del = cookies.delete_cookies_file()
    assert after_del.configured is False
    assert after_del.source == "none"
    get_settings.cache_clear()
