"""Helpers anti-DoS para parsers (zip-bomb, billion-laughs, JSON profundo, CSV-bomb)."""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

ZIP_MAX_UNCOMPRESSED_BYTES = 200 * 1024 * 1024
ZIP_MAX_RATIO = 100
ZIP_MAX_FILES = 10_000

JSON_MAX_BYTES = 10 * 1024 * 1024
JSON_MAX_DEPTH = 64

CSV_MAX_ROWS = 100_000


class UnsafeFileError(ValueError):
    pass


def assert_zip_safe(path: Path) -> None:
    try:
        with zipfile.ZipFile(path) as zf:
            entries = zf.infolist()
            if len(entries) > ZIP_MAX_FILES:
                raise UnsafeFileError(
                    f"ZIP contém {len(entries)} arquivos (limite {ZIP_MAX_FILES})"
                )

            total_uncompressed = 0
            total_compressed = 0
            for info in entries:
                name = info.filename
                if name.startswith(("/", "\\")) or ".." in name.replace("\\", "/").split("/"):
                    raise UnsafeFileError(f"caminho inseguro no ZIP: {name!r}")

                total_uncompressed += info.file_size
                total_compressed += max(info.compress_size, 1)
                if total_uncompressed > ZIP_MAX_UNCOMPRESSED_BYTES:
                    raise UnsafeFileError(
                        f"ZIP descomprime para mais de {ZIP_MAX_UNCOMPRESSED_BYTES} bytes"
                    )

            ratio = total_uncompressed / max(total_compressed, 1)
            if ratio > ZIP_MAX_RATIO:
                raise UnsafeFileError(
                    f"ZIP com razão de compressão suspeita ({ratio:.0f}x — possível zip-bomb)"
                )
    except zipfile.BadZipFile as e:
        raise UnsafeFileError(f"arquivo não é um ZIP válido: {e}")


def safe_load_json(path: Path) -> object:
    size = path.stat().st_size
    if size > JSON_MAX_BYTES:
        raise UnsafeFileError(
            f"JSON com {size} bytes excede limite de {JSON_MAX_BYTES}"
        )
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    _assert_json_depth(data, JSON_MAX_DEPTH)
    return data


def _assert_json_depth(obj: object, limit: int, depth: int = 0) -> None:
    if depth > limit:
        raise UnsafeFileError(
            f"JSON aninhado além do limite de {limit} níveis"
        )
    if isinstance(obj, dict):
        for v in obj.values():
            _assert_json_depth(v, limit, depth + 1)
    elif isinstance(obj, list):
        for v in obj:
            _assert_json_depth(v, limit, depth + 1)
