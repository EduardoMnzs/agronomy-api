"""Semáforo do LLM não pode ficar preso a um event loop morto.

`api/routes/documents.py` indexa inline, via o wrapper síncrono
`core.indexer.index_document` -> `asyncio.run(...)`, dentro de um processo
uvicorn de vida longa. Um `asyncio.Semaphore` instanciado no nível de módulo se
vincula ao primeiro loop que precisar bloquear; o segundo upload no mesmo
processo então estoura com "is bound to a different event loop".

O vínculo só acontece quando o semáforo REALMENTE bloqueia (`Semaphore.acquire`
só chama `_get_loop()` se precisar esperar), então os testes usam concorrência
acima do limite — com 3 tarefas nada bloquearia e o bug não apareceria.
"""
import asyncio

import pytest

from pageindex import utils as pageindex_utils


@pytest.fixture(autouse=True)
def _reset_slot():
    pageindex_utils._llm_semaphore_slot = None
    yield
    pageindex_utils._llm_semaphore_slot = None


async def _saturate(n: int) -> int:
    """Roda n tarefas concorrentes sob o semáforo e devolve o pico observado."""
    peak = 0
    current = 0

    async def one():
        nonlocal peak, current
        async with pageindex_utils._get_llm_semaphore():
            current += 1
            peak = max(peak, current)
            await asyncio.sleep(0.005)
            current -= 1

    await asyncio.gather(*(one() for _ in range(n)))
    return peak


def test_jobs_sequenciais_em_loops_distintos():
    """O caso que quebrava: vários `asyncio.run` no mesmo processo."""
    for _ in range(15):
        asyncio.run(_saturate(8))


def test_limite_de_concorrencia_e_respeitado():
    peak = asyncio.run(_saturate(20))
    assert peak == pageindex_utils._LLM_CONCURRENCY


def test_limite_vale_de_novo_no_loop_seguinte():
    assert asyncio.run(_saturate(20)) == pageindex_utils._LLM_CONCURRENCY
    assert asyncio.run(_saturate(20)) == pageindex_utils._LLM_CONCURRENCY


def test_memoria_nao_cresce_por_job():
    """Um dict por loop cresceria para sempre — o semáforo guarda `_loop`
    apontando para o loop, então nem WeakKeyDictionary liberaria a entrada.
    O cache é de slot único."""
    for _ in range(30):
        asyncio.run(_saturate(8))

    slot = pageindex_utils._llm_semaphore_slot
    assert slot is not None
    assert len(slot) == 2, "o slot guarda exatamente (loop, semáforo)"


def test_mesmo_loop_reusa_o_mesmo_semaforo():
    async def main():
        a = pageindex_utils._get_llm_semaphore()
        b = pageindex_utils._get_llm_semaphore()
        return a is b

    assert asyncio.run(main()) is True


def test_loop_novo_recebe_semaforo_novo():
    async def grab():
        return pageindex_utils._get_llm_semaphore()

    first = asyncio.run(grab())
    second = asyncio.run(grab())
    assert first is not second
