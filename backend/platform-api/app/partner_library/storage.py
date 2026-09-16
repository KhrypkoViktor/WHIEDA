from __future__ import annotations

import base64
import hashlib
import hmac
import json
import mimetypes
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Protocol

from app.settings import Settings, get_settings


@dataclass(frozen=True)
class StorageStat:
    size_bytes: int
    mime_type: str | None = None


class StorageBackend(Protocol):
    def exists(self, storage_key: str) -> bool: ...

    def stat(self, storage_key: str) -> StorageStat: ...

    def signed_url(self, storage_key: str, ttl: int) -> str: ...


def validate_storage_key(storage_key: str, *, tenant_id: str | None = None) -> str:
    value = str(storage_key or "").strip().replace("\\", "/")
    parts = value.split("/")
    if (
        not value
        or value.startswith("/")
        or any(part in {"", ".", ".."} for part in parts)
        or any(ord(char) < 32 for char in value)
    ):
        raise ValueError("invalid storage_key")
    if tenant_id and parts[0] != tenant_id:
        raise ValueError("storage_key must start with tenant_id")
    return value


class LocalStorageBackend:
    def __init__(self, root: Path, *, signing_secret: str, base_url: str) -> None:
        self.root = root.resolve()
        self.signing_secret = signing_secret.encode("utf-8")
        self.base_url = base_url.rstrip("/")

    def _path(self, storage_key: str) -> Path:
        key = validate_storage_key(storage_key)
        path = (self.root / Path(*key.split("/"))).resolve()
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise ValueError("storage_key escapes local root") from exc
        return path

    def exists(self, storage_key: str) -> bool:
        return self._path(storage_key).is_file()

    def stat(self, storage_key: str) -> StorageStat:
        path = self._path(storage_key)
        info = path.stat()
        mime_type, _ = mimetypes.guess_type(path.name)
        return StorageStat(size_bytes=info.st_size, mime_type=mime_type)

    def signed_url(self, storage_key: str, ttl: int) -> str:
        key = validate_storage_key(storage_key)
        payload = json.dumps(
            {"key": key, "expires": int(time.time()) + int(ttl)},
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode("utf-8")
        body = base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")
        signature = hmac.new(self.signing_secret, body.encode("ascii"), hashlib.sha256).digest()
        sig = base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")
        return f"{self.base_url}/{body}.{sig}"

    def open_signed_path(self, token: str) -> Path:
        try:
            body, signature = token.split(".", 1)
            expected = hmac.new(
                self.signing_secret,
                body.encode("ascii"),
                hashlib.sha256,
            ).digest()
            provided = base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4))
            if not hmac.compare_digest(expected, provided):
                raise ValueError("invalid signature")
            payload = json.loads(
                base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)).decode("utf-8")
            )
            if int(payload["expires"]) < int(time.time()):
                raise ValueError("expired token")
            path = self._path(str(payload["key"]))
            if not path.is_file():
                raise ValueError("file missing")
            return path
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("invalid local storage token") from exc


class S3StorageBackend:
    def __init__(
        self,
        *,
        bucket: str,
        endpoint_url: str | None,
        region_name: str | None,
        addressing_style: str = "auto",
    ) -> None:
        try:
            import boto3
            from botocore.config import Config
        except ImportError as exc:
            raise RuntimeError("boto3 is required for partner library S3 storage") from exc
        self.bucket = bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            region_name=region_name,
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": addressing_style},
            ),
        )

    def exists(self, storage_key: str) -> bool:
        try:
            self.stat(storage_key)
            return True
        except FileNotFoundError:
            return False

    def stat(self, storage_key: str) -> StorageStat:
        key = validate_storage_key(storage_key)
        try:
            response = self.client.head_object(Bucket=self.bucket, Key=key)
        except Exception as exc:
            error = getattr(exc, "response", {}).get("Error", {})
            if str(error.get("Code", "")) in {"404", "NoSuchKey", "NotFound"}:
                raise FileNotFoundError(key) from exc
            raise
        return StorageStat(
            size_bytes=int(response.get("ContentLength") or 0),
            mime_type=response.get("ContentType"),
        )

    def signed_url(self, storage_key: str, ttl: int) -> str:
        key = validate_storage_key(storage_key)
        return str(
            self.client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket, "Key": key},
                ExpiresIn=int(ttl),
            )
        )


def _build_storage_backend(settings: Settings) -> StorageBackend:
    if settings.platform_partner_library_storage_backend == "s3":
        if not settings.platform_partner_library_s3_bucket:
            raise RuntimeError("PLATFORM_PARTNER_LIBRARY_S3_BUCKET is required")
        return S3StorageBackend(
            bucket=settings.platform_partner_library_s3_bucket,
            endpoint_url=settings.platform_partner_library_s3_endpoint_url,
            region_name=settings.platform_partner_library_s3_region,
            addressing_style=settings.platform_partner_library_s3_addressing_style,
        )
    if settings.platform_partner_library_storage_backend == "filesystem":
        root_value = settings.platform_partner_library_filesystem_root
        secret = settings.platform_partner_library_filesystem_signing_secret or ""
        if not root_value:
            raise RuntimeError("PLATFORM_PARTNER_LIBRARY_FILESYSTEM_ROOT is required")
        root = Path(root_value)
        if not root.is_absolute():
            raise RuntimeError("partner library filesystem root must be absolute")
        if len(secret) < 32:
            raise RuntimeError("partner library filesystem signing secret must be at least 32 chars")
        return LocalStorageBackend(
            root,
            signing_secret=secret,
            base_url=settings.platform_partner_library_filesystem_base_url,
        )
    if settings.environment.lower() not in {"development", "dev", "test", "testing", "local"}:
        raise RuntimeError("local partner library storage is allowed only in dev/test")
    if not settings.platform_partner_library_local_root:
        raise RuntimeError("PLATFORM_PARTNER_LIBRARY_LOCAL_ROOT is required")
    local_secret = settings.platform_partner_library_local_signing_secret or ""
    if len(local_secret) < 32:
        raise RuntimeError("PLATFORM_PARTNER_LIBRARY_LOCAL_SIGNING_SECRET is required (>=32 chars)")
    return LocalStorageBackend(
        Path(settings.platform_partner_library_local_root),
        signing_secret=local_secret,
        base_url=settings.platform_partner_library_local_base_url,
    )


@lru_cache(maxsize=1)
def get_storage_backend() -> StorageBackend:
    return _build_storage_backend(get_settings())
