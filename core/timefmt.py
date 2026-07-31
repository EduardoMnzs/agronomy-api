"""Serialização de timestamps para o JSON da API.

Todas as colunas DateTime do projeto guardam UTC INGÊNUO — o padrão é
``datetime.now(tz=timezone.utc).replace(tzinfo=None)`` (ver db/models.py).

Chamar ``.isoformat()`` direto produz ``2026-07-31T01:35:22`` sem marcador de
fuso. O navegador, por especificação, interpreta ISO date-time SEM fuso como
HORA LOCAL — então um upload feito em 30/07 às 22:35 (BRT) aparecia como
31/07 às 01:35 na interface, três horas adiantado e no dia seguinte.

`utc_iso` acrescenta o 'Z' para que o cliente saiba que é UTC e converta para o
fuso do usuário. Centralizado aqui porque metade das rotas fazia
``.isoformat() + "Z"`` e a outra metade esquecia, gerando datas divergentes
entre telas para o mesmo registro.
"""
from __future__ import annotations

from datetime import datetime, timezone


def utc_iso(value: datetime | None) -> str | None:
    """ISO 8601 marcado como UTC, ou None.

    Aceita datetime ingênuo (assumido UTC, o caso do projeto) e também
    tz-aware, convertendo para UTC antes de formatar — assim um `default`
    que venha a ser trocado para tz-aware no futuro não passa a emitir
    offset duplicado.
    """
    if value is None:
        return None
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value.isoformat() + "Z"
