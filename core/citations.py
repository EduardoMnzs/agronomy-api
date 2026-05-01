import re
from dataclasses import dataclass


@dataclass
class Source:
    ref: int
    doc_id: int | str
    doc_name: str
    page: int
    section: str


def extract_sources(answer: str, node_refs: list[dict]) -> tuple[str, list[Source]]:
    """
    Receives the raw LLM answer (with [1], [2] markers) and the list of
    node_refs used as context. Returns the cleaned answer + structured sources.
    """
    sources = []
    for i, ref in enumerate(node_refs, start=1):
        pattern = f"[{i}]"
        if pattern in answer:
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
    """
    Formats retrieved nodes as a numbered context block for the LLM prompt.
    Each block carries its citation number so the model can reference it.
    """
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
