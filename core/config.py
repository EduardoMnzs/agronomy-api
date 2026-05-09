import logging

from pydantic import field_validator
from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)

_INSECURE_SECRET_KEYS = {"change-me-in-production", "", "secret", "changeme"}
_MAX_ACCESS_TOKEN_MINUTES = 60 * 24
_MAX_REFRESH_TOKEN_DAYS = 30


class Settings(BaseSettings):
    APP_NAME: str = "Agronomy API"
    DEBUG: bool = False

    DATABASE_URL: str = "postgresql+psycopg2://agronomy:agronomy@localhost:5432/agronomy"

    SECRET_KEY: str = "change-me-in-production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    REMEMBER_ME_DAYS: int = 30

    ALLOWED_ORIGINS: str = "http://localhost:5173,http://localhost:3000"

    MAX_UPLOAD_BYTES: int = 50 * 1024 * 1024
    MAX_UPLOAD_BYTES_BY_EXT: dict[str, int] = {
        ".pdf": 50 * 1024 * 1024,
        ".docx": 20 * 1024 * 1024,
        ".xlsx": 20 * 1024 * 1024,
        ".xls": 20 * 1024 * 1024,
        ".csv": 10 * 1024 * 1024,
        ".json": 5 * 1024 * 1024,
        ".md": 5 * 1024 * 1024,
        ".txt": 5 * 1024 * 1024,
    }

    CONVERSATION_MAX_MESSAGES: int = 100

    ALLOWED_LLM_MODELS: str = ""

    LLM_MODEL: str = "gpt-4o-2024-11-20"
    INDEX_MODEL: str | None = None
    QUERY_MODEL: str | None = None
    ROUTER_MODEL: str | None = None
    AGENT_MODEL: str | None = None

    ROUTER_ENABLED: bool = True
    ENABLE_DOC_DESCRIPTION: bool = True
    AGENT_MAX_TOOL_CALLS: int = 12
    AGENT_MAX_PAGES_PER_CALL: int = 8
    PROMPT_CACHE_ENABLED: bool = True

    AZURE_API_KEY: str = ""
    AZURE_API_BASE: str = ""
    AZURE_API_VERSION: str = ""

    REDIS_URL: str = "redis://localhost:6379"

    DATA_DIR: str = "data"
    KNOWLEDGE_FILES_DIR: str = "data/knowledge/files"
    KNOWLEDGE_INDEXES_DIR: str = "data/knowledge/indexes"
    SESSION_FILES_DIR: str = "data/sessions/files"
    SESSION_INDEXES_DIR: str = "data/sessions/indexes"
    USER_DOCS_FILES_DIR: str = "data/users/files"
    USER_DOCS_INDEXES_DIR: str = "data/users/indexes"
    AVATARS_DIR: str = "data/avatars"

    RESEND_API_KEY: str = ""
    FROM_EMAIL: str = ""
    APP_BASE_URL: str = "http://localhost:5173"

    model_config = {"env_file": ".env", "extra": "allow"}

    @field_validator("SECRET_KEY")
    @classmethod
    def _check_secret_key(cls, v: str, info) -> str:
        if v.strip().lower() in _INSECURE_SECRET_KEYS:
            debug = (info.data.get("DEBUG") if info and info.data else False) or False
            if not debug:
                raise ValueError(
                    "SECRET_KEY está com valor inseguro. "
                    "Gere com: python -c \"import secrets; print(secrets.token_urlsafe(64))\" "
                    "e defina em .env. (DEBUG=true permite bootstrap em dev)"
                )
            logger.warning("SECRET_KEY default detectada — só aceitável em DEBUG/dev.")
        return v

    @field_validator("ACCESS_TOKEN_EXPIRE_MINUTES")
    @classmethod
    def _cap_access_minutes(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("ACCESS_TOKEN_EXPIRE_MINUTES deve ser positivo")
        if v > _MAX_ACCESS_TOKEN_MINUTES:
            logger.warning(
                "ACCESS_TOKEN_EXPIRE_MINUTES=%d acima do cap (%d). Truncando.",
                v, _MAX_ACCESS_TOKEN_MINUTES,
            )
            return _MAX_ACCESS_TOKEN_MINUTES
        return v

    @field_validator("REFRESH_TOKEN_EXPIRE_DAYS", "REMEMBER_ME_DAYS")
    @classmethod
    def _cap_days(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("Token TTL deve ser positivo")
        return min(v, _MAX_REFRESH_TOKEN_DAYS)

    @property
    def allowed_origins_list(self) -> list[str]:
        return [o.strip() for o in self.ALLOWED_ORIGINS.split(",") if o.strip()]

    @property
    def allowed_llm_models(self) -> set[str]:
        return {m.strip() for m in self.ALLOWED_LLM_MODELS.split(",") if m.strip()}

    def _runtime(self, key: str, default=None):
        # Late import quebra o ciclo: app_settings importa settings.
        try:
            from core import app_settings as _rt
            return _rt.get(key, default)
        except Exception:  # noqa: BLE001
            return default

    @property
    def index_model(self) -> str:
        return self._runtime("INDEX_MODEL") or self._runtime("LLM_MODEL") or self.INDEX_MODEL or self.LLM_MODEL

    @property
    def query_model(self) -> str:
        return self._runtime("QUERY_MODEL") or self._runtime("LLM_MODEL") or self.QUERY_MODEL or self.LLM_MODEL

    @property
    def router_model(self) -> str:
        return self._runtime("ROUTER_MODEL") or self.query_model

    @property
    def agent_model(self) -> str:
        return self._runtime("AGENT_MODEL") or self.query_model

    def runtime_get(self, key: str, default=None):
        return self._runtime(key, default if default is not None else getattr(self, key, None))


settings = Settings()
