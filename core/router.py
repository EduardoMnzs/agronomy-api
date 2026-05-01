"""
Document router — given a question and a catalog of documents, pick the ones
that likely contain the answer. Reduces noise and token usage for the
downstream tree-search agent.
"""
from __future__ import annotations

import json
import logging

from core.config import settings
from core.llm import complete

logger = logging.getLogger(__name__)

ROUTER_SYSTEM = (
    "Você é um roteador de documentos para um sistema de perguntas e respostas "
    "em agronomia. Você recebe uma pergunta e um catálogo com o nome e a "
    "descrição de cada documento. Sua tarefa é escolher o subconjunto mínimo "
    "de documentos que provavelmente contém a resposta. Responda apenas em JSON."
)

ROUTER_PROMPT = """\
Pergunta: {question}

Catálogo de documentos disponíveis:
{catalog}

Escolha os documentos mais relevantes. Se a pergunta for genérica ou \
ambígua, prefira incluir mais documentos. Se for específica (uma fórmula, \
uma norma, um método), seja restritivo.

Responda APENAS com JSON:
{{
  "reasoning": "<breve justificativa>",
  "doc_ids": [<lista de doc_ids escolhidos>]
}}"""


def _format_catalog(catalog: list[dict]) -> str:
    lines = []
    for entry in catalog:
        desc = (entry.get("doc_description") or entry.get("description") or "").strip()
        category = entry.get("category") or ""
        tail = f" | categoria: {category}" if category else ""
        lines.append(
            f"- id={entry['doc_id']} | {entry.get('doc_name', 'sem nome')}{tail}\n"
            f"  descrição: {desc or '(sem descrição)'}"
        )
    return "\n".join(lines)


def route_documents(
    question: str,
    catalog: list[dict],
    model: str | None = None,
) -> list:
    """
    Given a list of candidate documents (with id, name and description),
    return the subset of doc_ids the router considers relevant.

    If the router fails or is disabled, returns all doc_ids (safe fallback).
    """
    all_ids = [c["doc_id"] for c in catalog]

    if not settings.ROUTER_ENABLED or not catalog:
        return all_ids
    if len(catalog) == 1:
        return all_ids

    prompt = ROUTER_PROMPT.format(question=question, catalog=_format_catalog(catalog))

    try:
        raw = complete(prompt, model=model or settings.router_model, system=ROUTER_SYSTEM)
    except Exception as e:  # noqa: BLE001
        logger.warning("Router falhou, retornando todos os documentos: %s", e)
        return all_ids

    from pageindex.utils import extract_json

    parsed = extract_json(raw)
    selected = parsed.get("doc_ids") if isinstance(parsed, dict) else None
    if not selected:
        logger.info("Router não retornou doc_ids, caindo para todos")
        return all_ids

    valid = [d for d in selected if d in all_ids]
    if not valid:
        logger.info("Router retornou ids inválidos, caindo para todos")
        return all_ids

    logger.info("Router selecionou %d/%d documentos", len(valid), len(all_ids))
    return valid
