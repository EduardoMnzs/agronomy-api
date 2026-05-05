from contextlib import asynccontextmanager
from pathlib import Path

from arq.connections import RedisSettings, create_pool
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from api.routes import auth, conversations, documents, knowledge, query, users
from core.config import settings
from db.models import Base, KnowledgeDocument
from db.session import SessionLocal, engine

Base.metadata.create_all(bind=engine)


def _bootstrap_schema() -> None:
    with engine.begin() as conn:
        conn.execute(text(
            "ALTER TABLE knowledge_documents "
            "ADD COLUMN IF NOT EXISTS file_size_bytes BIGINT"
        ))


_bootstrap_schema()


def _backfill_file_sizes() -> None:
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


app = FastAPI(
    title=settings.APP_NAME,
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(knowledge.router)
app.include_router(conversations.router)
app.include_router(documents.router)
app.include_router(query.router)
app.include_router(users.router)


@app.get("/health")
def health():
    return {"status": "ok", "app": settings.APP_NAME}
