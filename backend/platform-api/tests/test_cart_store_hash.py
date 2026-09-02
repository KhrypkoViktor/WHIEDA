"""Snapshot tokens are stored hashed, not as the public token."""

from app.cart.store import _hash_token


def test_snapshot_token_hash_is_opaque_sha256():
    token = "public-snap-token"
    digest = _hash_token(token)
    assert len(digest) == 64
    assert digest == _hash_token(token)
    assert digest != _hash_token("other-token")
    assert token not in digest
    assert "snap" not in digest
