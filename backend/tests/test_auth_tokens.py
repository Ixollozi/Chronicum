from app.services.auth import (
    decode_session_jwt,
    hash_api_token,
    issue_session_jwt,
    mint_api_token_raw,
)
from app.models import User


def test_pat_hash_stable():
    raw = mint_api_token_raw()
    assert raw.startswith("chr_")
    assert hash_api_token(raw) == hash_api_token(raw)
    assert hash_api_token(raw) != hash_api_token(raw + "x")


def test_session_jwt_roundtrip():
    user = User(
        id="11111111-1111-1111-1111-111111111111",
        google_sub="sub",
        email="a@b.c",
        name="Ada",
        picture_url=None,
    )
    token = issue_session_jwt(user)
    payload = decode_session_jwt(token)
    assert payload is not None
    assert payload["sub"] == user.id
    assert payload["email"] == "a@b.c"
    assert decode_session_jwt("not-a-jwt") is None
