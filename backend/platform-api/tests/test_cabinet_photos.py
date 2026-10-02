"""Фото профиля из кабинета: до 1200 px, JPEG, без EXIF/GPS, поворот с телефона учтён."""

from __future__ import annotations

from io import BytesIO

import pytest
from PIL import Image

from app.cabinet.photos import HEIF_SUPPORTED, MAX_UPLOAD_BYTES, PhotoError, process_profile_photo


def _jpeg(size=(3000, 2000), *, orientation: int | None = None, gps: bool = False, xmp: bytes | None = None) -> bytes:
    image = Image.new("RGB", size, (200, 30, 30))
    exif = Image.Exif()
    exif[0x010F] = "PhoneMaker"  # Make
    exif[0x0110] = "Phone 15"  # Model
    if orientation:
        exif[0x0112] = orientation
    if gps:
        exif[0x8825] = {1: "N", 2: (55.0, 45.0, 0.0), 3: "E", 4: (37.0, 37.0, 0.0)}
    out = BytesIO()
    kwargs = {"format": "JPEG", "exif": exif.tobytes(), "quality": 90}
    if xmp is not None:
        kwargs["xmp"] = xmp
    image.save(out, **kwargs)
    return out.getvalue()


def _open(body: bytes) -> Image.Image:
    image = Image.open(BytesIO(body))
    image.load()
    return image


def test_big_phone_jpeg_is_shrunk_to_1200_and_loses_exif_and_gps():
    source = _jpeg(gps=True, xmp=b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"/></x:xmpmeta>')
    assert b"Exif" in source and b"PhoneMaker" in source and b"xmpmeta" in source
    photo = process_profile_photo(source)
    image = _open(photo.body)
    assert image.format == "JPEG" and image.size == (1200, 800) == (photo.width, photo.height)
    assert not image.getexif()
    for marker in (b"Exif", b"PhoneMaker", b"Phone 15", b"adobe:ns:meta", b"xmpmeta"):
        assert marker not in photo.body, marker
    assert len(photo.sha256) == 64 and len(photo.body) < len(source)


def test_portrait_from_phone_is_turned_upright_before_exif_is_dropped():
    # Камера пишет кадр боком 1600×1200 и флаг «повернуть на 90°» (Orientation 6).
    photo = process_profile_photo(_jpeg((1600, 1200), orientation=6))
    assert (photo.width, photo.height) == (900, 1200)


def test_small_photo_is_not_upscaled_and_tiny_one_is_refused():
    photo = process_profile_photo(_jpeg((640, 480)))
    assert (photo.width, photo.height) == (640, 480)
    with pytest.raises(PhotoError) as tiny:
        process_profile_photo(_jpeg((120, 120)))
    assert (tiny.value.code, tiny.value.status) == ("photo_too_small", 400)


def test_transparent_png_gets_a_white_background():
    image = Image.new("RGBA", (400, 400), (0, 0, 0, 0))
    image.paste((10, 120, 10, 255), (100, 100, 300, 300))
    out = BytesIO()
    image.save(out, format="PNG")
    result = _open(process_profile_photo(out.getvalue()).body)
    assert result.mode == "RGB"
    corner = result.getpixel((5, 5))
    assert all(channel > 245 for channel in corner)


@pytest.mark.skipif(not HEIF_SUPPORTED, reason="pillow-heif is not installed")
def test_heic_from_iphone_becomes_jpeg():
    out = BytesIO()
    Image.new("RGB", (2400, 1800), (20, 20, 160)).save(out, format="HEIF")
    photo = process_profile_photo(out.getvalue())
    assert _open(photo.body).format == "JPEG" and (photo.width, photo.height) == (1200, 900)


@pytest.mark.parametrize(
    ("data", "code", "status"),
    [
        (b"", "photo_empty", 400),
        (b"this is not an image at all", "photo_unreadable", 422),
        (b"\xff\xd8\xff\xe0" + b"\x00" * 64, "photo_unreadable", 422),
    ],
)
def test_broken_uploads_are_refused(data, code, status):
    with pytest.raises(PhotoError) as caught:
        process_profile_photo(data)
    assert (caught.value.code, caught.value.status) == (code, status)


def test_gif_is_not_a_profile_photo_and_20mb_is_the_limit():
    out = BytesIO()
    Image.new("P", (300, 300)).save(out, format="GIF")
    with pytest.raises(PhotoError) as gif:
        process_profile_photo(out.getvalue())
    assert (gif.value.code, gif.value.status) == ("photo_type_unsupported", 415)
    with pytest.raises(PhotoError) as big:
        process_profile_photo(b"\xff" * (MAX_UPLOAD_BYTES + 1))
    assert (big.value.code, big.value.status) == ("photo_too_large", 413)
