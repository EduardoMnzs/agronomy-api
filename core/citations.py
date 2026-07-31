"""
Citation utilities.

O único formato válido nas respostas do agente é ``[doc_id:page]`` (ex.: [3:27]).

Não interpretamos mais ``[N]`` solto. Antes havia um fallback que lia ``[N]``
como índice 1-based em ``pages_read`` — convenção do pipeline antigo, baseado em
nós. Com o agente atual, que usa doc_id nos marcadores, as duas convenções
colidem de forma silenciosa e perigosa: o agente escreve ``[6]`` querendo dizer
"documento 6", e o fallback devolvia a 6ª página lida (ex.: pág. 26), gerando uma
citação ERRADA com aparência de correta. Fonte errada é pior que fonte ausente —
o usuário clica, abre a página errada e não tem como perceber.

Marcador que não resolve para uma fonte é REMOVIDO do texto, em vez de virar link
morto na UI. Mesmo tratamento que já se dava a doc_id fora do catálogo.

extract_inline_citations() percorre a resposta, troca cada marcador válido por um
[N] sequencial (numeração limpa 1..n) e devolve a lista de Source correspondente.
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
# Marcador só com doc_id, sem página (ex.: "[6]"). Não é citação válida: sem
# página não há o que abrir. Removido do texto para não deixar link morto.
_BARE_CITE_RE = re.compile(r"\[[A-Za-z0-9_\-]+\]")

# Sentinelas usados enquanto reescrevemos, para que a limpeza de marcadores sem
# página não apague as citações válidas recém-numeradas. Caracteres de controle
# porque não podem aparecer no texto do LLM.
_REF_OPEN = "\x00"
_REF_CLOSE = "\x01"


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
        # Emite um sentinela, não o `[N]` final. Se escrevêssemos `[1]` aqui, a
        # limpeza de marcadores sem página logo abaixo apagaria a própria
        # citação que acabamos de criar — `[1]` também casa `[doc_id]`.
        return f"{_REF_OPEN}{order[key]}{_REF_CLOSE}"

    rewritten = _PAGE_CITE_RE.sub(_replace, answer)

    # O que sobrou entre colchetes não tem página (`[6]`, `[abc]`), logo não vira
    # fonte. Remove para não renderizar link morto na UI.
    rewritten = _BARE_CITE_RE.sub("", rewritten)

    # Sentinelas de volta para a forma visível.
    rewritten = rewritten.replace(_REF_OPEN, "[").replace(_REF_CLOSE, "]")

    # A remoção pode deixar " ." ou espaço duplo.
    rewritten = re.sub(r" +([.,;:!?])", r"\1", rewritten)
    rewritten = re.sub(r"[ \t]{2,}", " ", rewritten)

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
