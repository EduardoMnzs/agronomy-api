import json
import tempfile
from pathlib import Path

from parsers.factory import get_parser
from core.config import settings


def _get_index_path(file_path: Path, indexes_dir: str) -> Path:
    return Path(indexes_dir) / (file_path.stem + "_structure.json")


async def index_document_async(file_path: Path, indexes_dir: str, llm_model: str | None = None) -> Path:
    from pageindex import page_index_main
    from pageindex.page_index_md import md_to_tree
    from pageindex.utils import ConfigLoader
    import asyncio

    model = llm_model or settings.index_model
    add_desc = "yes" if settings.runtime_get("ENABLE_DOC_DESCRIPTION", settings.ENABLE_DOC_DESCRIPTION) else "no"
    index_path = _get_index_path(file_path, indexes_dir)
    index_path.parent.mkdir(parents=True, exist_ok=True)

    if file_path.suffix.lower() == ".pdf":
        opt = ConfigLoader().load({
            "model": model,
            "toc_check_page_num": 30,
            "if_add_node_summary": "yes",
            "if_add_node_text": "no",
            "if_add_node_id": "yes",
            "if_add_doc_description": add_desc,
        })
        loop = asyncio.get_event_loop()
        structure = await loop.run_in_executor(None, page_index_main, str(file_path), opt)
    else:
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

    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(structure, f, indent=2, ensure_ascii=False)

    return index_path


def index_document(file_path: Path, indexes_dir: str, llm_model: str | None = None) -> Path:
    import asyncio
    return asyncio.run(index_document_async(file_path, indexes_dir, llm_model))


def load_index(index_path: str | Path) -> dict:
    with open(index_path, encoding="utf-8") as f:
        return json.load(f)


def get_doc_description(structure: dict) -> str:
    return (structure.get("doc_description") or "").strip()
