"""
Tree-navigation tools used by the retrieval agent.

These mirror the three tool functions PageIndex exposes in its cookbook:
  - get_document()            — metadata + top-level tree
  - get_document_structure()  — full structure of one document
  - get_page_content()        — content of specific pages / line ranges

The agent calls them iteratively to perform reasoning-based retrieval.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from core.indexer import load_index

logger = logging.getLogger(__name__)


# Max characters returned by get_page_content for a single call.
# PDF pages are ~3-5k chars — this caps a request at ~8 pages of text.
_MAX_CONTENT_CHARS = 40_000


@dataclass
class DocContext:
    """One document loaded and ready to be navigated by the agent."""

    doc_id: str
    doc_name: str
    doc_description: str
    file_path: str | None  # original PDF path, if available
    structure: dict  # the JSON tree as stored on disk
    is_pdf: bool
    total_pages: int | None = None
    pages_cache: list[str] | None = None  # lazy pdf text cache
    touched_pages: set[int] = field(default_factory=set)  # pages actually fetched

    @property
    def root_structure(self) -> list | dict:
        return self.structure.get("structure", self.structure)

    def iter_nodes(self):
        from pageindex.utils import create_node_mapping
        return create_node_mapping(self.root_structure)


def build_context(entry: dict) -> DocContext:
    structure = load_index(entry["index_path"])
    file_path = entry.get("file_path")
    is_pdf = bool(file_path and Path(file_path).suffix.lower() == ".pdf")
    doc_name = entry.get("doc_name") or structure.get("doc_name") or str(entry.get("doc_id"))
    desc = structure.get("doc_description", "") or entry.get("description") or ""

    total_pages = None
    if is_pdf:
        try:
            with pymupdf.open(file_path) as doc:
                total_pages = len(doc)
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to read PDF %s: %s", file_path, e)

    return DocContext(
        doc_id=str(entry["doc_id"]),
        doc_name=doc_name,
        doc_description=desc,
        file_path=file_path,
        structure=structure,
        is_pdf=is_pdf,
        total_pages=total_pages,
    )


def _shallow_structure(tree, max_depth: int = 3):
    """Return structure limited to max_depth, stripping heavy fields."""
    def _walk(nodes, depth):
        out = []
        if not isinstance(nodes, list):
            return out
        for node in nodes:
            if not isinstance(node, dict):
                continue
            clean = {
                "node_id": node.get("node_id"),
                "title": node.get("title"),
            }
            if "start_index" in node:
                clean["pages"] = f"{node.get('start_index', '?')}-{node.get('end_index', '?')}"
            if node.get("summary"):
                clean["summary"] = (node["summary"] or "")[:400]
            children = node.get("nodes") or []
            if children and depth < max_depth:
                clean["nodes"] = _walk(children, depth + 1)
            elif children:
                clean["child_count"] = len(children)
            out.append(clean)
        return out

    return _walk(tree, 0)


def tool_get_document(ctx: DocContext) -> str:
    result = {
        "doc_id": ctx.doc_id,
        "doc_name": ctx.doc_name,
        "doc_description": ctx.doc_description,
        "type": "pdf" if ctx.is_pdf else "non-pdf",
    }
    if ctx.total_pages is not None:
        result["page_count"] = ctx.total_pages
    # Include a short top-level ToC (depth=1) so the agent can orient itself
    result["top_level_sections"] = _shallow_structure(ctx.root_structure, max_depth=1)
    return json.dumps(result, ensure_ascii=False)


def tool_get_document_structure(ctx: DocContext, node_id: str | None = None) -> str:
    """
    Return the tree structure (or subtree if node_id is given).
    Always strips the 'text' field to save tokens.
    """
    from pageindex.utils import create_node_mapping

    tree = ctx.root_structure
    if node_id:
        node_map = create_node_mapping(tree)
        if node_id not in node_map:
            return json.dumps({"error": f"node_id '{node_id}' não encontrado no documento {ctx.doc_id}"})
        node = node_map[node_id]
        result = {
            "doc_id": ctx.doc_id,
            "node_id": node_id,
            "title": node.get("title"),
            "summary": node.get("summary"),
            "pages": f"{node.get('start_index', '?')}-{node.get('end_index', '?')}",
            "children": _shallow_structure(node.get("nodes") or [], max_depth=3),
        }
    else:
        result = {
            "doc_id": ctx.doc_id,
            "doc_name": ctx.doc_name,
            "structure": _shallow_structure(tree, max_depth=3),
        }
    return json.dumps(result, ensure_ascii=False)


def _parse_pages(pages: str) -> list[int]:
    result: list[int] = []
    for part in str(pages).split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            start, end = int(a.strip()), int(b.strip())
            if start > end:
                raise ValueError(f"range inválido: {part}")
            result.extend(range(start, end + 1))
        else:
            result.append(int(part))
    return sorted(set(result))


def _pdf_page_content(ctx: DocContext, page_nums: list[int]) -> list[dict]:
    if ctx.pages_cache is None:
        pages: list[str] = []
        with pymupdf.open(ctx.file_path) as doc:
            for page in doc:
                pages.append(page.get_text() or "")
        ctx.pages_cache = pages

    total = len(ctx.pages_cache)
    out = []
    for p in page_nums:
        if 1 <= p <= total:
            out.append({"page": p, "content": ctx.pages_cache[p - 1]})
            ctx.touched_pages.add(p)
    return out


def _non_pdf_page_content(ctx: DocContext, page_nums: list[int]) -> list[dict]:
    """For non-PDF docs, pages are line numbers on the reconstructed markdown tree."""
    if not page_nums:
        return []
    min_l, max_l = min(page_nums), max(page_nums)
    hits: list[dict] = []
    seen: set[int] = set()

    def _walk(nodes):
        for node in nodes or []:
            ln = node.get("line_num")
            if ln and min_l <= ln <= max_l and ln not in seen:
                seen.add(ln)
                hits.append({
                    "page": ln,
                    "title": node.get("title"),
                    "content": node.get("text") or node.get("summary") or "",
                })
                ctx.touched_pages.add(ln)
            if node.get("nodes"):
                _walk(node["nodes"])

    _walk(ctx.root_structure if isinstance(ctx.root_structure, list) else [ctx.root_structure])
    hits.sort(key=lambda x: x["page"])
    return hits


def tool_get_page_content(ctx: DocContext, pages: str, max_pages: int = 8) -> str:
    """
    Retrieve page content for a tight range.
    pages format: '5-7', '3,8', '12'. max_pages caps the total pages returned.
    """
    try:
        page_nums = _parse_pages(pages)
    except (ValueError, AttributeError) as e:
        return json.dumps({"error": f"formato inválido '{pages}': {e}"})

    if len(page_nums) > max_pages:
        truncated = page_nums[:max_pages]
        msg = (
            f"range com {len(page_nums)} páginas excede max_pages={max_pages}; "
            f"truncando para {truncated[0]}-{truncated[-1]}"
        )
        page_nums = truncated
    else:
        msg = None

    try:
        if ctx.is_pdf:
            content = _pdf_page_content(ctx, page_nums)
        else:
            content = _non_pdf_page_content(ctx, page_nums)
    except Exception as e:  # noqa: BLE001
        return json.dumps({"error": f"falha ao ler páginas: {e}"})

    # Enforce character cap
    total_chars = 0
    trimmed: list[dict] = []
    for item in content:
        text = item.get("content", "")
        if total_chars + len(text) > _MAX_CONTENT_CHARS:
            remaining = _MAX_CONTENT_CHARS - total_chars
            if remaining > 500:
                item = {**item, "content": text[:remaining] + "\n[... truncado ...]"}
                trimmed.append(item)
            break
        trimmed.append(item)
        total_chars += len(text)

    payload: dict = {"doc_id": ctx.doc_id, "pages": trimmed}
    if msg:
        payload["note"] = msg
    return json.dumps(payload, ensure_ascii=False)
