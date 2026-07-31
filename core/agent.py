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
    tool_search_document,
)

logger = logging.getLogger(__name__)


AGENT_SYSTEM = """\
Você é um assistente especialista em agronomia que responde perguntas \
utilizando exclusivamente o conteúdo dos documentos disponíveis. \
Siga RIGOROSAMENTE este protocolo de ferramentas:

1. Use list_documents() apenas se precisar lembrar o catálogo.
2. Se a pergunta mencionar um nome específico (cultivar, produto, código), \
chame search_document(doc_id, query) IMEDIATAMENTE para localizar o nó exato. \
Use o trecho retornado para confirmar a informação sem precisar navegar a árvore.
3. Para cada documento candidato, chame get_document(doc_id) para ver a \
ToC de alto nível.
4. Use get_document_structure(doc_id, node_id?) para navegar a árvore e \
identificar seções específicas. Prefira descer no node_id certo em vez de \
baixar a árvore inteira.
5. Chame get_page_content(doc_id, pages) com RANGES APERTADOS (ex.: '22-24', \
'5,8', '12'). NUNCA peça o documento inteiro. Se errar, tente outro range \
com base no que leu.
6. Antes de cada chamada de ferramenta, produza UMA frase explicando o motivo.
7. OBRIGATÓRIO: chame get_page_content em pelo menos UMA página antes de \
responder, mesmo em perguntas panorâmicas do tipo "o que fala neste documento". \
Títulos e resumos da árvore NÃO servem como fonte — só página lida serve. \
Se a pergunta é ampla, leia a página de uma seção representativa.
8. Quando tiver evidência suficiente, responda em português SEM chamar mais \
ferramentas. A resposta final deve:
   - Citar cada fato com marcadores inline no formato [doc_id:página], por \
exemplo [3:27] para o documento de id 3 na página 27. \
SEMPRE inclua a página: `[3]` sozinho NÃO é citação válida e será descartado. \
NUNCA cite um doc_id que não esteja no catálogo de documentos disponíveis ou \
que você não tenha lido via get_page_content. Se não tiver evidência, responda \
sem marcador de citação.
   - Se houver fórmulas ou tabelas nos documentos, aplicá-las aos dados do \
usuário e mostrar o cálculo passo a passo.
   - Declarar explicitamente quando a resposta NÃO estiver nos documentos.
   - NUNCA misturar dados de culturas, cultivares ou categorias diferentes. \
Cada linha ou bloco de dados pertence exclusivamente à cultura/cultivar descrita \
naquele bloco. Se um bloco contiver dados de múltiplas entradas, leia com \
atenção qual linha corresponde exatamente ao item perguntado.
   - Resultados de search_document trazem o campo "section_path" (ex.: \
"AMENDOIM > Zoneamento de Plantio"). Use ESTE caminho — e não apenas o título \
do nó — para confirmar a qual cultura cada linha pertence. Se a pergunta é sobre \
AMENDOIM e o section_path começa com outra cultura, DESCARTE o hit.
   - Quando a pergunta usar "quantidade de períodos", "número de janelas" ou \
expressão similar, responda com a CONTAGEM de blocos/linhas distintos (ex.: \
"2 períodos"), NÃO com a duração somada em dias ou meses. Se a duração for \
relevante como complemento, mencione-a DEPOIS da contagem, deixando claro que \
são grandezas diferentes.

Não invente fatos. Não responda fora dos documentos. Seja conciso e técnico."""


_PROFILE_RULES = """\

CONTEXTO DO PERFIL DO USUÁRIO (metadados de configuração da conta, NÃO fazem \
parte da pergunta):
{profile_lines}

Regras ABSOLUTAS sobre esse perfil:
- Ele existe apenas para você DESEMPATAR e PRIORIZAR informação quando o \
documento trouxer variações por região, cultura, sistema de plantio ou unidade. \
Ex.: se o documento tem doses por bioma, prefira a do bioma do perfil.
- NUNCA mencione, cite, liste ou repita esses valores na resposta.
- NUNCA trate o perfil como parte da pergunta. Se o usuário perguntou "o que \
fala no documento", responda sobre o documento — não sobre o perfil.
- NUNCA comente que o documento não cobre o estado, município, bioma, cultura \
ou unidade do perfil. A ausência disso no documento é irrelevante e não deve \
ser reportada.
- Se o perfil não ajudar a responder, ignore-o por completo e em silêncio."""


def _profile_block(profile: dict | None) -> str:
    """Bloco de sistema com o perfil. Vazio quando não há perfil."""
    if not profile:
        return ""
    lines = "\n".join(f"- {k}: {v}" for k, v in profile.items())
    return _PROFILE_RULES.format(profile_lines=lines)


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
    {
        "type": "function",
        "function": {
            "name": "search_document",
            "description": (
                "Busca por palavra-chave ou nome de cultivar/produto em todo o "
                "conteúdo do documento (título, texto e resumo dos nós). "
                "Use quando precisar localizar um item específico sem navegar "
                "a árvore manualmente."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "doc_id": {"type": "string"},
                    "query": {"type": "string", "description": "Termo a buscar"},
                },
                "required": ["doc_id", "query"],
            },
        },
    },
]


@dataclass
class RetrievalTrace:
    tool_calls: list[dict] = field(default_factory=list)
    pages_read: list[dict] = field(default_factory=list)

    def record_call(self, name: str, args: dict):
        self.tool_calls.append({"tool": name, "args": args})

    def record_pages(self, doc_id: str, payload: dict, doc_name: str, structure: list | dict):
        for p in payload.get("pages") or []:
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
    best: tuple[int, str] | None = None

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


_STOPWORDS = {
    "de", "do", "da", "dos", "das", "o", "a", "os", "as", "e", "em", "para",
    "com", "que", "no", "na", "nos", "nas", "um", "uma", "e", "se", "por",
    "ao", "a", "the", "of", "in", "for", "and", "or", "quais", "qual",
    "sao", "sao", "sao", "os", "periodos", "periodo", "periodo", "recomendados",
    "recomendado", "cultura", "ciclo", "solo", "risco", "plantio", "dados",
    "quiser", "plantar", "limitando", "meu", "seus", "respectivos", "disponiveis",
    "disponivel", "grupos", "grupo", "quais", "qual", "quero", "preciso",
    "informacoes", "informacao", "periodos", "periodo", "sobre", "para",
}


def _extract_keywords(question: str) -> list[str]:
    import re
    tokens = re.findall(r"[A-Za-zÀ-ÿ0-9]+", question)
    meaningful = [t for t in tokens if len(t) >= 3 and t.lower() not in _STOPWORDS]

    individual = list(dict.fromkeys(t for t in meaningful))
    phrases2 = [" ".join(meaningful[i:i+2]) for i in range(len(meaningful)-1)]
    phrases3 = [" ".join(meaningful[i:i+3]) for i in range(len(meaningful)-2)]

    candidates = phrases3 + phrases2 + individual

    seen: set[str] = set()
    result: list[str] = []
    for kw in candidates:
        if kw.lower() not in seen:
            seen.add(kw.lower())
            result.append(kw)
    return result[:20]


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
            ctx, pages, max_pages=settings.runtime_get("AGENT_MAX_PAGES_PER_CALL", settings.AGENT_MAX_PAGES_PER_CALL)
        )
        try:
            payload = json.loads(payload_str)
            trace.record_pages(ctx.doc_id, payload, ctx.doc_name, ctx.root_structure)
        except Exception:  # noqa: BLE001
            pass
        return payload_str

    if name == "search_document":
        result_str = tool_search_document(ctx, args.get("query") or "")
        logger.info("[search_document] result: %s", result_str[:2000])
        try:
            payload = json.loads(result_str)
            for hit in payload.get("results") or []:
                page_ref = hit.get("page", "?")
                try:
                    page_num = int(str(page_ref).split("-")[0])
                except (ValueError, AttributeError):
                    page_num = 1
                trace.pages_read.append({
                    "doc_id": ctx.doc_id,
                    "doc_name": ctx.doc_name,
                    "page": page_num,
                    "title": hit.get("title") or "",
                })
        except Exception:  # noqa: BLE001
            pass
        return result_str

    return json.dumps({"error": f"ferramenta desconhecida: {name}"})


def run_agent(
    question: str,
    doc_ctxs: dict[str, DocContext],
    user_data: dict | None = None,
    profile: dict | None = None,
    model: str | None = None,
    history: list[dict] | None = None,
) -> AgentResult:
    """Roda o agente.

    `user_data` são dados que o usuário enviou JUNTO da pergunta (ex.: valores
    de uma análise de solo para cálculo) — fazem parte do pedido e podem ser
    citados livremente.

    `profile` são metadados de configuração da conta (estado, município, bioma,
    cultura, unidade). São contexto ambiente: servem para desempatar variações
    do documento, e o modelo é instruído a nunca mencioná-los. Antes os dois
    iam juntos no fim da mensagem do usuário, e o modelo passou a tratar o
    perfil como parte da pergunta — respondendo coisas como "não há informação
    sobre o estado de São Paulo, o município de Marília ou o bioma Mata
    Atlântica", que é ruído de configuração vazando para o usuário.
    """
    used_model = model or settings.agent_model
    trace = RetrievalTrace()

    system_prompt = AGENT_SYSTEM + _profile_block(profile)

    user_block = question.strip()
    if user_data:
        data_lines = "\n".join(f"- {k}: {v}" for k, v in user_data.items())
        user_block += f"\n\nDados fornecidos pelo usuário:\n{data_lines}"

    catalog = _catalog_payload(doc_ctxs)

    pre_search_blocks: list[str] = []
    keywords = _extract_keywords(question)
    for ctx in doc_ctxs.values():
        for kw in keywords:
            result_str = tool_search_document(ctx, kw)
            try:
                payload = json.loads(result_str)
                hits = payload.get("results") or []
                if not hits:
                    continue
                for hit in hits:
                    page_ref = hit.get("page", "?")
                    try:
                        page_num = int(str(page_ref).split("-")[0])
                    except (ValueError, AttributeError):
                        page_num = 1
                    trace.pages_read.append({
                        "doc_id": ctx.doc_id,
                        "doc_name": ctx.doc_name,
                        "page": page_num,
                        "title": hit.get("title") or "",
                    })
                block = f"[Busca por '{kw}' em '{ctx.doc_name}' (doc_id={ctx.doc_id})]:\n"
                block += "\n---\n".join(
                    f"Caminho: {h.get('section_path') or h.get('title')} | Página: {h.get('page')}\n"
                    f"Seção: {h.get('title')}\n{h.get('snippet','')}"
                    for h in hits
                )
                pre_search_blocks.append(block)
                break
            except Exception:  # noqa: BLE001
                pass

    pre_search_text = ""
    if pre_search_blocks:
        pre_search_text = (
            "\n\nResultados de busca pré-carregados (use estes trechos para responder "
            "— só chame ferramentas se precisar de mais detalhes):\n\n"
            + "\n\n".join(pre_search_blocks)
        )

    prior: list[dict] = []
    if history:
        for msg in history[-6:]:
            role = msg.get("role")
            content = msg.get("content") or ""
            if role in ("user", "assistant") and content:
                prior.append({"role": role, "content": content})

    messages: list[dict] = prior + [
        {
            "role": "user",
            "content": (
                f"Pergunta: {user_block}\n\n"
                f"Catálogo de documentos disponíveis:\n{catalog}"
                f"{pre_search_text}\n\n"
                "Use as ferramentas para navegar na árvore e ler apenas os "
                "trechos realmente relevantes antes de responder."
            ),
        }
    ]

    # Cobrança de leitura feita? Só uma vez, para não entrar em laço.
    nudged_to_read = False

    for step in range(settings.runtime_get("AGENT_MAX_TOOL_CALLS", settings.AGENT_MAX_TOOL_CALLS)):
        msg = tool_complete(
            messages=messages,
            tools=TOOLS_SCHEMA,
            model=used_model,
            system=system_prompt,
        )
        tool_calls = getattr(msg, "tool_calls", None) or []

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
            # Toda afirmação precisa de fonte rastreável. Em pergunta panorâmica
            # ("o que fala neste documento?") o modelo tende a responder só com
            # os títulos e resumos da árvore, sem chamar get_page_content — e
            # então não existe página para citar, a resposta sai sem fonte e os
            # marcadores viram links mortos.
            #
            # A regra vive aqui e não só no prompt porque instrução de prompt o
            # modelo ignora quando "já sabe" a resposta. Cobramos UMA vez; se
            # ainda assim ele não ler nada, aceitamos para não travar a consulta.
            if not trace.pages_read and not nudged_to_read:
                nudged_to_read = True
                messages.append({
                    "role": "user",
                    "content": (
                        "Você respondeu sem abrir nenhuma página, então não há "
                        "fonte rastreável. Chame get_page_content em pelo menos "
                        "uma página que embase os pontos principais e depois "
                        "responda citando [doc_id:página]."
                    ),
                })
                continue
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
            logger.info("[agent step=%d] output: %s", step, tool_output[:2000])

            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": tool_output,
            })

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
        system=system_prompt,
    )
    return AgentResult(
        answer=final_msg.content or "Não foi possível concluir com o orçamento disponível.",
        trace=trace,
        model_used=used_model,
    )
