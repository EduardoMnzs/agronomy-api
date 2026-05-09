import logging
from contextlib import asynccontextmanager
from pathlib import Path

from arq.connections import RedisSettings, create_pool
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from sqlalchemy import text
from starlette.middleware.base import BaseHTTPMiddleware
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from api.rate_limit import limiter, rate_limit_exceeded_handler
from api.routes import access_requests, auth, conversations, documents, knowledge, my_documents, query, search as search_routes, settings as settings_routes, users
from core.config import settings
from db.models import KnowledgeDocument
from db.session import SessionLocal, engine

logger = logging.getLogger(__name__)


def _backfill_file_sizes() -> None:
    from core.config import settings
    if settings.STORAGE_BACKEND == "s3":
        return  # size is always captured at upload time for S3

    db = SessionLocal()
    try:
        rows = db.query(KnowledgeDocument).filter(
            KnowledgeDocument.file_size_bytes.is_(None)
        ).all()
        dirty = False
        for doc in rows:
            if not doc.file_path:
                continue
            try:
                size = Path(doc.file_path).stat().st_size
            except OSError:
                continue
            doc.file_size_bytes = size
            dirty = True
        if dirty:
            db.commit()
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    _backfill_file_sizes()
    app.state.arq = await create_pool(RedisSettings.from_dsn(settings.REDIS_URL))
    yield
    await app.state.arq.close()


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    _csp = (
        "default-src 'self'; "
        "img-src 'self' data: blob:; "
        "style-src 'self' 'unsafe-inline'; "
        "script-src 'self'; "
        "connect-src 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'"
    )

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
        # HSTS/CSP só fazem sentido sob HTTPS (DEBUG=false implica deploy via reverse proxy TLS).
        if not settings.DEBUG:
            response.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=63072000; includeSubDomains",
            )
            response.headers.setdefault("Content-Security-Policy", self._csp)
        return response


_show_docs = settings.DEBUG

app = FastAPI(
    title=settings.APP_NAME,
    version="1.0.0",
    docs_url="/docs" if _show_docs else None,
    redoc_url="/redoc" if _show_docs else None,
    openapi_url="/openapi.json" if _show_docs else None,
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept"],
    max_age=600,
)
# Deve ser o último add_middleware (fica mais externo na cadeia) para que
# X-Forwarded-Proto do Caddy seja lido antes de qualquer outro middleware.
# Sem isso, request.url_for() gera http:// mesmo com TLS no Caddy.
# TRUSTED_PROXY_IPS configura quais proxies têm seus headers aceitos.
_proxy_trusted = settings.TRUSTED_PROXY_IPS.strip()
_trusted_hosts: list[str] | str = (
    _proxy_trusted
    if _proxy_trusted == "*"
    else [h.strip() for h in _proxy_trusted.split(",") if h.strip()]
)
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts=_trusted_hosts)

app.include_router(auth.router)
app.include_router(knowledge.router)
app.include_router(conversations.router)
app.include_router(documents.router)
app.include_router(my_documents.router)
app.include_router(query.router)
app.include_router(users.router)
app.include_router(settings_routes.router)
app.include_router(search_routes.router)
app.include_router(access_requests.router)


@app.get("/health", tags=["infra"])
def health():
    return {"status": "ok", "app": settings.APP_NAME}


@app.get("/healthz/ready", tags=["infra"])
async def readiness(request: Request):
    checks: dict[str, dict] = {}
    overall_ok = True

    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        checks["database"] = {"ok": True}
    except Exception as exc:  # noqa: BLE001
        overall_ok = False
        checks["database"] = {"ok": False, "error": type(exc).__name__}
        logger.warning("readiness: DB check failed: %s", exc)

    arq_pool = getattr(request.app.state, "arq", None)
    if arq_pool is None:
        overall_ok = False
        checks["redis"] = {"ok": False, "error": "arq pool not initialized"}
    else:
        try:
            await arq_pool.ping()
            checks["redis"] = {"ok": True}
        except Exception as exc:  # noqa: BLE001
            overall_ok = False
            checks["redis"] = {"ok": False, "error": type(exc).__name__}
            logger.warning("readiness: Redis check failed: %s", exc)

    status_code = 200 if overall_ok else 503
    return JSONResponse(
        status_code=status_code,
        content={"status": "ready" if overall_ok else "not_ready", "checks": checks},
    )
