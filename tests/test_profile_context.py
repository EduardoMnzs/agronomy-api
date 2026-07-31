"""Perfil do usuário é contexto ambiente, não parte da pergunta.

Bug em produção: `_profile_context(user)` era mesclado com `body.user_data` num
dict só e anexado ao fim da mensagem do usuário como "Dados fornecidos pelo
usuário". O modelo passou a ler o perfil como parte do pedido e respondeu:

  "...não há informações específicas sobre o estado de São Paulo, o município
   de Marília ou o bioma Mata Atlântica."

Configuração da conta vazando para a resposta. Agora o perfil vai no prompt de
SISTEMA, com regra explícita de nunca ser mencionado, e `user_data` (dados que o
usuário mandou junto da pergunta, ex.: análise de solo) continua na mensagem do
usuário, onde pode ser citado e usado em cálculo.
"""
import pytest

from core import agent as agent_mod
from core.agent import AGENT_SYSTEM, _profile_block


PERFIL = {
    "Estado": "SP",
    "Município": "Marília",
    "Bioma": "Mata Atlântica",
    "Cultura principal": "soja",
}


# ── _profile_block ────────────────────────────────────────────────────────────

def test_sem_perfil_nao_adiciona_nada():
    assert _profile_block(None) == ""
    assert _profile_block({}) == ""


def test_bloco_contem_os_valores_do_perfil():
    block = _profile_block(PERFIL)
    for value in PERFIL.values():
        assert value in block


def test_bloco_proibe_mencionar_e_proibe_comentar_ausencia():
    block = _profile_block(PERFIL)
    assert "NUNCA mencione" in block
    assert "NUNCA comente que o documento não cobre" in block
    assert "NÃO fazem parte da pergunta" in block


def test_bloco_nao_usa_o_rotulo_de_dados_do_usuario():
    """O rótulo "Dados fornecidos pelo usuário" é o que confundia o modelo:
    fazia o perfil parecer entrada do pedido."""
    assert "Dados fornecidos pelo usuário" not in _profile_block(PERFIL)


# ── run_agent: onde cada coisa é injetada ─────────────────────────────────────

class _FakeMsg:
    """Mesma forma do retorno de core.llm.tool_complete: .content e .tool_calls."""
    def __init__(self, content: str):
        self.content = content
        self.tool_calls = []


@pytest.fixture()
def captura(monkeypatch):
    """Intercepta tool_complete e guarda o system/messages da 1a chamada.

    Copia `messages` porque run_agent muta a lista ao longo do loop — sem a
    copia, a asserção veria o estado final e não o que foi enviado.
    """
    capturado = {}

    def fake_tool_complete(*, model, system, messages, tools, **kwargs):
        capturado.setdefault("system", system)
        capturado.setdefault("messages", [dict(m) for m in messages])
        return _FakeMsg("resposta final sem citacao")

    monkeypatch.setattr(agent_mod, "tool_complete", fake_tool_complete)
    return capturado


def _run(profile=None, user_data=None):
    return agent_mod.run_agent(
        question="O que fala na prescrição agronômica?",
        doc_ctxs={},
        user_data=user_data,
        profile=profile,
        model="fake/model",
    )


def test_perfil_vai_no_system_e_nao_na_mensagem_do_usuario(captura):
    _run(profile=PERFIL)

    system = captura["system"]
    user_msgs = "\n".join(
        m.get("content") or "" for m in captura["messages"] if m.get("role") == "user"
    )

    # está no system
    assert "Marília" in system and "Mata Atlântica" in system
    # e NÃO na mensagem do usuário — o ponto do bug
    assert "Marília" not in user_msgs
    assert "Mata Atlântica" not in user_msgs
    assert "SP" not in user_msgs


def test_sem_perfil_o_system_fica_igual_ao_base(captura):
    _run(profile=None)
    assert captura["system"] == AGENT_SYSTEM


def test_user_data_continua_na_mensagem_do_usuario(captura):
    """Dados enviados junto da pergunta são parte do pedido e devem ficar lá,
    porque o prompt manda aplicar fórmulas do documento sobre eles."""
    _run(user_data={"pH": "4.8", "Argila": "35%"})

    user_msgs = "\n".join(
        m.get("content") or "" for m in captura["messages"] if m.get("role") == "user"
    )
    assert "pH" in user_msgs and "4.8" in user_msgs
    assert "Dados fornecidos pelo usuário" in user_msgs


def test_perfil_e_user_data_nao_se_misturam(captura):
    _run(profile=PERFIL, user_data={"pH": "4.8"})

    system = captura["system"]
    user_msgs = "\n".join(
        m.get("content") or "" for m in captura["messages"] if m.get("role") == "user"
    )

    assert "Marília" in system and "Marília" not in user_msgs
    assert "4.8" in user_msgs and "4.8" not in system


def test_pergunta_permanece_intacta(captura):
    _run(profile=PERFIL)
    user_msgs = [m for m in captura["messages"] if m.get("role") == "user"]
    assert "O que fala na prescrição agronômica?" in (user_msgs[0].get("content") or "")


# ── a rota separa as duas coisas? ─────────────────────────────────────────────

def test_rota_passa_profile_e_user_data_separados(monkeypatch, client, db, make_user, auth_as):
    """Guarda contra a regressão de voltar a mesclar os dois num dict só."""
    from db.models import DocumentCategory, IndexStatus, KnowledgeDocument
    import api.routes.query as query_route

    u = make_user(email="agro@test.com")
    u.state = "SP"
    u.city = "Marília"
    u.biome = "Mata Atlântica"
    db.add(KnowledgeDocument(
        name="Doc", original_filename="d.pdf", file_type="pdf",
        file_path="k/d.pdf", index_path="k/d.json",
        category=DocumentCategory.outro, indexed_by=u.id, status=IndexStatus.done,
    ))
    db.commit()
    auth_as(u)

    visto = {}

    def fake_query(**kwargs):
        visto.update(kwargs)
        from core.query_engine import QueryResult
        return QueryResult(answer="ok", sources=[], model_used="fake")

    monkeypatch.setattr(query_route, "query", fake_query)

    resp = client.post("/query", json={
        "question": "o que fala no documento?",
        "scope": "kb",
        "user_data": {"pH": "4.8"},
    })
    assert resp.status_code == 200, resp.text

    assert visto["profile"] == {
        "Estado": "SP", "Município": "Marília", "Bioma": "Mata Atlântica",
    }
    assert visto["user_data"] == {"pH": "4.8"}
    # o perfil NÃO pode estar dentro de user_data
    assert "Município" not in (visto["user_data"] or {})


def test_rota_sem_perfil_manda_profile_none(monkeypatch, client, db, make_user, auth_as):
    from db.models import DocumentCategory, IndexStatus, KnowledgeDocument
    import api.routes.query as query_route

    u = make_user(email="vazio@test.com")
    db.add(KnowledgeDocument(
        name="Doc", original_filename="d.pdf", file_type="pdf",
        file_path="k/d.pdf", index_path="k/d.json",
        category=DocumentCategory.outro, indexed_by=u.id, status=IndexStatus.done,
    ))
    db.commit()
    auth_as(u)

    visto = {}

    def fake_query(**kwargs):
        visto.update(kwargs)
        from core.query_engine import QueryResult
        return QueryResult(answer="ok", sources=[], model_used="fake")

    monkeypatch.setattr(query_route, "query", fake_query)
    client.post("/query", json={"question": "q", "scope": "kb"})

    assert visto["profile"] is None
    assert visto["user_data"] is None
