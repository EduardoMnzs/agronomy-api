"""Rate limiting via slowapi com storage Redis (fallback memory)."""
from __future__ import annotations

import logging

from fastapi import Request
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.responses import JSONResponse

from core.config import settings

logger = logging.getLogger(__name__)


def _key_func(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return get_remote_address(request)


_storage_uri = settings.REDIS_URL if settings.REDIS_URL else "memory://"

limiter = Limiter(
    key_func=_key_func,
    storage_uri=_storage_uri,
    default_limits=[],
    headers_enabled=False,
    # Se o storage (Redis) cair, desliga rate-limit em vez de propagar 500.
    swallow_errors=True,
    strategy="fixed-window",
)


def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    return JSONResponse(
        status_code=429,
        content={"detail": "Muitas requisições. Tente novamente em alguns instantes."},
        headers={"Retry-After": str(int(getattr(exc, "retry_after", 60) or 60))},
    )
