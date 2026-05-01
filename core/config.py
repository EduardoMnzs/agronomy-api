from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # App
    APP_NAME: str = "Agronomy API"
    DEBUG: bool = False

    # Database
    DATABASE_URL: str = "postgresql://agronomy:agronomy@localhost:5432/agronomy"

    # JWT
    SECRET_KEY: str = "change-me-in-production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # LLM (multi-model).
    # LLM_MODEL is the fallback; INDEX/QUERY/ROUTER/AGENT can override per stage.
    LLM_MODEL: str = "gpt-4o-2024-11-20"
    INDEX_MODEL: str | None = None
    QUERY_MODEL: str | None = None
    ROUTER_MODEL: str | None = None
    AGENT_MODEL: str | None = None

    # Retrieval behavior
    ROUTER_ENABLED: bool = True
    ENABLE_DOC_DESCRIPTION: bool = True
    AGENT_MAX_TOOL_CALLS: int = 10
    AGENT_MAX_PAGES_PER_CALL: int = 8
    PROMPT_CACHE_ENABLED: bool = True

    # Azure OpenAI (opcional)
    AZURE_API_KEY: str = ""
    AZURE_API_BASE: str = ""
    AZURE_API_VERSION: str = ""

    # Paths
    DATA_DIR: str = "data"
    KNOWLEDGE_FILES_DIR: str = "data/knowledge/files"
    KNOWLEDGE_INDEXES_DIR: str = "data/knowledge/indexes"
    SESSION_FILES_DIR: str = "data/sessions/files"
    SESSION_INDEXES_DIR: str = "data/sessions/indexes"

    model_config = {"env_file": ".env", "extra": "allow"}

    @property
    def index_model(self) -> str:
        return self.INDEX_MODEL or self.LLM_MODEL

    @property
    def query_model(self) -> str:
        return self.QUERY_MODEL or self.LLM_MODEL

    @property
    def router_model(self) -> str:
        return self.ROUTER_MODEL or self.query_model

    @property
    def agent_model(self) -> str:
        return self.AGENT_MODEL or self.query_model


settings = Settings()
