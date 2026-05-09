from __future__ import annotations

import json
import tempfile
from pathlib import Path

from parsers.factory import get_parser
from core.config import settings


def _index_key(file_path: str | Path, indexes_dir: str) -> str:
    """Return the storage key for the index JSON derived from file_path."""
    from core.storage import _to_key
    stem = Path(str(file_path)).stem
    prefix = _to_key(indexes_dir)
    return f"{prefix}/{stem}_structure.json"


async def _run_indexer(file_path: Path, model: str, add_desc: str) -> dict:
    """Run PageIndex on a local file and return the structure dict."""
    from pageindex import page_index_main
    from pageindex.page_index_md import md_to_tree
    from pageindex.utils import ConfigLoader
    import asyncio

    if file_path.suffix.lower() == ".pdf":
        opt = ConfigLoader().load({
            "model": model,
            "toc_check_page_num": 30,
            "if_add_node_summary": "yes",
            "if_add_node_text": "no",
            "if_add_node_id": "yes",
            "if_add_doc_description": add_desc,
        })
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, page_index_main, str(file_path), opt)

    parser = get_parser(file_path)
    parsed = parser.parse(file_path)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", encoding="utf-8", delete=False) as tmp:
        tmp.write(parsed.text)
        tmp_path = Path(tmp.name)

    try:
        structure = await md_to_tree(
            md_path=str(tmp_path),
            if_thinning=False,
            if_add_node_summary="yes",
            summary_token_threshold=200,
            if_add_node_text="yes",
            if_add_node_id="yes",
            if_add_doc_description=add_desc,
            model=model,
        )
        if isinstance(structure, dict):
            structure["doc_name"] = file_path.name
            structure["original_page_map"] = [
                {"page_number": p.page_number, "label": p.label}
                for p in parsed.page_map
            ]
    finally:
        tmp_path.unlink(missing_ok=True)

    return structure


async def index_document_async(
    file_path: str | Path,
    indexes_dir: str,
    llm_model: str | None = None,
) -> str:
    """Index a document and return its storage key (local path or S3 key)."""
    from core import storage

    model = llm_model or settings.index_model
    add_desc = "yes" if settings.runtime_get("ENABLE_DOC_DESCRIPTION", settings.ENABLE_DOC_DESCRIPTION) else "no"
    index_key = _index_key(file_path, indexes_dir)

    if settings.STORAGE_BACKEND == "s3":
        file_key = storage._to_key(str(file_path))
        suffix = Path(str(file_path)).suffix
        with storage.temp_download(file_key, suffix=suffix) as tmp_file:
            structure = await _run_indexer(tmp_file, model, add_desc)
        payload = json.dumps(structure, indent=2, ensure_ascii=False).encode()
        storage.upload_file(index_key, payload)
        return index_key

    # Local mode — write JSON to disk
    index_path = Path(settings.DATA_DIR) / index_key
    index_path.parent.mkdir(parents=True, exist_ok=True)
    structure = await _run_indexer(Path(str(file_path)), model, add_desc)
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(structure, f, indent=2, ensure_ascii=False)
    return str(index_path)


def index_document(file_path: str | Path, indexes_dir: str, llm_model: str | None = None) -> str:
    import asyncio
    return asyncio.run(index_document_async(file_path, indexes_dir, llm_model))


def load_index(index_path: str | Path) -> dict:
    from core.storage import load_index as _cached_load, _to_key
    return _cached_load(_to_key(str(index_path)))


def get_doc_description(structure: dict) -> str:
    return (structure.get("doc_description") or "").strip()
