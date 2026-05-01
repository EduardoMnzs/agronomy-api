import asyncio
import json
import tempfile
from pathlib import Path

from parsers.factory import get_parser
from core.config import settings


def _get_index_path(file_path: Path, indexes_dir: str) -> Path:
    return Path(indexes_dir) / (file_path.stem + "_structure.json")


def index_document(file_path: Path, indexes_dir: str, llm_model: str | None = None) -> Path:
    """
    Index a document using PageIndex engine.
    Returns the path to the generated JSON structure.
    """
    from pageindex import page_index_main
    from pageindex.page_index_md import md_to_tree
    from pageindex.utils import ConfigLoader

    model = llm_model or settings.LLM_MODEL
    index_path = _get_index_path(file_path, indexes_dir)
    index_path.parent.mkdir(parents=True, exist_ok=True)

    if file_path.suffix.lower() == ".pdf":
        opt = ConfigLoader().load({"model": model})
        structure = page_index_main(str(file_path), opt)
    else:
        parser = get_parser(file_path)
        parsed = parser.parse(file_path)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", encoding="utf-8", delete=False) as tmp:
            tmp.write(parsed.text)
            tmp_path = Path(tmp.name)

        try:
            structure = asyncio.run(
                md_to_tree(
                    md_path=str(tmp_path),
                    if_thinning=False,
                    if_add_node_summary=True,
                    if_add_node_id=True,
                    model=model,
                )
            )
            # Inject original filename so citations reference the real file
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


def load_index(index_path: str | Path) -> dict:
    with open(index_path, encoding="utf-8") as f:
        return json.load(f)
