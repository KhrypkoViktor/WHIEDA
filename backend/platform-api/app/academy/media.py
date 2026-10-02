"""Academy v2 media: uploads on the Core disk and signed links to them.

Storage — ``partner_library.storage.LocalStorageBackend`` (safe keys, no escape from
the root) at ``PLATFORM_ACADEMY_MEDIA_DIR``. Keys never hold a user's file name:
``<tenant>/academy/<media_id>/original.<ext>``, video variants ``720.mp4`` and
``poster.jpg`` next to it.

Upload: ``init`` (kind, mime, size, name — limits and a mime whitelist) → chunks
``PUT …/chunks/<n>`` in any order, each written at its offset into ``upload.part``
with a marker ``chunks/<n>`` (a retried chunk is harmless; ``received`` tells the
site where to resume) → ``complete``: every chunk present and the size right,
``upload.part`` becomes the original. Images are checked by signature; video goes
to the transcoding queue (``app.academy.transcode``).

Links: nginx ``secure_link`` format, so nginx checks them without Core:
``/academy-media/<key>?u=<telegram_user_id>&e=<expires>&s=<md5>`` with
``s = base64url(md5("<e>/academy-media/<key><u> <secret>"))``. The link is bound to
the viewer (u) and lives ``PLATFORM_ACADEMY_MEDIA_URL_TTL_SECONDS`` (1 hour). Until
nginx serves the directory (``PLATFORM_ACADEMY_MEDIA_VIA_API=true``) the same
signature points to the Core API, which streams the file with Range support.
This is protection on the level of basic video hosting plans, not DRM.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import shutil
import time
from pathlib import Path
from typing import Any

from app.partner_library.storage import LocalStorageBackend, validate_storage_key
from app.settings import get_settings

CHUNK_SIZE = 5 * 1024 * 1024
SIZE_LIMITS = {"video": 4 * 1024**3, "file": 200 * 1024**2, "image": 20 * 1024**2}
MIME_TYPES: dict[str, dict[str, str]] = {
    "image": {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/gif": "gif"},
    "video": {
        "video/mp4": "mp4",
        "video/quicktime": "mov",
        "video/webm": "webm",
        "video/x-matroska": "mkv",
        "video/3gpp": "3gp",
        "video/x-m4v": "m4v",
        "video/x-msvideo": "avi",
        "video/mpeg": "mpg",
    },
    "file": {
        "application/pdf": "pdf",
        "application/zip": "zip",
        "application/x-zip-compressed": "zip",
        "text/plain": "txt",
        "application/msword": "doc",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
        "application/vnd.ms-excel": "xls",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
        "application/vnd.ms-powerpoint": "ppt",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
        "audio/mpeg": "mp3",
        "audio/mp4": "m4a",
        "audio/x-m4a": "m4a",
        "image/jpeg": "jpg",
        "image/png": "png",
        "image/webp": "webp",
    },
}
_IMAGE_SIGNATURES = {
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/gif": (b"GIF87a", b"GIF89a"),
}
PUBLIC_PREFIX = "/academy-media/"
API_FILES_PREFIX = "/api/v1/content-access/academy/media/files/"
MIN_SECRET_CHARS = 32


class MediaError(Exception):
    """A media request was refused; ``code`` is machine-readable."""

    def __init__(self, status: int, code: str, extra: dict[str, Any] | None = None) -> None:
        super().__init__(code)
        self.status = status
        self.code = code
        self.extra = dict(extra or {})


# ---- what may be uploaded ---------------------------------------------------------------


def check_upload(kind: str, mime: str, size: int, name: str) -> str:
    """Limits and the mime whitelist; returns the file extension for the key."""
    if kind not in MIME_TYPES:
        raise MediaError(400, "bad_kind")
    ext = MIME_TYPES[kind].get(str(mime or "").strip().lower())
    if ext is None:
        raise MediaError(400, "bad_mime", {"allowed": sorted(MIME_TYPES[kind])})
    clean_name = str(name or "").strip()
    if not clean_name or len(clean_name) > 255 or any(ord(ch) < 32 for ch in clean_name):
        raise MediaError(400, "bad_name")
    try:
        size = int(size)
    except (TypeError, ValueError) as exc:
        raise MediaError(400, "bad_size") from exc
    if size <= 0:
        raise MediaError(400, "bad_size")
    if size > SIZE_LIMITS[kind]:
        raise MediaError(413, "too_large", {"limit_bytes": SIZE_LIMITS[kind]})
    return ext


def chunks_total(size: int, chunk_size: int = CHUNK_SIZE) -> int:
    return max(1, -(-int(size) // int(chunk_size)))


def chunk_length(size: int, n: int, chunk_size: int = CHUNK_SIZE) -> int:
    total = chunks_total(size, chunk_size)
    if n < 0 or n >= total:
        raise MediaError(400, "bad_chunk", {"chunks_total": total})
    return int(chunk_size) if n < total - 1 else int(size) - int(chunk_size) * (total - 1)


def image_signature_ok(mime: str, head: bytes) -> bool:
    if mime == "image/webp":
        return head[:4] == b"RIFF" and head[8:12] == b"WEBP"
    return any(head.startswith(prefix) for prefix in _IMAGE_SIGNATURES.get(mime, ()))


def media_key(tenant_id: str, media_id: str, name: str) -> str:
    return validate_storage_key(f"{tenant_id}/academy/{media_id}/{name}", tenant_id=tenant_id)


# ---- signed links ------------------------------------------------------------------------------


def _secret() -> str:
    secret = str(get_settings().platform_media_signing_secret or "")
    if len(secret) < MIN_SECRET_CHARS:
        raise MediaError(503, "media_unavailable")
    return secret


def sign(storage_key: str, telegram_user_id: int | str, expires: int, secret: str) -> str:
    """nginx: secure_link_md5 "$secure_link_expires$uri$arg_u <secret>"."""
    uri = PUBLIC_PREFIX + validate_storage_key(storage_key)
    digest = hashlib.md5(f"{int(expires)}{uri}{telegram_user_id} {secret}".encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def signed_url(storage_key: str, telegram_user_id: int, *, now: float | None = None) -> str:
    settings = get_settings()
    secret = _secret()
    expires = int(now if now is not None else time.time()) + int(settings.platform_academy_media_url_ttl_seconds)
    key = validate_storage_key(storage_key)
    prefix = API_FILES_PREFIX if settings.platform_academy_media_via_api else PUBLIC_PREFIX
    signature = sign(key, int(telegram_user_id), expires, secret)
    return f"{prefix}{key}?u={int(telegram_user_id)}&e={expires}&s={signature}"


def verify(storage_key: str, user: str, expires: str, signature: str, *, now: float | None = None) -> bool:
    try:
        expires_at = int(expires)
        user_id = int(user)
        expected = sign(storage_key, user_id, expires_at, _secret())
    except (TypeError, ValueError, MediaError):
        return False
    if expires_at < int(now if now is not None else time.time()):
        return False
    return hmac.compare_digest(expected, str(signature or ""))


# ---- files on disk ----------------------------------------------------------------------------


class MediaStore(LocalStorageBackend):
    """Local storage of the Academy: the partner-library backend plus writes."""

    def __init__(self, root: Path) -> None:
        super().__init__(Path(root), signing_secret="-", base_url=PUBLIC_PREFIX)

    def signed_url(self, storage_key: str, ttl: int) -> str:  # pragma: no cover - the format is ours
        raise NotImplementedError("use app.academy.media.signed_url")

    def path(self, storage_key: str) -> Path:
        return self._path(storage_key)

    def _upload_dir(self, tenant_id: str, media_id: str) -> Path:
        return self.path(f"{tenant_id}/academy/{media_id}/chunks/x").parent.parent

    def write_chunk(self, tenant_id: str, media_id: str, n: int, offset: int, data: bytes) -> None:
        folder = self._upload_dir(tenant_id, media_id)
        (folder / "chunks").mkdir(parents=True, exist_ok=True)
        part = folder / "upload.part"
        fd = os.open(part, os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o640)
        try:
            os.lseek(fd, int(offset), os.SEEK_SET)
            view = memoryview(data)
            while view:
                written = os.write(fd, view)
                view = view[written:]
            os.fsync(fd)
        finally:
            os.close(fd)
        (folder / "chunks" / str(int(n))).touch()

    def received(self, tenant_id: str, media_id: str) -> list[int]:
        folder = self._upload_dir(tenant_id, media_id) / "chunks"
        if not folder.is_dir():
            return []
        return sorted(int(item.name) for item in folder.iterdir() if item.name.isdigit())

    def part_head(self, tenant_id: str, media_id: str, length: int = 16) -> bytes:
        with open(self._upload_dir(tenant_id, media_id) / "upload.part", "rb") as handle:
            return handle.read(length)

    def assemble(self, tenant_id: str, media_id: str, final_key: str, size: int) -> Path:
        folder = self._upload_dir(tenant_id, media_id)
        part = folder / "upload.part"
        if not part.is_file() or part.stat().st_size != int(size):
            raise MediaError(409, "upload_incomplete")
        target = self.path(final_key)
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(part, target)
        shutil.rmtree(folder / "chunks", ignore_errors=True)
        return target

    def delete(self, storage_key: str) -> None:
        try:
            self.path(storage_key).unlink()
        except FileNotFoundError:
            pass

    def discard_upload(self, tenant_id: str, media_id: str) -> None:
        folder = self._upload_dir(tenant_id, media_id)
        shutil.rmtree(folder / "chunks", ignore_errors=True)
        try:
            (folder / "upload.part").unlink()
        except FileNotFoundError:
            pass


def get_media_store() -> MediaStore:
    return MediaStore(Path(get_settings().platform_academy_media_dir))
