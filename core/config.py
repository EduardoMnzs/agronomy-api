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

    # LLM
    LLM_MODEL: str = "gpt-4o-2024-11-20"

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


settings = Settings()
