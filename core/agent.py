"""
Iterative retrieval agent.

Given a question and a set of pre-routed DocContexts, the agent can call:
  - list_documents()            : see the catalog with descriptions
  - get_document(doc_id)        : metadata + top-level ToC
  - get_document_structure(...) : full tree (or subtree at node_id)
  - get_page_content(...)       : exact page range of a document

It loops until it decides to produce a final answer or hits a cap.

The final answer carries inline citations like [1], [2] and the agent reports
which pages it used; the query engine maps those back to Source objects.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from core.config import settings
from core.llm import tool_complete
from core.tree_tools import (
    DocContext,
    tool_get_document,
    tool_get_document_structure,
    tool_get_page_content,
)

logger = logging.getLogger(__name__)


AGENT_SYSTEM = """\
Você é um assistente especialista em agronomia que responde perguntas \
utilizando exclusivamente o conteúdo dos documentos disponíveis. \
Siga RIGOROSAMENTE este protocolo de ferramentas:

1. Use list_documents() apenas se precisar lembrar o catálogo.
2. Para cada documento candidato, chame get_document(doc_id) para ver a \
ToC de alto nível.
3. Use get_document_structure(doc_id, node_id?) para navegar a árvore e \
identificar seções específicas. Prefira descer no node_id certo em vez de \
baixar a árvore inteira.
4. Chame get_page_content(doc_id, pages) com RANGES APERTADOS (ex.: '22-24', \
'5,8', '12'). NUNCA peça o documento inteiro. Se errar, tente outro range \
com base no que leu.
5. Antes de cada chamada de ferramenta, produza UMA frase explicando o motivo.
6. Quando tiver evidência suficiente, responda em português SEM chamar mais \
ferramentas. A resposta final deve:
   - Citar cada fato com marcadores inline no formato [doc_id:página], por \
exemplo [3:27] para o documento de id 3 na página 27.
   - Se houver fórmulas ou tabelas nos documentos, aplicá-las aos dados do \
usuário e mostrar o cálculo passo a passo.
   - Declarar explicitamente quando a resposta NÃO estiver nos documentos.

Não invente fatos. Não responda fora dos documentos. Seja conciso e técnico."""


TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "list_documents",
            "description": "Lista os documentos disponíveis com doc_id, nome e descrição.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_document",
            "description": "Retorna metadados de um documento (nome, descrição, tipo, páginas) e sua ToC de primeiro nível.",
            "parameters": {
                "type": "object",
                "properties": {
                    "doc_id": {"type": "string", "description": "ID do documento"},
                },
                "required": ["doc_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_document_structure",
            "description": (
                "Retorna a árvore de seções do documento. Se node_id for "
                "fornecido, devolve apenas a subárvore daquele nó."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "doc_id": {"type": "string"},
                    "node_id": {
                        "type": "string",
                        "description": "Opcional. ID do nó para descer na árvore.",
                    },
                },
                "required": ["doc_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_page_content",
            "description": (
                "Retorna o conteúdo de páginas específicas. Formato de pages: "
                "'5-7', '3,8' ou '12'. Use ranges APERTADOS."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "doc_id": {"type": "string"},
                    "pages": {"type": "string"},
                },
                "required": ["doc_id", "pages"],
            },
        },
    },
]


@dataclass
class RetrievalTrace:
    """Records every tool call the agent made and every page it read."""

    tool_calls: list[dict] = field(default_factory=list)
    pages_read: list[dict] = field(default_factory=list)  # [{doc_id, page, title}]

    def record_call(self, name: str, args: dict):
        self.tool_calls.append({"tool": name, "args": args})

    def record_pages(self, doc_id: str, payload: dict, doc_name: str, structure: list | dict):
        pages = payload.get("pages") or []
        for p in pages:
            pg = p.get("page")
            if pg is None:
                continue
            self.pages_read.append({
                "doc_id": doc_id,
                "doc_name": doc_name,
                "page": pg,
                "title": _find_section_title_for_page(structure, pg) or p.get("title") or "",
            })


def _find_section_title_for_page(tree, page: int) -> str | None:
    """Find the deepest section whose [start_index, end_index] contains page."""
    best: tuple[int, str] | None = None  # (depth, title)

    def _walk(nodes, depth):
        nonlocal best
        if not isinstance(nodes, list):
            return
        for node in nodes:
            if not isinstance(node, dict):
                continue
            s = node.get("start_index")
            e = node.get("end_index")
            if s is not None and e is not None and s <= page <= e:
                title = node.get("title") or ""
                if best is None or depth > best[0]:
                    best = (depth, title)
                _walk(node.get("nodes") or [], depth + 1)

    _walk(tree if isinstance(tree, list) else [tree], 0)
    return best[1] if best else None


@dataclass
class AgentResult:
    answer: str
    trace: RetrievalTrace
    model_used: str


def _catalog_payload(doc_ctxs: dict[str, DocContext]) -> str:
    items = [
        {
            "doc_id": ctx.doc_id,
            "doc_name": ctx.doc_name,
            "doc_description": ctx.doc_description,
            "type": "pdf" if ctx.is_pdf else "non-pdf",
            "page_count": ctx.total_pages,
        }
        for ctx in doc_ctxs.values()
    ]
    return json.dumps({"documents": items}, ensure_ascii=False)


def _dispatch_tool(
    name: str,
    args: dict,
    doc_ctxs: dict[str, DocContext],
    trace: RetrievalTrace,
) -> str:
    if name == "list_documents":
        return _catalog_payload(doc_ctxs)

    doc_id = str(args.get("doc_id") or "")
    ctx = doc_ctxs.get(doc_id)
    if not ctx:
        return json.dumps({
            "error": f"doc_id '{doc_id}' não está disponível para esta consulta",
            "available_ids": list(doc_ctxs.keys()),
        })

    if name == "get_document":
        return tool_get_document(ctx)
    if name == "get_document_structure":
        return tool_get_document_structure(ctx, args.get("node_id"))
    if name == "get_page_content":
        pages = args.get("pages") or ""
        payload_str = tool_get_page_content(
            ctx, pages, max_pages=settings.AGENT_MAX_PAGES_PER_CALL
        )
        try:
            payload = json.loads(payload_str)
            trace.record_pages(ctx.doc_id, payload, ctx.doc_name, ctx.root_structure)
        except Exception:  # noqa: BLE001
            pass
        return payload_str

    return json.dumps({"error": f"ferramenta desconhecida: {name}"})


def run_agent(
    question: str,
    doc_ctxs: dict[str, DocContext],
    user_data: dict | None = None,
    model: str | None = None,
) -> AgentResult:
    used_model = model or settings.agent_model
    trace = RetrievalTrace()

    user_block = question.strip()
    if user_data:
        data_lines = "\n".join(f"- {k}: {v}" for k, v in user_data.items())
        user_block += f"\n\nDados fornecidos pelo usuário:\n{data_lines}"

    # Seed the conversation with the catalog so the agent can start routing.
    catalog = _catalog_payload(doc_ctxs)
    messages: list[dict] = [
        {
            "role": "user",
            "content": (
                f"Pergunta: {user_block}\n\n"
                f"Catálogo de documentos disponíveis:\n{catalog}\n\n"
                "Use as ferramentas para navegar na árvore e ler apenas os "
                "trechos realmente relevantes antes de responder."
            ),
        }
    ]

    for step in range(settings.AGENT_MAX_TOOL_CALLS):
        msg = tool_complete(
            messages=messages,
            tools=TOOLS_SCHEMA,
            model=used_model,
            system=AGENT_SYSTEM,
        )
        tool_calls = getattr(msg, "tool_calls", None) or []

        # Always record what the model said (with or without content)
        assistant_entry = {"role": "assistant", "content": msg.content or ""}
        if tool_calls:
            assistant_entry["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in tool_calls
            ]
        messages.append(assistant_entry)

        if not tool_calls:
            final = msg.content or ""
            return AgentResult(answer=final, trace=trace, model_used=used_model)

        for tc in tool_calls:
            name = tc.function.name
            try:
                raw_args = tc.function.arguments or "{}"
                args = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
            except json.JSONDecodeError:
                args = {}

            trace.record_call(name, args)
            logger.info("[agent step=%d] %s(%s)", step, name, args)
            tool_output = _dispatch_tool(name, args, doc_ctxs, trace)

            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": tool_output,
            })

    # Budget exhausted — force a final answer without tools
    messages.append({
        "role": "user",
        "content": (
            "Orçamento de ferramentas esgotado. Responda agora com base apenas "
            "no que já foi lido, citando [doc_id:página]. Se a evidência for "
            "insuficiente, diga explicitamente."
        ),
    })
    final_msg = tool_complete(
        messages=messages,
        tools=TOOLS_SCHEMA,
        model=used_model,
        system=AGENT_SYSTEM,
    )
    return AgentResult(
        answer=final_msg.content or "Não foi possível concluir com o orçamento disponível.",
        trace=trace,
        model_used=used_model,
    )
