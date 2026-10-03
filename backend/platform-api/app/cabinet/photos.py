"""Фото профиля из кабинета: проверка, поворот по EXIF, до 1200 px, JPEG без метаданных.

Телефон присылает JPEG, PNG, HEIC (iPhone) или WebP до 20 МБ. Сохраняем один
вид: JPEG, длинная сторона не больше 1200 px, без EXIF/XMP (там бывают
координаты и модель телефона). Поворот из EXIF применяется до удаления, иначе
портрет с телефона лёг бы на бок.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError

try:  # HEIC с iPhone; в образе пакет есть (pyproject), локально может не быть.
    from pillow_heif import register_heif_opener

    register_heif_opener()
    HEIF_SUPPORTED = True
except ImportError:  # pragma: no cover - depends on the environment
    HEIF_SUPPORTED = False

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_SIDE = 1200
MIN_SIDE = 160
# JPEG декодируется сразу уменьшенным (draft): 60 Мп — больше любой камеры телефона.
MAX_PIXELS = 60_000_000
# PNG / WebP / HEIC декодируются целиком (RGBA — 4 байта на точку): не больше ~100 МБ
# на картинку. PNG в 1 МБ может разжиматься в гигабайт (ревью 02.10.2026).
MAX_PIXELS_FULL_DECODE = 25_000_000
JPEG_QUALITY = 85
ALLOWED_FORMATS = frozenset({"JPEG", "MPO", "PNG", "WEBP", "HEIF", "HEIC"})
# Какие декодеры Pillow вообще пробует: остальные форматы до разбора не доходят.
# MPO (снимок iPhone «две картинки») открывает декодер JPEG — отдельного у Pillow нет.
DECODERS = ("JPEG", "PNG", "WEBP") + (("HEIF",) if HEIF_SUPPORTED else ())
_DRAFT_FORMATS = frozenset({"JPEG", "MPO"})


class PhotoError(ValueError):
    def __init__(self, code: str, status: int) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


@dataclass(frozen=True)
class ProcessedPhoto:
    body: bytes
    width: int
    height: int
    sha256: str


def _flatten(image: Image.Image) -> Image.Image:
    """RGB на белом фоне: прозрачный PNG не должен стать чёрным квадратом."""
    if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
        rgba = image.convert("RGBA")
        background = Image.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.getchannel("A"))
        return background
    return image.convert("RGB") if image.mode != "RGB" else image


def process_profile_photo(data: bytes) -> ProcessedPhoto:
    if not data:
        raise PhotoError("photo_empty", 400)
    if len(data) > MAX_UPLOAD_BYTES:
        raise PhotoError("photo_too_large", 413)
    try:
        with Image.open(BytesIO(data), formats=DECODERS) as source:
            fmt = str(source.format or "").upper()
            if fmt not in ALLOWED_FORMATS:
                raise PhotoError("photo_type_unsupported", 415)
            width, height = source.size
            if width * height > (MAX_PIXELS if fmt in _DRAFT_FORMATS else MAX_PIXELS_FULL_DECODE):
                raise PhotoError("photo_too_large", 413)
            if fmt in _DRAFT_FORMATS:
                source.draft("RGB", (MAX_SIDE, MAX_SIDE))  # уменьшение уже при декодировании
            # Сначала уменьшаем (в памяти одна маленькая копия), потом поворот и фон.
            source.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)
            image = ImageOps.exif_transpose(source)
            image = _flatten(image)
            if min(image.size) < MIN_SIDE:
                raise PhotoError("photo_too_small", 400)
            # Новая картинка без info: в файл не попадут EXIF, XMP, GPS, ICC.
            clean = Image.new("RGB", image.size)
            clean.paste(image)
            out = BytesIO()
            clean.save(out, format="JPEG", quality=JPEG_QUALITY, optimize=True, progressive=True)
    except PhotoError:
        raise
    except UnidentifiedImageError as exc:
        raise PhotoError("photo_type_unsupported", 415) from exc
    except Exception as exc:  # битый файл от телефона — 422, а не 500 с трейсом
        raise PhotoError("photo_unreadable", 422) from exc
    body = out.getvalue()
    return ProcessedPhoto(
        body=body,
        width=clean.size[0],
        height=clean.size[1],
        sha256=hashlib.sha256(body).hexdigest(),
    )
