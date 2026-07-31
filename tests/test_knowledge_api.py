"""Integração das rotas de /knowledge: filtros server-side, /tags e PATCH.

Antes, `GET /knowledge` ignorava todos os query params e devolvia a base inteira
para o frontend filtrar em memória.
"""
import pytest

from db.models import DocumentCategory, IndexStatus, KnowledgeDocument, UserRole


@pytest.fixture()
def docs(db, make_user):
    admin = make_user(email="admin@test.com", role=UserRole.admin, full_name="Ana Souza")
    rows = [
        ("Manual de Solo", DocumentCategory.solo, ["safra-24", "Latossolo"], "analise de solo"),
        ("Catalogo Sementes", DocumentCategory.sementes, ["safra-24", "soja"], "cultivares"),
        ("Guia Herbicidas", DocumentCategory.herbicidas, [], "controle de daninhas"),
    ]
    created = []
    for name, cat, tags, desc in rows:
        d = KnowledgeDocument(
            name=name,
            original_filename=f"{name}.pdf",
            file_type="pdf",
            file_path=f"knowledge/files/{name}.pdf",
            index_path=f"knowledge/indexes/{name}.json",
            category=cat,
            tags=tags,
            description=desc,
            indexed_by=admin.id,
            status=IndexStatus.done,
        )
        db.add(d)
        created.append(d)
    db.commit()
    for d in created:
        db.refresh(d)
    return {"admin": admin, "docs": created}


def _names(resp):
    assert resp.status_code == 200, resp.text
    return sorted(d["name"] for d in resp.json())


# ── listagem e filtros ────────────────────────────────────────────────────────

def test_lista_todos_sem_filtro(client, docs, auth_as):
    auth_as(docs["admin"])
    assert _names(client.get("/knowledge")) == [
        "Catalogo Sementes", "Guia Herbicidas", "Manual de Solo",
    ]


def test_busca_por_nome(client, docs, auth_as):
    auth_as(docs["admin"])
    assert _names(client.get("/knowledge", params={"search": "solo"})) == ["Manual de Solo"]


def test_busca_e_case_insensitive(client, docs, auth_as):
    auth_as(docs["admin"])
    assert _names(client.get("/knowledge", params={"search": "SOLO"})) == ["Manual de Solo"]


def test_busca_por_descricao(client, docs, auth_as):
    auth_as(docs["admin"])
    assert _names(client.get("/knowledge", params={"search": "daninhas"})) == ["Guia Herbicidas"]


def test_busca_por_nome_de_arquivo(client, docs, auth_as):
    auth_as(docs["admin"])
    assert _names(client.get("/knowledge", params={"search": "Sementes.pdf"})) == ["Catalogo Sementes"]


def test_busca_sem_resultado(client, docs, auth_as):
    auth_as(docs["admin"])
    assert _names(client.get("/knowledge", params={"search": "zzzz"})) == []


def test_filtro_por_uma_categoria(client, docs, auth_as):
    auth_as(docs["admin"])
    assert _names(client.get("/knowledge", params={"category": "solo"})) == ["Manual de Solo"]


def test_filtro_por_multiplas_categorias(client, docs, auth_as):
    """Multi-seleção vai como param repetido; antes só o primeiro era usado."""
    auth_as(docs["admin"])
    resp = client.get("/knowledge", params=[("category", "solo"), ("category", "sementes")])
    assert _names(resp) == ["Catalogo Sementes", "Manual de Solo"]


def test_filtro_por_tag(client, docs, auth_as):
    auth_as(docs["admin"])
    resp = client.get("/knowledge", params={"tags": "safra-24"})
    assert _names(resp) == ["Catalogo Sementes", "Manual de Solo"]


def test_filtro_por_multiplas_tags_e_overlap(client, docs, auth_as):
    """Tags são OR (overlap), não AND."""
    auth_as(docs["admin"])
    resp = client.get("/knowledge", params=[("tags", "soja"), ("tags", "Latossolo")])
    assert _names(resp) == ["Catalogo Sementes", "Manual de Solo"]


def test_tag_inexistente_nao_traz_nada(client, docs, auth_as):
    auth_as(docs["admin"])
    assert _names(client.get("/knowledge", params={"tags": "inexistente"})) == []


def test_filtros_combinados_sao_and(client, docs, auth_as):
    auth_as(docs["admin"])
    # 'soja' só existe no Catalogo; search 'solo' só casa Manual -> interseção vazia
    resp = client.get("/knowledge", params=[("search", "solo"), ("tags", "soja")])
    assert _names(resp) == []
    # agora coerentes
    resp = client.get("/knowledge", params=[("search", "Catalogo"), ("tags", "soja")])
    assert _names(resp) == ["Catalogo Sementes"]


def test_params_vazios_sao_ignorados(client, docs, auth_as):
    auth_as(docs["admin"])
    resp = client.get("/knowledge", params=[("search", "  "), ("category", ""), ("tags", " ")])
    assert len(resp.json()) == 3


# ── payload ───────────────────────────────────────────────────────────────────

def test_payload_traz_tags_e_indexed_by_name(client, docs, auth_as):
    auth_as(docs["admin"])
    by_name = {d["name"]: d for d in client.get("/knowledge").json()}
    assert by_name["Manual de Solo"]["tags"] == ["safra-24", "Latossolo"]
    assert by_name["Manual de Solo"]["indexed_by_name"] == "Ana Souza"
    assert by_name["Guia Herbicidas"]["tags"] == []


def test_indexed_by_name_cai_para_email_sem_nome(client, db, make_user, auth_as):
    u = make_user(email="sem.nome@test.com", full_name="   ")
    db.add(KnowledgeDocument(
        name="D", original_filename="d.pdf", file_type="pdf",
        file_path="k/d.pdf", category=DocumentCategory.outro,
        indexed_by=u.id, status=IndexStatus.done,
    ))
    db.commit()
    auth_as(u)
    assert client.get("/knowledge").json()[0]["indexed_by_name"] == "sem.nome@test.com"


def test_indexed_by_name_nulo_sem_autor(client, db, make_user, auth_as):
    u = make_user()
    db.add(KnowledgeDocument(
        name="Orfao", original_filename="o.pdf", file_type="pdf",
        file_path="k/o.pdf", category=DocumentCategory.outro,
        indexed_by=None, status=IndexStatus.done,
    ))
    db.commit()
    auth_as(u)
    assert client.get("/knowledge").json()[0]["indexed_by_name"] is None


# ── /tags ─────────────────────────────────────────────────────────────────────

def test_lista_tags_distintas_ordenadas(client, docs, auth_as):
    auth_as(docs["admin"])
    resp = client.get("/knowledge/tags")
    assert resp.status_code == 200
    # ordenação case-insensitive; 'safra-24' aparece em 2 docs mas sai uma vez
    assert resp.json() == ["Latossolo", "safra-24", "soja"]


def test_tags_vazio_quando_nao_ha_tag(client, db, make_user, auth_as):
    u = make_user()
    db.add(KnowledgeDocument(
        name="D", original_filename="d.pdf", file_type="pdf",
        file_path="k/d.pdf", category=DocumentCategory.outro,
        indexed_by=u.id, status=IndexStatus.done,
    ))
    db.commit()
    auth_as(u)
    assert client.get("/knowledge/tags").json() == []


def test_rota_tags_nao_e_capturada_por_doc_id(client, docs, auth_as):
    """`/knowledge/tags` está declarada antes de `/knowledge/{doc_id}`."""
    auth_as(docs["admin"])
    assert isinstance(client.get("/knowledge/tags").json(), list)


# ── PATCH ─────────────────────────────────────────────────────────────────────

def test_patch_renomeia(client, docs, auth_as):
    auth_as(docs["admin"])
    doc_id = docs["docs"][0].id
    resp = client.patch(f"/knowledge/{doc_id}", json={"name": "  Manual de Solo v2  "})
    assert resp.status_code == 200
    assert resp.json()["name"] == "Manual de Solo v2"


def test_patch_edita_tags_com_normalizacao(client, docs, auth_as):
    auth_as(docs["admin"])
    doc_id = docs["docs"][0].id
    resp = client.patch(f"/knowledge/{doc_id}", json={"tags": ["  A  ", "a", "B", ""]})
    assert resp.status_code == 200
    assert resp.json()["tags"] == ["A", "B"]


def test_patch_nome_vazio_rejeitado(client, docs, auth_as):
    auth_as(docs["admin"])
    doc_id = docs["docs"][0].id
    assert client.patch(f"/knowledge/{doc_id}", json={"name": "   "}).status_code == 400


def test_patch_campo_ausente_nao_altera(client, docs, auth_as):
    auth_as(docs["admin"])
    doc = docs["docs"][0]
    resp = client.patch(f"/knowledge/{doc.id}", json={"name": "Novo Nome"})
    assert resp.status_code == 200
    assert resp.json()["tags"] == ["safra-24", "Latossolo"], "tags não deveriam mudar"


def test_patch_lista_vazia_limpa_tags(client, docs, auth_as):
    auth_as(docs["admin"])
    doc = docs["docs"][0]
    resp = client.patch(f"/knowledge/{doc.id}", json={"tags": []})
    assert resp.status_code == 200
    assert resp.json()["tags"] == []


def test_patch_documento_inexistente(client, docs, auth_as):
    auth_as(docs["admin"])
    assert client.patch("/knowledge/999999", json={"name": "x"}).status_code == 404


def test_patch_exige_admin(client, docs, make_user, auth_as):
    comum = make_user(email="user@test.com", role=UserRole.user, full_name="Bia")
    auth_as(comum)
    doc_id = docs["docs"][0].id
    assert client.patch(f"/knowledge/{doc_id}", json={"name": "x"}).status_code == 403


def test_patch_reflete_na_listagem_e_nas_tags(client, docs, auth_as):
    auth_as(docs["admin"])
    doc_id = docs["docs"][2].id  # Guia Herbicidas, sem tags
    client.patch(f"/knowledge/{doc_id}", json={"tags": ["nova-tag"]})

    assert "nova-tag" in client.get("/knowledge/tags").json()
    assert _names(client.get("/knowledge", params={"tags": "nova-tag"})) == ["Guia Herbicidas"]
