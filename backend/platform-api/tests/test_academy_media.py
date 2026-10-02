"""Academy v2 media: upload limits, chunks on disk, links in the nginx secure_link format."""

from __future__ import annotations

import base64
import hashlib

import pytest

from app.academy.media import (
    CHUNK_SIZE,
    MediaError,
    MediaStore,
    check_upload,
    chunk_length,
    chunks_total,
    image_signature_ok,
    media_key,
    sign,
    signed_url,
    verify,
)
from app.settings import get_settings

SECRET = "s" * 40
KEY = "whieda/academy/0f8fad5b-d9cb-469f-a165-70867728950e/720.mp4"


@pytest.fixture
def media_env(monkeypatch, tmp_path):
    monkeypatch.setenv("PLATFORM_MEDIA_SIGNING_SECRET", SECRET)
    monkeypatch.setenv("PLATFORM_ACADEMY_MEDIA_DIR", str(tmp_path))
    monkeypatch.delenv("PLATFORM_ACADEMY_MEDIA_VIA_API", raising=False)
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


# ---- what may be uploaded ------------------------------------------------------------


def test_limits_per_kind():
    assert check_upload("video", "video/mp4", 4 * 1024**3, "урок.mp4") == "mp4"
    assert check_upload("video", "video/quicktime", 10, "IMG_0001.MOV") == "mov"
    assert check_upload("file", "application/pdf", 200 * 1024**2, "Рабочая тетрадь.pdf") == "pdf"
    assert check_upload("image", "image/jpeg", 20 * 1024**2, "фото.jpg") == "jpg"
    for kind, mime, size in (
        ("video", "video/mp4", 4 * 1024**3 + 1),
        ("file", "application/pdf", 200 * 1024**2 + 1),
        ("image", "image/png", 20 * 1024**2 + 1),
    ):
        with pytest.raises(MediaError) as too_big:
            check_upload(kind, mime, size, "x")
        assert too_big.value.code == "too_large"


@pytest.mark.parametrize(
    ("kind", "mime"),
    [("image", "image/svg+xml"), ("file", "text/html"), ("file", "application/x-msdownload"),
     ("video", "image/png"), ("audio", "audio/mpeg")],
)
def test_mime_whitelist(kind, mime):
    with pytest.raises(MediaError) as refused:
        check_upload(kind, mime, 10, "x")
    assert refused.value.code in {"bad_mime", "bad_kind"}


def test_empty_or_odd_names_and_sizes_are_refused():
    for name, size in (("", 10), ("a" * 300, 10), ("x.pdf", 0), ("x.pdf", -5)):
        with pytest.raises(MediaError):
            check_upload("file", "application/pdf", size, name)


def test_chunk_math():
    assert chunks_total(1) == 1
    assert chunks_total(CHUNK_SIZE) == 1
    assert chunks_total(CHUNK_SIZE + 1) == 2
    size = 2 * CHUNK_SIZE + 7
    assert [chunk_length(size, n) for n in range(3)] == [CHUNK_SIZE, CHUNK_SIZE, 7]
    for bad in (-1, 3):
        with pytest.raises(MediaError):
            chunk_length(size, bad)


def test_image_signatures():
    assert image_signature_ok("image/png", b"\x89PNG\r\n\x1a\n....")
    assert image_signature_ok("image/jpeg", b"\xff\xd8\xff\xe0")
    assert image_signature_ok("image/webp", b"RIFF\x00\x00\x00\x00WEBPVP8 ")
    assert image_signature_ok("image/gif", b"GIF89a....")
    assert not image_signature_ok("image/png", b"<html><script>")
    assert not image_signature_ok("image/jpeg", b"\x89PNG\r\n\x1a\n")


# ---- signed links ---------------------------------------------------------------------------


def nginx_secure_link(uri: str, expires: int, user: int, secret: str) -> str:
    """What nginx computes for secure_link_md5 "$secure_link_expires$uri$arg_u <secret>"."""
    digest = hashlib.md5(f"{expires}{uri}{user} {secret}".encode()).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def test_signature_is_the_nginx_secure_link_md5():
    assert sign(KEY, 7001, 1_900_000_000, SECRET) == nginx_secure_link("/academy-media/" + KEY, 1_900_000_000, 7001, SECRET)


def test_signed_url_points_to_nginx_and_expires_in_an_hour(media_env):
    url = signed_url(KEY, 7001, now=1_800_000_000)
    assert url == (
        f"/academy-media/{KEY}?u=7001&e=1800003600&s="
        + nginx_secure_link("/academy-media/" + KEY, 1_800_003_600, 7001, SECRET)
    )
    assert verify(KEY, "7001", "1800003600", url.rsplit("s=", 1)[1], now=1_800_003_600)
    assert not verify(KEY, "7001", "1800003600", url.rsplit("s=", 1)[1], now=1_800_003_601)  # истекла
    assert not verify(KEY, "7002", "1800003600", url.rsplit("s=", 1)[1], now=1_800_000_000)  # чужой ученик
    assert not verify(KEY, "7001", "1900003600", url.rsplit("s=", 1)[1], now=1_800_000_000)  # продлил сам
    assert not verify(KEY.replace("720", "orig"), "7001", "1800003600", url.rsplit("s=", 1)[1], now=1_800_000_000)
    assert not verify(KEY, "7001", "soon", "x", now=1_800_000_000)


def test_signed_url_through_the_api_until_nginx_is_ready(media_env, monkeypatch):
    monkeypatch.setenv("PLATFORM_ACADEMY_MEDIA_VIA_API", "true")
    get_settings.cache_clear()
    url = signed_url(KEY, 7001, now=1_800_000_000)
    assert url.startswith(f"/api/v1/content-access/academy/media/files/{KEY}?u=7001&e=1800003600&s=")
    # Подпись та же: nginx и API проверяют одну и ту же строку.
    assert url.rsplit("s=", 1)[1] == nginx_secure_link("/academy-media/" + KEY, 1_800_003_600, 7001, SECRET)


def test_no_secret_no_links(monkeypatch):
    monkeypatch.delenv("PLATFORM_MEDIA_SIGNING_SECRET", raising=False)
    get_settings.cache_clear()
    try:
        with pytest.raises(MediaError) as unavailable:
            signed_url(KEY, 7001)
        assert unavailable.value.code == "media_unavailable"
        monkeypatch.setenv("PLATFORM_MEDIA_SIGNING_SECRET", "short")
        get_settings.cache_clear()
        with pytest.raises(MediaError):
            signed_url(KEY, 7001)
    finally:
        get_settings.cache_clear()


# ---- chunks on disk --------------------------------------------------------------------------


def test_chunks_arrive_in_any_order_and_assemble(tmp_path):
    store = MediaStore(tmp_path)
    media_id = "0f8fad5b-d9cb-469f-a165-70867728950e"
    size = 2 * 8 + 3
    data = bytes(range(size))
    parts = {0: data[0:8], 1: data[8:16], 2: data[16:]}
    store.write_chunk("whieda", media_id, 2, 16, parts[2])
    store.write_chunk("whieda", media_id, 0, 0, parts[0])
    assert store.received("whieda", media_id) == [0, 2]
    store.write_chunk("whieda", media_id, 1, 8, parts[1])
    store.write_chunk("whieda", media_id, 1, 8, parts[1])  # повтор после обрыва — безопасен
    assert store.received("whieda", media_id) == [0, 1, 2]
    final = media_key("whieda", media_id, "original.pdf")
    path = store.assemble("whieda", media_id, final, size)
    assert path.read_bytes() == data
    assert store.received("whieda", media_id) == []
    assert store.path(final) == path
    with pytest.raises(ValueError):
        store.path("../etc/passwd")


def test_assemble_refuses_a_short_file(tmp_path):
    store = MediaStore(tmp_path)
    media_id = "0f8fad5b-d9cb-469f-a165-70867728950e"
    store.write_chunk("whieda", media_id, 0, 0, b"abc")
    with pytest.raises(MediaError) as short:
        store.assemble("whieda", media_id, media_key("whieda", media_id, "original.pdf"), 10)
    assert short.value.code == "upload_incomplete"
