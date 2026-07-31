"""Infra de testes de integração.

Usa um banco Postgres dedicado `agronomy_test`, derivado do `DATABASE_URL` do
`.env` trocando apenas o nome do banco — nunca toca no banco de desenvolvimento.
As tabelas são criadas via metadata e truncadas entre testes.

A app é exercida via `TestClient` SEM acionar o lifespan (não usamos
`with client`), então ARQ/Redis não precisam estar de pé. Rotas que enfileiram
jobs (`POST /knowledge`) não são cobertas aqui por esse motivo.
"""
import psycopg2
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from api.deps import get_current_user
from core.config import settings
from db.models import Base, User, UserRole, UserStatus
from db.session import get_db
import main

_TEST_DB = "agronomy_test"
_base_url = make_url(settings.DATABASE_URL)
_test_url = _base_url.set(database=_TEST_DB)

# Ordem importa: filhas antes das mães (FK).
_TABLES_TO_CLEAR = [
    "query_logs",
    "session_documents",
    "user_documents",
    "knowledge_documents",
    "password_reset_tokens",
    "access_requests",
    "conversations",
    "app_settings",
    "users",
]


def _ensure_test_database() -> None:
    admin = psycopg2.connect(
        host=_base_url.host,
        port=_base_url.port,
        user=_base_url.username,
        password=_base_url.password,
        dbname=_base_url.database,
    )
    admin.autocommit = True
    try:
        with admin.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (_TEST_DB,))
            if not cur.fetchone():
                cur.execute(f'CREATE DATABASE "{_TEST_DB}"')
    finally:
        admin.close()


@pytest.fixture(scope="session")
def engine():
    _ensure_test_database()
    eng = create_engine(_test_url.render_as_string(hide_password=False), pool_pre_ping=True)
    Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture()
def db(engine):
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = Session()
    yield session
    session.rollback()
    session.close()
    with engine.begin() as conn:
        for table in _TABLES_TO_CLEAR:
            conn.execute(text(f'TRUNCATE TABLE "{table}" RESTART IDENTITY CASCADE'))


@pytest.fixture()
def client(engine, db):
    """TestClient com get_db apontando para o banco de teste."""
    from fastapi.testclient import TestClient

    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def _get_db():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    main.app.dependency_overrides[get_db] = _get_db
    with_client = TestClient(main.app)
    yield with_client
    main.app.dependency_overrides.clear()


@pytest.fixture()
def make_user(db):
    def _make(email="admin@test.com", role=UserRole.admin, full_name="Ana Souza"):
        u = User(
            email=email,
            password_hash="x",
            full_name=full_name,
            role=role,
            status=UserStatus.active,
        )
        db.add(u)
        db.commit()
        db.refresh(u)
        return u

    return _make


@pytest.fixture()
def auth_as():
    """Autentica a app como `user` para as próximas requisições."""
    def _auth(user):
        main.app.dependency_overrides[get_current_user] = lambda: user
        return user

    yield _auth
    main.app.dependency_overrides.pop(get_current_user, None)
