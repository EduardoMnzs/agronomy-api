"""Chaves de storage precisam usar '/' sempre.

A API pode rodar em host Windows (uvicorn local) enquanto o worker roda em
container Linux. Uma chave gravada como `data\\knowledge\\files\\x.pdf` não
resolve do outro lado.
"""
from pathlib import PureWindowsPath

from core import storage as store


# Chave como um host Windows a grava.
_WIN_KEY = "data" + chr(92) + "knowledge" + chr(92) + "files" + chr(92) + "x.pdf"


def test_normalize_key_troca_backslash():
    """Asserção no nível da string, não do Path.

    No Windows, `Path("a\\b")` já separa os componentes e mascararia a falta da
    normalização — um teste via `Path.as_posix()` passaria mesmo com o bug
    presente. É exatamente o container Linux (onde '\\' é nome de arquivo
    válido) que quebra, então o invariante tem de ser verificado na string.
    """
    assert store.normalize_key(_WIN_KEY) == "data/knowledge/files/x.pdf"
    assert chr(92) not in store.normalize_key(_WIN_KEY)


def test_normalize_key_idempotente():
    once = store.normalize_key(_WIN_KEY)
    assert store.normalize_key(once) == once


def test_normalize_key_aceita_path_e_str():
    assert store.normalize_key(PureWindowsPath("data/k/x.pdf")) == "data/k/x.pdf"
    assert store.normalize_key("data/k/x.pdf") == "data/k/x.pdf"


def test_local_storage_normaliza_backslash():
    s = store._LocalStorage("data")
    assert s._p(_WIN_KEY).as_posix() == "data/knowledge/files/x.pdf"


def test_local_storage_aceita_forward_slash_inalterado():
    s = store._LocalStorage("data")
    assert s._p("data/knowledge/files/x.pdf").as_posix() == "data/knowledge/files/x.pdf"


def test_local_storage_nao_duplica_prefixo_base():
    s = store._LocalStorage("data")
    # chave que já inclui o diretório base não deve virar data/data/...
    assert s._p("data/knowledge/x.pdf").as_posix() == "data/knowledge/x.pdf"


def test_finalize_to_storage_retorna_forward_slash(monkeypatch):
    from core.config import settings

    monkeypatch.setattr(settings, "STORAGE_BACKEND", "local", raising=False)
    out = store.finalize_to_storage(PureWindowsPath("data/knowledge/indexes/x.json"))
    assert chr(92) not in out
    assert out == "data/knowledge/indexes/x.json"


def test_to_key_normaliza_backslash():
    win = "data" + chr(92) + "knowledge" + chr(92) + "files" + chr(92) + "y.pdf"
    assert chr(92) not in store._to_key(win)
