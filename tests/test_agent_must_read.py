"""O agente não pode responder sem ter aberto pelo menos uma página.

Em pergunta panorâmica ("o que fala neste documento?") o modelo tende a responder
só com títulos e resumos da árvore. Sem página lida não existe fonte rastreável,
a resposta sai sem citação e o usuário não tem como conferir nada.

A regra é cobrada no LOOP, não apenas no prompt: instrução de prompt o modelo
ignora quando "já sabe" a resposta. Cobramos uma única vez, para não travar a
consulta se ele insistir.
"""
import pytest

from core import agent as agent_mod


class _Msg:
    """Mesma forma do retorno de core.llm.tool_complete."""
    def __init__(self, content="", tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []


class _Call:
    def __init__(self, name, args='{}', cid="c1"):
        self.id = cid
        self.type = "function"
        self.function = type("F", (), {"name": name, "arguments": args})()


@pytest.fixture()
def roteiro(monkeypatch):
    """Encadeia respostas pré-definidas de tool_complete e registra as chamadas."""
    estado = {"i": 0, "respostas": [], "user_msgs": []}

    def fake(*, model, system, messages, tools, **kwargs):
        # guarda as mensagens 'user' vistas, para checar a cobrança
        estado["user_msgs"] = [
            m.get("content") or "" for m in messages if m.get("role") == "user"
        ]
        i = estado["i"]
        estado["i"] += 1
        respostas = estado["respostas"]
        return respostas[i] if i < len(respostas) else _Msg("resposta tardia")

    monkeypatch.setattr(agent_mod, "tool_complete", fake)
    return estado


def _run():
    return agent_mod.run_agent(
        question="O que fala neste documento?",
        doc_ctxs={},
        model="fake/model",
    )


def test_resposta_sem_pagina_lida_e_cobrada(roteiro):
    """1a tentativa: responde direto. Deve ser cobrado e tentar de novo."""
    roteiro["respostas"] = [
        _Msg("Fala de manejo de soja."),   # sem tool call, sem página lida
        _Msg("Agora com fonte [6:30]."),   # 2a tentativa
    ]

    result = _run()

    assert roteiro["i"] == 2, "deveria ter havido uma segunda chamada ao modelo"
    cobranca = [m for m in roteiro["user_msgs"] if "sem abrir nenhuma página" in m]
    assert cobranca, "a cobrança de leitura não foi enviada"
    assert "get_page_content" in cobranca[0]
    assert result.answer == "Agora com fonte [6:30]."


def test_cobranca_acontece_uma_unica_vez(roteiro):
    """Se o modelo insistir em não ler, aceitamos — não podemos travar a consulta."""
    roteiro["respostas"] = [
        _Msg("Sem fonte."),
        _Msg("Ainda sem fonte."),
        _Msg("Nunca deveria chegar aqui."),
    ]

    result = _run()

    assert roteiro["i"] == 2, "não pode cobrar mais de uma vez"
    assert result.answer == "Ainda sem fonte."


def test_nao_cobra_quando_paginas_foram_lidas(monkeypatch, roteiro):
    """Com página lida já existe fonte possível; não deve haver cobrança.

    Primeira volta chama get_page_content, segunda responde.
    """
    roteiro["respostas"] = [
        _Msg("vou ler", [_Call("get_page_content", '{"doc_id":"6","pages":"30"}')]),
        _Msg("Resposta com fonte [6:30]."),
    ]

    def fake_dispatch(name, args, doc_ctxs, trace):
        trace.pages_read.append(
            {"doc_id": "6", "doc_name": "Doc", "page": 30, "title": "secao"}
        )
        return '{"pages":[]}'

    monkeypatch.setattr(agent_mod, "_dispatch_tool", fake_dispatch)

    result = _run()

    assert roteiro["i"] == 2, "uma volta para ler, outra para responder"
    cobranca = [m for m in roteiro["user_msgs"] if "sem abrir nenhuma página" in m]
    assert not cobranca, "não deveria cobrar: página já havia sido lida"
    assert result.answer == "Resposta com fonte [6:30]."


def test_prompt_exige_leitura_explicitamente():
    """A instrução também vive no prompt, como reforço da regra do loop."""
    assert "OBRIGATÓRIO" in agent_mod.AGENT_SYSTEM
    assert "get_page_content em pelo menos UMA página" in agent_mod.AGENT_SYSTEM


def test_prompt_avisa_que_marcador_sem_pagina_e_descartado():
    assert "NÃO é citação válida" in agent_mod.AGENT_SYSTEM
