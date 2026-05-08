"""
Agronomy query engine.

Pipeline:
  1. Load index metadata for every candidate document (router sees only
     doc_id + name + description, no bulky structure).
  2. Router LLM picks the subset of documents likely to answer the question.
  3. The retrieval agent loads full DocContexts for those documents and drives
     tree-search via get_document / get_document_structure / get_page_content.
  4. The agent's final answer is parsed for inline [doc_id:page] citations;
     those become the Source list returned to the API.

Legacy single-shot path is preserved as `query_single_shot` in case someone
still needs the old behavior (or wants to benchmark).
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

import litellm
import pymupdf
from dotenv import load_dotenv

from core.agent import AgentResult, run_agent
from core.citations import Source, build_context_block, extract_inline_citations, extract_sources
from core.config import settings
from core.indexer import get_doc_description, load_index
from core.router import route_documents
from core.tree_tools import DocContext, build_context

load_dotenv()
logger = logging.getLogger(__name__)


@dataclass
class QueryResult:
    answer: str
    sources: list[Source]
    model_used: str


def _catalog_entry(entry: dict) -> dict:
    """Cheap metadata load for the router — no heavy structure in memory."""
    structure = load_index(entry["index_path"])
    return {
        "doc_id": str(entry["doc_id"]),
        "doc_name": entry.get("doc_name") or structure.get("doc_name", ""),
        "doc_description": get_doc_description(structure) or entry.get("description") or "",
        "category": entry.get("category"),
    }


def _dedupe_sources(sources: list[Source]) -> list[Source]:
    """Drop duplicate (doc_id, page) pairs and renumber sequentially."""
    seen: dict[tuple, int] = {}
    out: list[Source] = []
    for s in sources:
        key = (str(s.doc_id), s.page)
        if key in seen:
            continue
        seen[key] = len(out) + 1
        out.append(Source(
            ref=seen[key],
            doc_id=s.doc_id,
            doc_name=s.doc_name,
            page=s.page,
            section=s.section,
        ))
    return out


def query(
    question: str,
    index_entries: list[dict],
    user_data: dict | None = None,
    model: str | None = None,
    history: list[dict] | None = None,
) -> QueryResult:
    """
    Main entry point. `index_entries` items must carry:
      - doc_id: scalar identifier
      - doc_name: human label
      - index_path: path to the PageIndex _structure.json
      - file_path (optional): original file on disk (PDF preferred)
      - description (optional): user-provided description from DB
    """
    used_model = model or settings.query_model

    if not index_entries:
        return QueryResult(
            answer="Não encontrei informações relevantes nos documentos disponíveis.",
            sources=[],
            model_used=used_model,
        )

    # 1. Build lightweight catalog for router
    catalog = []
    entry_by_id: dict[str, dict] = {}
    for entry in index_entries:
        try:
            meta = _catalog_entry(entry)
        except Exception as e:  # noqa: BLE001
            logger.warning("Falha ao carregar metadados de %s: %s", entry.get("doc_id"), e)
            continue
        catalog.append(meta)
        entry_by_id[meta["doc_id"]] = entry

    if not catalog:
        return QueryResult(
            answer="Não foi possível carregar os documentos selecionados.",
            sources=[],
            model_used=used_model,
        )

    # 2. Route
    selected_ids = route_documents(question, catalog, model=settings.router_model)
    logger.info("Documentos selecionados pelo router: %s", selected_ids)

    # 3. Build full DocContexts and run the agent
    doc_ctxs: dict[str, DocContext] = {}
    for doc_id in selected_ids:
        entry = entry_by_id.get(str(doc_id))
        if not entry:
            continue
        try:
            ctx = build_context(entry)
        except Exception as e:  # noqa: BLE001
            logger.warning("Falha ao carregar contexto de %s: %s", doc_id, e)
            continue
        doc_ctxs[ctx.doc_id] = ctx

    if not doc_ctxs:
        return QueryResult(
            answer="Não foi possível abrir os documentos indicados pelo roteador.",
            sources=[],
            model_used=used_model,
        )

    result: AgentResult = run_agent(
        question=question,
        doc_ctxs=doc_ctxs,
        user_data=user_data,
        model=used_model,
        history=history,
    )

    # 4. Extract citations → Source list
    valid_doc_ids = {str(did) for did in doc_ctxs.keys()}
    answer, sources = extract_inline_citations(
        result.answer, result.trace.pages_read, valid_doc_ids=valid_doc_ids
    )
    sources = _dedupe_sources(sources)

    # If the agent produced no citations but did read pages, surface them as
    # supporting references so the user isn't left blind.
    if not sources and result.trace.pages_read:
        unique: dict[tuple, dict] = {}
        for p in result.trace.pages_read:
            key = (str(p.get("doc_id")), p.get("page"))
            unique.setdefault(key, p)
        sources = [
            Source(
                ref=i + 1,
                doc_id=p.get("doc_id", ""),
                doc_name=p.get("doc_name", ""),
                page=p.get("page", 1),
                section=p.get("title", ""),
            )
            for i, p in enumerate(unique.values())
        ]

    return QueryResult(answer=answer, sources=sources, model_used=result.model_used)


# ─────────────────────────────────────────────────────────────────────────────
# Legacy single-shot path (kept for benchmarking / fallback)
# ─────────────────────────────────────────────────────────────────────────────

IDENTIFY_NODES_PROMPT = """\
Você é um especialista em agronomia. Analise a estrutura do documento abaixo \
e identifique os node_ids mais relevantes para responder à pergunta.

Observação: os títulos estão em português e os summaries podem estar em inglês — \
considere ambos ao avaliar a relevância.

Pergunta: {question}

Estrutura do documento "{doc_name}":
{structure}

Responda APENAS em JSON:
{{
  "reasoning": "<breve justificativa>",
  "node_ids": ["id1", "id2"]
}}"""

ANSWER_PROMPT = """\
Você é um especialista em agronomia. Responda à pergunta com base SOMENTE \
nos documentos fornecidos abaixo.

Regras:
- Cite cada informação com o número da fonte inline: [1], [2], etc.
- Se os documentos contiverem fórmulas ou tabelas, aplique-as aos dados \
  fornecidos pelo usuário para calcular a resposta.
- Mostre o raciocínio passo a passo quando fizer cálculos.
- Responda no mesmo idioma da pergunta.
- Se a informação não estiver nos documentos, diga explicitamente.

Pergunta: {question}
{user_data_block}

Documentos:
{context}"""


_MAX_NODE_CHARS = 40_000


def _llm(prompt: str, model: str) -> str:
    response = litellm.completion(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
    )
    return response.choices[0].message.content


def _keyword_fallback(question: str, node_map: dict) -> list[str]:
    stop = {"de", "do", "da", "dos", "das", "o", "a", "os", "as", "e", "em",
            "para", "com", "que", "no", "na", "nos", "nas", "um", "uma", "é",
            "se", "por", "ao", "à", "the", "of", "in", "for", "and", "or"}
    words = {w.lower() for w in re.findall(r"\w+", question) if len(w) > 3} - stop
    scored = []
    for nid, node in node_map.items():
        title = node.get("title", "").lower()
        summary = node.get("summary", "").lower()
        score = sum(3 for w in words if w in title) + sum(1 for w in words if w in summary)
        if score:
            scored.append((score, nid))
    scored.sort(reverse=True)
    return [nid for _, nid in scored[:3]]


def _identify_nodes(question: str, structure: dict, doc_name: str, model: str) -> list[str]:
    from pageindex.utils import create_node_mapping, extract_json

    node_map = create_node_mapping(structure.get("structure", structure))
    summaries = [
        {
            "node_id": n["node_id"],
            "title": n["title"],
            "pages": f"{n.get('start_index', '?')}-{n.get('end_index', '?')}",
            "summary": (n.get("summary") or "")[:500],
        }
        for n in node_map.values()
    ]
    prompt = IDENTIFY_NODES_PROMPT.format(
        question=question,
        doc_name=doc_name,
        structure=json.dumps(summaries, indent=2, ensure_ascii=False),
    )
    raw = _llm(prompt, model)
    result = extract_json(raw)
    node_ids = result.get("node_ids", [])
    if not node_ids:
        node_ids = _keyword_fallback(question, node_map)
    return node_ids


def _extract_pages_from_pdf(file_path: str, start: int, end: int) -> str:
    doc = pymupdf.open(file_path)
    texts = []
    for i in range(max(0, start - 1), min(end, len(doc))):
        texts.append(doc[i].get_text())
    doc.close()
    text = "\n".join(texts)
    if len(text) > _MAX_NODE_CHARS:
        text = text[:_MAX_NODE_CHARS] + "\n[... texto truncado ...]"
    return text


def _extract_node_text(structure: dict, node_ids: list[str], doc_name: str, doc_id, file_path: str | None = None) -> list[dict]:
    from pageindex.utils import create_node_mapping

    node_map = create_node_mapping(structure.get("structure", structure))
    is_pdf = file_path and Path(file_path).suffix.lower() == ".pdf"

    nodes = []
    for nid in node_ids:
        if nid not in node_map:
            continue
        node = node_map[nid]
        start = node.get("start_index", 1)
        end = node.get("end_index", 1)
        if is_pdf:
            text = _extract_pages_from_pdf(file_path, start, end)
        else:
            text = node.get("text") or node.get("summary") or ""
        nodes.append({
            "doc_id": doc_id,
            "doc_name": doc_name,
            "node_id": nid,
            "title": node.get("title", ""),
            "start_page": start,
            "end_page": end,
            "text": text,
            "summary": node.get("summary") or "",
        })
    return nodes


def query_single_shot(
    question: str,
    index_entries: list[dict],
    user_data: dict | None = None,
    model: str | None = None,
) -> QueryResult:
    """Legacy one-pass identifier → extractor → answer pipeline."""
    used_model = model or settings.query_model
    all_nodes: list[dict] = []
    for entry in index_entries:
        structure = load_index(entry["index_path"])
        doc_name = entry.get("doc_name") or structure.get("doc_name", entry["index_path"])
        node_ids = _identify_nodes(question, structure, doc_name, used_model)
        nodes = _extract_node_text(structure, node_ids, doc_name, entry["doc_id"], file_path=entry.get("file_path"))
        all_nodes.extend(nodes)

    if not all_nodes:
        return QueryResult(
            answer="Não encontrei informações relevantes nos documentos disponíveis.",
            sources=[],
            model_used=used_model,
        )

    context = build_context_block(all_nodes)
    user_data_block = ""
    if user_data:
        lines = "\n".join(f"- {k}: {v}" for k, v in user_data.items())
        user_data_block = f"\nDados fornecidos pelo usuário:\n{lines}\n"

    prompt = ANSWER_PROMPT.format(
        question=question,
        user_data_block=user_data_block,
        context=context,
    )
    raw_answer = _llm(prompt, used_model)
    answer, sources = extract_sources(raw_answer, all_nodes)
    return QueryResult(answer=answer, sources=sources, model_used=used_model)
