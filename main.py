from contextlib import asynccontextmanager
from pathlib import Path

from arq.connections import RedisSettings, create_pool
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from api.routes import access_requests, auth, conversations, documents, knowledge, my_documents, query, search as search_routes, settings as settings_routes, users
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
        conn.execute(text("""
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'users' AND column_name = 'name'
                ) AND NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'users' AND column_name = 'full_name'
                ) THEN
                    ALTER TABLE users RENAME COLUMN name TO full_name;
                END IF;
            END $$;
        """))
        conn.execute(text("""
            DO $$
            BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'userstatus') THEN
                    CREATE TYPE userstatus AS ENUM ('active', 'inactive', 'pending');
                END IF;
            END $$;
        """))
        conn.execute(text(
            "ALTER TABLE users "
            "ADD COLUMN IF NOT EXISTS status userstatus NOT NULL DEFAULT 'active'"
        ))
        conn.execute(text(
            "ALTER TABLE users "
            "ADD COLUMN IF NOT EXISTS last_active_at TIMESTAMP WITHOUT TIME ZONE"
        ))
        for col, ddl in (
            ("state", "VARCHAR(2)"),
            ("city", "VARCHAR(128)"),
            ("biome", "VARCHAR(64)"),
            ("main_crop", "VARCHAR(64)"),
            ("planting_system", "VARCHAR(32)"),
            ("preferred_units", "VARCHAR(16)"),
            ("profile_updated_at", "TIMESTAMP WITHOUT TIME ZONE"),
            ("avatar_path", "VARCHAR(1024)"),
        ):
            conn.execute(text(f"ALTER TABLE users ADD COLUMN IF NOT EXISTS {col} {ddl}"))

        # Garantir ON DELETE nas FKs que apontam para users.id
        conn.execute(text("""
            DO $$
            DECLARE
                fk RECORD;
            BEGIN
                FOR fk IN
                    SELECT
                        c.conname AS name,
                        conrelid::regclass AS table_name,
                        CASE c.confdeltype
                            WHEN 'a' THEN 'NO ACTION'
                            WHEN 'r' THEN 'RESTRICT'
                            WHEN 'c' THEN 'CASCADE'
                            WHEN 'n' THEN 'SET NULL'
                            WHEN 'd' THEN 'SET DEFAULT'
                        END AS on_delete
                    FROM pg_constraint c
                    JOIN pg_class cl ON cl.oid = c.conrelid
                    WHERE c.contype = 'f'
                      AND c.confrelid = 'users'::regclass
                LOOP
                    IF fk.name = 'conversations_user_id_fkey' AND fk.on_delete <> 'CASCADE' THEN
                        EXECUTE 'ALTER TABLE conversations DROP CONSTRAINT ' || quote_ident(fk.name);
                        EXECUTE 'ALTER TABLE conversations ADD CONSTRAINT ' || quote_ident(fk.name)
                                || ' FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE';
                    ELSIF fk.name = 'session_documents_user_id_fkey' AND fk.on_delete <> 'CASCADE' THEN
                        EXECUTE 'ALTER TABLE session_documents DROP CONSTRAINT ' || quote_ident(fk.name);
                        EXECUTE 'ALTER TABLE session_documents ADD CONSTRAINT ' || quote_ident(fk.name)
                                || ' FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE';
                    ELSIF fk.name = 'knowledge_documents_indexed_by_fkey' AND fk.on_delete <> 'SET NULL' THEN
                        EXECUTE 'ALTER TABLE knowledge_documents DROP CONSTRAINT ' || quote_ident(fk.name);
                        EXECUTE 'ALTER TABLE knowledge_documents ADD CONSTRAINT ' || quote_ident(fk.name)
                                || ' FOREIGN KEY (indexed_by) REFERENCES users(id) ON DELETE SET NULL';
                    ELSIF fk.name = 'query_logs_user_id_fkey' AND fk.on_delete <> 'SET NULL' THEN
                        EXECUTE 'ALTER TABLE query_logs DROP CONSTRAINT ' || quote_ident(fk.name);
                        EXECUTE 'ALTER TABLE query_logs ADD CONSTRAINT ' || quote_ident(fk.name)
                                || ' FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL';
                    END IF;
                END LOOP;
            END $$;
        """))


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
app.include_router(my_documents.router)
app.include_router(query.router)
app.include_router(users.router)
app.include_router(settings_routes.router)
app.include_router(search_routes.router)
app.include_router(access_requests.router)


@app.get("/health")
def health():
    return {"status": "ok", "app": settings.APP_NAME}
