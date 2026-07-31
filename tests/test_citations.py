"""Citações: só `[doc_id:página]` vale; marcador sem página é removido.

Dois defeitos corrigidos aqui.

1. MARCADOR MORTO. Em pergunta panorâmica o agente respondia a partir dos títulos
   e resumos da árvore, sem chamar get_page_content. Sem página, escrevia `[6]`
   (só o doc). O regex de citação exige `[id:pag]`, então nenhuma fonte era
   gerada — mas o `[6]` continuava visível na resposta, virando link que não abre
   nada. Foi o que apareceu em produção: 7 marcadores no texto, painel de fontes
   vazio.

2. CITAÇÃO ERRADA (mais grave). Havia um fallback que lia `[N]` como índice
   1-based em `pages_read` — convenção do pipeline antigo, baseado em nós. Com o
   agente atual usando doc_id, as duas colidem: o agente escreve `[6]` para
   "documento 6" e o fallback devolvia a 6ª página lida. Citação errada com
   aparência de correta.
"""
import pytest

from core.citations import extract_inline_citations


def _pages(doc_id="6", pages=(21, 22, 23, 24, 25, 26, 27, 28), doc_name="Prescrição"):
    return [
        {"doc_id": doc_id, "doc_name": doc_name, "page": p, "title": f"secao {p}"}
        for p in pages
    ]


# ── formato válido ────────────────────────────────────────────────────────────

def test_cita_pagina_gera_fonte_e_renumera():
    out, src = extract_inline_citations(
        "A dose é 2 L/ha [6:30].", _pages(pages=(30,)), valid_doc_ids={"6"}
    )
    assert out == "A dose é 2 L/ha [1]."
    assert len(src) == 1
    assert (src[0].doc_id, src[0].page, src[0].section) == ("6", 30, "secao 30")


def test_mesma_pagina_citada_duas_vezes_vira_um_ref():
    out, src = extract_inline_citations(
        "Vale [6:30] e também [6:30].", _pages(pages=(30,)), valid_doc_ids={"6"}
    )
    assert out == "Vale [1] e também [1]."
    assert len(src) == 1


def test_paginas_distintas_numeram_na_ordem_de_aparicao():
    out, src = extract_inline_citations(
        "Primeiro [6:28], depois [6:22].", _pages(), valid_doc_ids={"6"}
    )
    assert out == "Primeiro [1], depois [2]."
    assert [(s.ref, s.page) for s in src] == [(1, 28), (2, 22)]


def test_doc_id_com_prefixo_de_sessao():
    pages = [{"doc_id": "session_5", "doc_name": "Laudo", "page": 3, "title": "t"}]
    out, src = extract_inline_citations("Ver [session_5:3].", pages, valid_doc_ids={"session_5"})
    assert out == "Ver [1]."
    assert src[0].doc_id == "session_5"


# ── defeito 1: marcador sem página ────────────────────────────────────────────

def test_marcador_sem_pagina_e_removido_do_texto():
    """`[6]` não tem âncora; não pode sobrar como link morto."""
    out, src = extract_inline_citations(
        "Aborda manejo de soja [6]. Inclui sementes [6].", [], valid_doc_ids={"6"}
    )
    assert "[6]" not in out
    assert out == "Aborda manejo de soja. Inclui sementes."
    assert src == []


def test_marcador_sem_pagina_removido_mesmo_com_paginas_lidas():
    """Aqui estava o defeito 2: com 8 páginas lidas, o `[6]` virava pág. 26."""
    out, src = extract_inline_citations("Dose citada [6].", _pages(), valid_doc_ids={"6"})
    assert out == "Dose citada."
    assert src == [], "não pode inventar fonte a partir de índice em pages_read"


def test_nao_apaga_citacao_valida_ao_limpar_as_invalidas():
    """Regressão do sentinela: a citação válida vira [1], que também casa o
    padrão de marcador sem página — se a limpeza rodasse sem proteção,
    apagaria a própria fonte."""
    out, src = extract_inline_citations(
        "Valido [6:30] e invalido [6].", _pages(pages=(30,)), valid_doc_ids={"6"}
    )
    assert out == "Valido [1] e invalido."
    assert len(src) == 1


def test_mistura_de_validos_e_invalidos_em_varias_frases():
    out, src = extract_inline_citations(
        "Um [6:21]. Dois [6]. Tres [6:22]. Quatro [7].",
        _pages(), valid_doc_ids={"6"},
    )
    assert out == "Um [1]. Dois. Tres [2]. Quatro."
    assert [s.page for s in src] == [21, 22]


# ── doc_id fora do catálogo ───────────────────────────────────────────────────

def test_doc_id_alucinado_e_removido():
    out, src = extract_inline_citations(
        "Alegado [99:5].", _pages(), valid_doc_ids={"6"}
    )
    assert out == "Alegado."
    assert src == []


def test_sem_valid_doc_ids_aceita_qualquer_doc():
    out, src = extract_inline_citations("Ver [9:5].", [], valid_doc_ids=None)
    assert out == "Ver [1]."
    assert src[0].doc_id == "9"


def test_pagina_citada_sem_ter_sido_lida_ainda_gera_fonte():
    """Mantém a alegação visível; a seção fica vazia por não ter sido lida."""
    out, src = extract_inline_citations("Ver [6:99].", _pages(), valid_doc_ids={"6"})
    assert out == "Ver [1]."
    assert src[0].page == 99
    assert src[0].section == ""
    assert src[0].doc_name == "Prescrição"


# ── limpeza de pontuação ──────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "entrada,esperado",
    [
        ("Texto [6].", "Texto."),
        ("Texto [6] ,", "Texto,"),
        ("Texto [6] ; fim", "Texto; fim"),
        ("Texto [6]  duplo", "Texto duplo"),
        ("Certo [6:21] e errado [6]!", "Certo [1] e errado!"),
    ],
)
def test_nao_deixa_espaco_orfao(entrada, esperado):
    out, _ = extract_inline_citations(entrada, _pages(), valid_doc_ids={"6"})
    assert out == esperado


def test_texto_sem_marcador_fica_intacto():
    txt = "Resposta sem nenhuma citação."
    out, src = extract_inline_citations(txt, _pages(), valid_doc_ids={"6"})
    assert out == txt
    assert src == []


def test_colchete_que_nao_e_citacao_e_preservado():
    """Colchete com espaço ou pontuação não é marcador e não deve ser tocado."""
    txt = "Ver a nota [ver anexo] e a faixa [2,5-3,0]."
    out, _ = extract_inline_citations(txt, _pages(), valid_doc_ids={"6"})
    assert out == txt
