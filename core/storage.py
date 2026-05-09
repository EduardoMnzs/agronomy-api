"""Storage abstraction — local filesystem or S3.

Storage keys are relative paths like ``knowledge/files/abc.pdf``.
LocalStorage resolves them under DATA_DIR; S3Storage uses them as object
keys (with an optional S3_PREFIX).
"""
from __future__ import annotations

import json
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO, Generator

from cachetools import TTLCache

_index_cache: TTLCache = TTLCache(maxsize=128, ttl=300)
_storage_singleton: "_LocalStorage | _S3Storage | None" = None


def _to_key(path: Path | str) -> str:
    """Strip DATA_DIR prefix from a path to produce a normalised storage key.

    Handles both old-style absolute/prefixed paths (``data/knowledge/files/x``)
    and already-normalised keys (``knowledge/files/x``).
    """
    from core.config import settings
    p = str(path).replace("\\", "/")
    prefix = settings.DATA_DIR.rstrip("/").replace("\\", "/") + "/"
    return p[len(prefix):] if p.startswith(prefix) else p


class _LocalStorage:
    def __init__(self, base: str) -> None:
        self._base = Path(base)

    def _p(self, key: str) -> Path:
        p = Path(key)
        if p.is_absolute():
            return p
        # Avoid double-prefix for old-style keys that already include base dir
        try:
            rel = p.relative_to(self._base)
            return self._base / rel
        except ValueError:
            return self._base / p

    def upload(self, key: str, src: bytes | BinaryIO) -> None:
        target = self._p(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(src, (bytes, bytearray)):
            target.write_bytes(src)
        else:
            with open(target, "wb") as f:
                while chunk := src.read(1 << 16):
                    f.write(chunk)

    def upload_from_path(self, key: str, source: Path) -> None:
        target = self._p(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)

    def download_bytes(self, key: str) -> bytes:
        return self._p(key).read_bytes()

    def download_to_file(self, key: str, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self._p(key), dest)

    def delete(self, key: str) -> None:
        self._p(key).unlink(missing_ok=True)

    def exists(self, key: str) -> bool:
        return self._p(key).exists()

    def presigned_url(
        self,
        key: str,
        ttl: int = 900,
        *,
        filename: str | None = None,
        content_type: str | None = None,
    ) -> str | None:
        return None  # local storage serves through the API

    def resolve_path(self, key: str) -> Path:
        return self._p(key)


class _S3Storage:
    def __init__(self) -> None:
        import boto3
        from core.config import settings

        self._bucket = settings.S3_BUCKET
        self._prefix = settings.S3_PREFIX.rstrip("/")
        kw: dict = {"region_name": settings.S3_REGION}
        if settings.S3_ACCESS_KEY:
            kw["aws_access_key_id"] = settings.S3_ACCESS_KEY
            kw["aws_secret_access_key"] = settings.S3_SECRET_KEY
        if settings.S3_ENDPOINT_URL:
            kw["endpoint_url"] = settings.S3_ENDPOINT_URL
        self._s3 = boto3.client("s3", **kw)

    def _fk(self, key: str) -> str:
        return f"{self._prefix}/{key}" if self._prefix else key

    def upload(self, key: str, src: bytes | BinaryIO) -> None:
        if isinstance(src, (bytes, bytearray)):
            self._s3.put_object(Bucket=self._bucket, Key=self._fk(key), Body=src)
        else:
            self._s3.upload_fileobj(src, self._bucket, self._fk(key))

    def upload_from_path(self, key: str, source: Path) -> None:
        self._s3.upload_file(str(source), self._bucket, self._fk(key))

    def download_bytes(self, key: str) -> bytes:
        return self._s3.get_object(Bucket=self._bucket, Key=self._fk(key))["Body"].read()

    def download_to_file(self, key: str, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        self._s3.download_file(self._bucket, self._fk(key), str(dest))

    def delete(self, key: str) -> None:
        self._s3.delete_object(Bucket=self._bucket, Key=self._fk(key))

    def exists(self, key: str) -> bool:
        try:
            self._s3.head_object(Bucket=self._bucket, Key=self._fk(key))
            return True
        except Exception:
            return False

    def presigned_url(
        self,
        key: str,
        ttl: int = 900,
        *,
        filename: str | None = None,
        content_type: str | None = None,
    ) -> str | None:
        params: dict = {"Bucket": self._bucket, "Key": self._fk(key)}
        if filename:
            params["ResponseContentDisposition"] = f'attachment; filename="{filename}"'
        if content_type:
            params["ResponseContentType"] = content_type
        return self._s3.generate_presigned_url("get_object", Params=params, ExpiresIn=ttl)

    def resolve_path(self, key: str) -> Path | None:
        return None


def _storage() -> "_LocalStorage | _S3Storage":
    global _storage_singleton
    if _storage_singleton is None:
        from core.config import settings
        if settings.STORAGE_BACKEND == "s3":
            _storage_singleton = _S3Storage()
        else:
            _storage_singleton = _LocalStorage(settings.DATA_DIR)
    return _storage_singleton


def reset_singleton() -> None:
    """Reset storage singleton — for testing."""
    global _storage_singleton
    _storage_singleton = None


# ── context manager ───────────────────────────────────────────────────────────

@contextmanager
def temp_download(key: str, suffix: str = "") -> Generator[Path, None, None]:
    """Download a storage object to a temp file; delete on context exit."""
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
        tmp = Path(f.name)
    try:
        _storage().download_to_file(key, tmp)
        yield tmp
    finally:
        tmp.unlink(missing_ok=True)


# ── public helpers ────────────────────────────────────────────────────────────

def upload_file(key: str, src: bytes | BinaryIO) -> None:
    _storage().upload(key, src)


def upload_from_path(key: str, source: Path) -> None:
    _storage().upload_from_path(key, source)


def download_bytes(key: str) -> bytes:
    return _storage().download_bytes(key)


def delete_file(key: str) -> None:
    _storage().delete(key)
    _index_cache.pop(key, None)


def file_exists(key: str) -> bool:
    return _storage().exists(key)


def presigned_url(
    key: str,
    ttl: int = 900,
    *,
    filename: str | None = None,
    content_type: str | None = None,
) -> str | None:
    """Returns a presigned URL for S3; None for local storage."""
    return _storage().presigned_url(key, ttl, filename=filename, content_type=content_type)


def resolve_local_path(key: str) -> Path | None:
    """Returns the local filesystem Path if using local storage, else None."""
    st = _storage()
    if isinstance(st, _LocalStorage):
        return st.resolve_path(key)
    return None


def load_index(key: str) -> dict:
    """Load index JSON from storage with an in-process TTL cache (5 min)."""
    normalised = _to_key(key)
    if normalised not in _index_cache:
        _index_cache[normalised] = json.loads(_storage().download_bytes(normalised))
    return _index_cache[normalised]


def finalize_to_storage(local_path: Path) -> str:
    """Upload local_path to S3 (S3 mode) or return str path (local mode).

    In S3 mode the local file is deleted after upload.
    """
    from core.config import settings
    if settings.STORAGE_BACKEND != "s3":
        return str(local_path)
    key = _to_key(local_path)
    _storage().upload_from_path(key, local_path)
    local_path.unlink(missing_ok=True)
    return key
