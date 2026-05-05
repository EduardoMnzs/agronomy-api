from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from core.indexer import load_index

logger = logging.getLogger(__name__)

_MAX_CONTENT_CHARS = 40_000


@dataclass
class DocContext:
    doc_id: str
    doc_name: str
    doc_description: str
    file_path: str | None
    structure: dict
    is_pdf: bool
    total_pages: int | None = None
    pages_cache: list[str] | None = None
    touched_pages: set[int] = field(default_factory=set)

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
    result["top_level_sections"] = _shallow_structure(ctx.root_structure, max_depth=1)
    return json.dumps(result, ensure_ascii=False)


def tool_get_document_structure(ctx: DocContext, node_id: str | None = None) -> str:
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


def tool_search_document(ctx: DocContext, query: str, max_results: int = 10) -> str:
    import re as _re

    q = query.lower().strip()
    q_norm = _re.sub(r"[^a-z0-9\s]", " ", q).split()

    def _title_exact(node: dict) -> bool:
        return (node.get("title") or "").lower().strip() == q

    def _title_contains_all_tokens(node: dict) -> bool:
        title = _re.sub(r"[^a-z0-9\s]", " ", (node.get("title") or "").lower())
        return all(tok in title for tok in q_norm)

    def _any_title_match(nodes, fn) -> bool:
        for n in nodes if isinstance(nodes, list) else [nodes]:
            if isinstance(n, dict):
                if fn(n):
                    return True
                if _any_title_match(n.get("nodes") or [], fn):
                    return True
        return False

    tree = ctx.root_structure

    if _any_title_match(tree if isinstance(tree, list) else [tree], _title_exact):
        def _matches(node: dict) -> bool:
            return _title_exact(node)
    elif _any_title_match(tree if isinstance(tree, list) else [tree], _title_contains_all_tokens):
        def _matches(node: dict) -> bool:  # noqa: F811
            return _title_contains_all_tokens(node)
    else:
        def _matches(node: dict) -> bool:  # noqa: F811
            return (
                q in (node.get("title") or "").lower()
                or q in (node.get("text") or "").lower()
                or q in (node.get("summary") or "").lower()
            )

    def _any_child_matches(node: dict) -> bool:
        for child in node.get("nodes") or []:
            if _matches(child) or _any_child_matches(child):
                return True
        return False

    def _page_ref(node: dict) -> str:
        if node.get("start_index") is not None:
            return f"{node['start_index']}-{node.get('end_index', node['start_index'])}"
        if node.get("line_num") is not None:
            return str(node["line_num"])
        return "?"

    results: list[dict] = []

    def _content_lines(text: str) -> int:
        return sum(1 for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#"))

    def _walk(nodes, path: list[str]):
        for node in nodes or []:
            title = node.get("title") or ""
            next_path = path + [title] if title else path
            if _matches(node):
                if _any_child_matches(node):
                    _walk(node.get("nodes") or [], next_path)
                else:
                    text = node.get("text") or node.get("summary") or ""
                    breadcrumb = " > ".join(next_path)
                    if _content_lines(text) == 0 and node.get("nodes"):
                        for child in node["nodes"]:
                            child_text = child.get("text") or child.get("summary") or ""
                            child_title = child.get("title") or ""
                            child_breadcrumb = " > ".join(next_path + [child_title]) if child_title else breadcrumb
                            results.append({
                                "node_id": child.get("node_id"),
                                "title": child.get("title"),
                                "section_path": child_breadcrumb,
                                "page": _page_ref(child),
                                "snippet": child_text,
                            })
                    else:
                        results.append({
                            "node_id": node.get("node_id"),
                            "title": node.get("title"),
                            "section_path": breadcrumb,
                            "page": _page_ref(node),
                            "snippet": text,
                        })
            else:
                _walk(node.get("nodes") or [], next_path)

    tree = ctx.root_structure
    _walk(tree if isinstance(tree, list) else [tree], [])
    return json.dumps(
        {"doc_id": ctx.doc_id, "query": query, "results": results[:max_results]},
        ensure_ascii=False,
    )


def tool_get_page_content(ctx: DocContext, pages: str, max_pages: int = 8) -> str:
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
