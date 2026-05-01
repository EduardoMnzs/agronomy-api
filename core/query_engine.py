import json
from dataclasses import dataclass

import litellm
from dotenv import load_dotenv

from core.citations import Source, build_context_block, extract_sources
from core.config import settings
from core.indexer import load_index

load_dotenv()

IDENTIFY_NODES_PROMPT = """\
Você é um especialista em agronomia. Analise a estrutura do documento abaixo \
e identifique os node_ids mais relevantes para responder à pergunta.

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


@dataclass
class QueryResult:
    answer: str
    sources: list[Source]
    model_used: str


def _llm(prompt: str, model: str) -> str:
    response = litellm.completion(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
    )
    return response.choices[0].message.content


def _identify_nodes(question: str, structure: dict, doc_name: str, model: str) -> list[str]:
    from pageindex.utils import create_node_mapping, extract_json

    node_map = create_node_mapping(structure.get("structure", structure))
    summaries = [
        {
            "node_id": n["node_id"],
            "title": n["title"],
            "pages": f"{n.get('start_index', '?')}-{n.get('end_index', '?')}",
            "summary": (n.get("summary") or "")[:300],
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
    return result.get("node_ids", [])


def _extract_node_text(structure: dict, node_ids: list[str], doc_name: str, doc_id) -> list[dict]:
    from pageindex.utils import create_node_mapping

    node_map = create_node_mapping(structure.get("structure", structure))
    nodes = []
    for nid in node_ids:
        if nid not in node_map:
            continue
        node = node_map[nid]
        nodes.append({
            "doc_id": doc_id,
            "doc_name": doc_name,
            "node_id": nid,
            "title": node.get("title", ""),
            "start_page": node.get("start_index", 1),
            "end_page": node.get("end_index", 1),
            "text": node.get("text") or node.get("summary") or "",
            "summary": node.get("summary") or "",
        })
    return nodes


def query(
    question: str,
    index_entries: list[dict],
    user_data: dict | None = None,
    model: str | None = None,
) -> QueryResult:
    """
    index_entries: list of {"doc_id", "doc_name", "index_path"}
    """
    used_model = model or settings.LLM_MODEL

    all_nodes: list[dict] = []
    for entry in index_entries:
        structure = load_index(entry["index_path"])
        doc_name = entry.get("doc_name") or structure.get("doc_name", entry["index_path"])

        node_ids = _identify_nodes(question, structure, doc_name, used_model)
        nodes = _extract_node_text(structure, node_ids, doc_name, entry["doc_id"])
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
