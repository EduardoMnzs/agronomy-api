"""Timestamps da API precisam ser marcados como UTC.

Todas as colunas DateTime guardam UTC ingênuo. Serializar com `.isoformat()`
puro produz "2026-07-31T01:35:22" — sem fuso. O navegador, por especificação,
interpreta ISO date-time SEM fuso como HORA LOCAL. Efeito em produção: um upload
feito em 30/07 às 22:35 (BRT, UTC-3) aparecia na interface como 31/07 às 01:35 —
três horas adiantado e no DIA SEGUINTE.

Metade das rotas fazia `.isoformat() + "Z"` e a outra metade esquecia, então o
mesmo registro mostrava datas diferentes em telas diferentes.
"""
from datetime import datetime, timedelta, timezone

import pytest

from core.timefmt import utc_iso


def test_none_devolve_none():
    assert utc_iso(None) is None


def test_naive_recebe_marcador_z():
    dt = datetime(2026, 7, 31, 1, 35, 22, 231316)
    assert utc_iso(dt) == "2026-07-31T01:35:22.231316Z"


def test_marcador_z_esta_presente_sempre():
    assert utc_iso(datetime(2026, 1, 1)).endswith("Z")


def test_tz_aware_e_convertido_para_utc_sem_offset_duplicado():
    """Se um default virar tz-aware no futuro, não pode sair '+00:00Z'."""
    br = timezone(timedelta(hours=-3))
    dt = datetime(2026, 7, 30, 22, 35, 22, tzinfo=br)
    out = utc_iso(dt)
    assert out == "2026-07-31T01:35:22Z"
    assert "+" not in out
    assert out.count("Z") == 1


def test_utc_aware_nao_muda_o_instante():
    dt = datetime(2026, 7, 31, 1, 35, 22, tzinfo=timezone.utc)
    assert utc_iso(dt) == "2026-07-31T01:35:22Z"


def test_o_dia_muda_ao_converter_para_brasilia():
    """O caso concreto que motivou a correção."""
    gravado = datetime(2026, 7, 31, 1, 35, 22)          # UTC ingênuo no banco
    br = timezone(timedelta(hours=-3))

    # sem marcador, o cliente assume local e mostra 31/07
    sem_marcador = gravado                                # interpretado como local
    assert sem_marcador.strftime("%d/%m/%Y") == "31/07/2026"

    # com marcador, o cliente converte e mostra 30/07
    iso = utc_iso(gravado)
    convertido = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(br)
    assert convertido.strftime("%d/%m/%Y") == "30/07/2026"
    assert convertido.strftime("%H:%M") == "22:35"


# ── contrato das rotas ────────────────────────────────────────────────────────

def test_knowledge_indexed_at_vem_marcado(client, db, make_user, auth_as):
    from db.models import DocumentCategory, IndexStatus, KnowledgeDocument

    u = make_user()
    db.add(KnowledgeDocument(
        name="D", original_filename="d.pdf", file_type="pdf", file_path="k/d.pdf",
        category=DocumentCategory.outro, indexed_by=u.id, status=IndexStatus.done,
        indexed_at=datetime(2026, 7, 31, 1, 35, 22),
    ))
    db.commit()
    auth_as(u)

    got = client.get("/knowledge").json()[0]["indexed_at"]
    assert got == "2026-07-31T01:35:22Z", "sem o Z a interface mostra o dia errado"


def test_knowledge_indexed_at_nulo_continua_nulo(client, db, make_user, auth_as):
    from db.models import DocumentCategory, IndexStatus, KnowledgeDocument

    u = make_user()
    db.add(KnowledgeDocument(
        name="D", original_filename="d.pdf", file_type="pdf", file_path="k/d.pdf",
        category=DocumentCategory.outro, indexed_by=u.id, status=IndexStatus.queued,
        indexed_at=None,
    ))
    db.commit()
    auth_as(u)
    assert client.get("/knowledge").json()[0]["indexed_at"] is None


def test_users_me_marca_utc(client, db, make_user, auth_as):
    u = make_user()
    auth_as(u)
    d = client.get("/users/me").json()
    assert d["created_at"].endswith("Z")


@pytest.mark.parametrize("campo", ["created_at", "updated_at"])
def test_conversations_marcam_utc(client, db, make_user, auth_as, campo):
    import uuid
    from db.models import Conversation

    u = make_user()
    db.add(Conversation(
        id=uuid.uuid4(), user_id=u.id, title="t", messages=[],
        created_at=datetime(2026, 7, 31, 1, 0, 0),
        updated_at=datetime(2026, 7, 31, 1, 0, 0),
    ))
    db.commit()
    auth_as(u)

    items = client.get("/conversations").json()
    items = items.get("items", items) if isinstance(items, dict) else items
    assert items[0][campo].endswith("Z")
