"""Normalização de tags — unitário, sem banco.

`POST /knowledge` recebe tags por multipart, que só transporta string, então a
função tem de aceitar JSON, CSV e lista nativa (o `PATCH` manda JSON de verdade).
"""
import pytest

from api.routes.knowledge import _MAX_TAGS, _MAX_TAG_LEN, _normalize_tags


@pytest.mark.parametrize(
    "raw,expected",
    [
        # JSON (o que o TagInput do frontend envia)
        ('["solo","soja"]', ["solo", "soja"]),
        ("[]", []),
        # CSV (digitação manual / curl)
        ("solo, soja", ["solo", "soja"]),
        ("solo,,soja", ["solo", "soja"]),
        ("  solo  ,  soja  ", ["solo", "soja"]),
        # lista nativa (PATCH com JSON body)
        (["solo", "soja"], ["solo", "soja"]),
        (("solo", "soja"), ["solo", "soja"]),
        # escalar
        ("unica", ["unica"]),
        # vazios
        ("", []),
        ("   ", []),
        (None, []),
        ([], []),
        # tipos inesperados não explodem
        (123, []),
        (object(), []),
    ],
)
def test_formatos_aceitos(raw, expected):
    assert _normalize_tags(raw) == expected


def test_dedup_case_insensitive_preserva_primeira_grafia():
    assert _normalize_tags(["Solo", "solo", "SOLO", "Soja"]) == ["Solo", "Soja"]


def test_objeto_json_nao_vira_tag_lixo():
    """Um dict serializado viraria a "tag" `{'nao': 'lista'}` se caísse no
    caminho de escalar."""
    assert _normalize_tags('{"nao":"lista"}') == []


def test_cap_de_quantidade():
    out = _normalize_tags([f"tag{i}" for i in range(_MAX_TAGS + 20)])
    assert len(out) == _MAX_TAGS
    assert out[0] == "tag0"


def test_cap_de_tamanho():
    out = _normalize_tags(["x" * (_MAX_TAG_LEN + 50)])
    assert len(out) == 1
    assert len(out[0]) == _MAX_TAG_LEN


def test_truncar_nao_gera_duplicata_silenciosa():
    """Duas tags que só diferem depois do corte colapsam em uma — o dedup roda
    sobre o valor JÁ truncado, então não sobra par idêntico."""
    a = "x" * _MAX_TAG_LEN + "AAA"
    b = "x" * _MAX_TAG_LEN + "BBB"
    assert _normalize_tags([a, b]) == ["x" * _MAX_TAG_LEN]


def test_valores_nulos_dentro_da_lista_json():
    # json.loads devolve None; str(None) == 'None' entra como tag — documentado
    # aqui para que a decisão seja explícita.
    assert _normalize_tags("[null]") == ["None"]


def test_ordem_de_entrada_e_preservada():
    assert _normalize_tags(["z", "a", "m"]) == ["z", "a", "m"]
