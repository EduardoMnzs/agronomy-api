"""Helpers de upload com UUID storage filename e cap de tamanho em streaming."""
from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path

from fastapi import HTTPException, UploadFile, status

from core.config import settings

_CHUNK_SIZE = 1 << 16


def _max_bytes_for(suffix: str) -> int:
    return settings.MAX_UPLOAD_BYTES_BY_EXT.get(suffix.lower(), settings.MAX_UPLOAD_BYTES)


def safe_storage_name(suffix: str) -> str:
    suffix = suffix.lower()
    if not suffix.startswith("."):
        suffix = "." + suffix if suffix else ""
    return f"{uuid.uuid4().hex}{suffix}"


async def save_upload_async(
    file: UploadFile,
    target_dir: Path,
    suffix: str,
    *,
    max_bytes: int | None = None,
) -> tuple[str, int]:
    """Save upload to storage and return (storage_key_or_path, size).

    Always buffers to a local temp file first, then uploads to S3 if needed.
    Returns a storage key (S3 mode) or local path string (local mode).
    """
    from core import storage as store

    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / safe_storage_name(suffix)
    cap = max_bytes if max_bytes is not None else _max_bytes_for(suffix)

    total = 0
    try:
        with open(target, "wb") as out:
            while chunk := await file.read(_CHUNK_SIZE):
                total += len(chunk)
                if total > cap:
                    out.close()
                    target.unlink(missing_ok=True)
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=f"Arquivo excede o limite de {cap // (1024 * 1024)} MB",
                    )
                out.write(chunk)
    except HTTPException:
        raise
    except Exception:
        target.unlink(missing_ok=True)
        raise

    # Upload to S3 in executor to avoid blocking the event loop
    loop = asyncio.get_event_loop()
    key = await loop.run_in_executor(None, store.finalize_to_storage, target)
    return key, total


def save_upload_sync(
    file: UploadFile,
    target_dir: Path,
    suffix: str,
    *,
    max_bytes: int | None = None,
) -> tuple[Path, int]:
    """Save upload locally and return (local_path, size).

    Always writes to local disk — callers are responsible for calling
    ``storage.finalize_to_storage`` after any post-processing (e.g. indexing).
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / safe_storage_name(suffix)
    cap = max_bytes if max_bytes is not None else _max_bytes_for(suffix)

    total = 0
    try:
        with open(target, "wb") as out:
            while True:
                chunk = file.file.read(_CHUNK_SIZE)
                if not chunk:
                    break
                total += len(chunk)
                if total > cap:
                    out.close()
                    target.unlink(missing_ok=True)
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=f"Arquivo excede o limite de {cap // (1024 * 1024)} MB",
                    )
                out.write(chunk)
    except HTTPException:
        raise
    except Exception:
        target.unlink(missing_ok=True)
        raise

    return target, total


def is_path_inside(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def safe_extension(filename: str | None, allowed: set[str]) -> str:
    if not filename:
        raise HTTPException(status_code=400, detail="Nome de arquivo ausente")
    suffix = os.path.splitext(filename)[1].lower()
    if suffix not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"Formato não suportado: {suffix or '(sem extensão)'}",
        )
    return suffix
