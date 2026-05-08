"""
Citation utilities.

Two inline formats are supported in agent answers:
  - [doc_id:page]  e.g. [3:27]  (preferred — page-level precision)
  - [N]            e.g. [1]     (legacy — node-level, kept for backwards compat)

extract_inline_citations() walks the answer text, replaces each marker with a
sequential [N] so the final text uses a clean 1..n ordering, and returns the
list of Source objects matching each N.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class Source:
    ref: int
    doc_id: int | str
    doc_name: str
    page: int
    section: str


# Matches [3:27] or [session_5:12] — allows alnum + underscore in doc_id
_PAGE_CITE_RE = re.compile(r"\[([A-Za-z0-9_\-]+):(\d+)\]")
_LEGACY_CITE_RE = re.compile(r"\[(\d+)\]")


def _section_for(pages_read: list[dict], doc_id: str, page: int) -> tuple[str, str]:
    """Return (doc_name, section_title) for a (doc_id, page) read by the agent."""
    for entry in pages_read:
        if str(entry.get("doc_id")) == str(doc_id) and entry.get("page") == page:
            return entry.get("doc_name", ""), entry.get("title") or ""
    return "", ""


def extract_inline_citations(
    answer: str,
    pages_read: list[dict],
    valid_doc_ids: set[str] | None = None,
) -> tuple[str, list[Source]]:
    """
    Replace inline [doc_id:page] markers in the LLM answer with [N] references
    and return (rewritten_answer, sources). Keeps a stable numbering: each
    unique (doc_id, page) pair gets one ref, in order of first occurrence.

    If `valid_doc_ids` is provided, citations referencing a doc_id not in the
    set are stripped from the answer (the LLM hallucinated a source). We prefer
    silent removal over surfacing "Documento N" phantoms that break preview.
    """
    order: dict[tuple[str, int], int] = {}
    sources: list[Source] = []

    def _replace(match: re.Match) -> str:
        doc_id = match.group(1)
        page = int(match.group(2))

        if valid_doc_ids is not None and str(doc_id) not in valid_doc_ids:
            return ""  # drop invalid citation marker

        key = (str(doc_id), page)
        if key not in order:
            doc_name, section = _section_for(pages_read, doc_id, page)
            # If the agent cited a page it never fetched, we still record it so
            # the user sees the claim — section will be empty.
            if not doc_name:
                for entry in pages_read:
                    if str(entry.get("doc_id")) == str(doc_id):
                        doc_name = entry.get("doc_name", "")
                        break
            order[key] = len(order) + 1
            sources.append(
                Source(
                    ref=order[key],
                    doc_id=doc_id,
                    doc_name=doc_name or f"Documento {doc_id}",
                    page=page,
                    section=section,
                )
            )
        return f"[{order[key]}]"

    rewritten = _PAGE_CITE_RE.sub(_replace, answer)

    # If no page-level cites but legacy [N] used, fall back to pages_read order.
    if not sources and pages_read:
        seen: dict[int, int] = {}

        def _legacy(match: re.Match) -> str:
            n = int(match.group(1))
            if n in seen:
                return f"[{seen[n]}]"
            if 1 <= n <= len(pages_read):
                entry = pages_read[n - 1]
                new_ref = len(sources) + 1
                sources.append(
                    Source(
                        ref=new_ref,
                        doc_id=entry.get("doc_id", ""),
                        doc_name=entry.get("doc_name", ""),
                        page=entry.get("page", 1),
                        section=entry.get("title", ""),
                    )
                )
                seen[n] = new_ref
                return f"[{new_ref}]"
            return match.group(0)

        rewritten = _LEGACY_CITE_RE.sub(_legacy, rewritten)

    return rewritten, sources


# Kept for backwards compatibility with callers still passing node lists.
def extract_sources(answer: str, node_refs: list[dict]) -> tuple[str, list[Source]]:
    sources: list[Source] = []
    for i, ref in enumerate(node_refs, start=1):
        if f"[{i}]" in answer:
            sources.append(
                Source(
                    ref=i,
                    doc_id=ref.get("doc_id"),
                    doc_name=ref.get("doc_name", "Documento desconhecido"),
                    page=ref.get("start_page", 1),
                    section=ref.get("title", ""),
                )
            )
    return answer, sources


def build_context_block(nodes: list[dict]) -> str:
    blocks = []
    for i, node in enumerate(nodes, start=1):
        doc_name = node.get("doc_name", "Documento")
        page = node.get("start_page", "?")
        section = node.get("title", "")
        text = node.get("text") or node.get("summary") or ""
        header = f"[{i}] {doc_name} — pág. {page}"
        if section:
            header += f" | {section}"
        blocks.append(f"{header}\n{text}")
    return "\n\n".join(blocks)
