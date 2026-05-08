"""
Runtime app settings — DB-backed, overrides .env values.

Secrets are encrypted with Fernet using a key derived from settings.SECRET_KEY.
Keep `.env` as the bootstrap source; DB wins at runtime once populated.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import threading
import time
from typing import Any, Iterable

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.orm import Session

from core.config import settings as env_settings
from db.models import AppSetting
from db.session import SessionLocal

logger = logging.getLogger(__name__)

SECRET_KEYS: set[str] = {
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
    "AZURE_API_KEY",
}

ALLOWED_KEYS: set[str] = {
    # provider selection
    "LLM_PROVIDER",
    # provider creds
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
    "AZURE_API_KEY",
    "AZURE_API_BASE",
    "AZURE_API_VERSION",
    # models
    "LLM_MODEL",
    "INDEX_MODEL",
    "QUERY_MODEL",
    "ROUTER_MODEL",
    "AGENT_MODEL",
    # RAG behavior
    "ROUTER_ENABLED",
    "PROMPT_CACHE_ENABLED",
    "AGENT_MAX_TOOL_CALLS",
    "AGENT_MAX_PAGES_PER_CALL",
    "ENABLE_DOC_DESCRIPTION",
}

BOOL_KEYS = {"ROUTER_ENABLED", "PROMPT_CACHE_ENABLED", "ENABLE_DOC_DESCRIPTION"}
INT_KEYS = {"AGENT_MAX_TOOL_CALLS", "AGENT_MAX_PAGES_PER_CALL"}

_CACHE_TTL = 5.0
_cache: dict[str, Any] = {}
_cache_loaded_at: float = 0.0
_lock = threading.Lock()


def _fernet() -> Fernet:
    key = hashlib.sha256(env_settings.SECRET_KEY.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def _encrypt(raw: str) -> str:
    return _fernet().encrypt(raw.encode("utf-8")).decode("utf-8")


def _decrypt(token: str) -> str | None:
    try:
        return _fernet().decrypt(token.encode("utf-8")).decode("utf-8")
    except (InvalidToken, ValueError):
        return None


def _coerce(key: str, raw: str) -> Any:
    if raw is None:
        return None
    if key in BOOL_KEYS:
        return str(raw).strip().lower() in ("1", "true", "yes", "on")
    if key in INT_KEYS:
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None
    return raw


def _load_from_db() -> dict[str, Any]:
    db: Session = SessionLocal()
    try:
        rows = db.query(AppSetting).all()
        out: dict[str, Any] = {}
        for row in rows:
            if row.value is None:
                continue
            raw = row.value
            if row.is_secret:
                decrypted = _decrypt(raw)
                if decrypted is None:
                    logger.warning("Failed to decrypt setting %s", row.key)
                    continue
                raw = decrypted
            out[row.key] = _coerce(row.key, raw)
        return out
    finally:
        db.close()


def _refresh_cache_if_stale() -> None:
    global _cache_loaded_at, _cache
    now = time.monotonic()
    if _cache and (now - _cache_loaded_at) < _CACHE_TTL:
        return
    with _lock:
        if _cache and (time.monotonic() - _cache_loaded_at) < _CACHE_TTL:
            return
        try:
            _cache = _load_from_db()
            _cache_loaded_at = time.monotonic()
        except Exception:  # noqa: BLE001
            logger.exception("Failed to load app_settings from DB")


def invalidate_cache() -> None:
    global _cache_loaded_at
    _cache_loaded_at = 0.0


def get(key: str, default: Any = None) -> Any:
    """Read a setting: DB wins, falls back to env, then default."""
    _refresh_cache_if_stale()
    if key in _cache and _cache[key] not in (None, ""):
        return _cache[key]
    env_val = getattr(env_settings, key, None)
    if env_val not in (None, ""):
        return env_val
    return default


def get_all_for_admin() -> dict[str, Any]:
    """Returns every ALLOWED_KEYS value. Secrets are masked."""
    _refresh_cache_if_stale()
    out: dict[str, Any] = {}
    for key in ALLOWED_KEYS:
        is_secret = key in SECRET_KEYS
        value = _cache.get(key)
        env_val = getattr(env_settings, key, None) if not is_secret else None
        if value is None or value == "":
            value = env_val
        if is_secret:
            # Reveal only whether it's set + a short hint.
            stored_in_db = key in _cache and _cache[key]
            stored_in_env = bool(getattr(env_settings, key, None))
            raw_preview = _cache.get(key) or getattr(env_settings, key, "") or ""
            preview = _mask(raw_preview) if raw_preview else ""
            out[key] = {
                "is_secret": True,
                "has_value": bool(stored_in_db or stored_in_env),
                "source": "db" if stored_in_db else ("env" if stored_in_env else None),
                "preview": preview,
            }
        else:
            out[key] = {
                "is_secret": False,
                "value": value,
                "source": "db" if key in _cache and _cache[key] not in (None, "") else ("env" if env_val not in (None, "") else None),
            }
    return out


def _mask(value: str) -> str:
    if not value:
        return ""
    value = str(value)
    if len(value) <= 8:
        return "•" * len(value)
    return f"{value[:3]}••••{value[-4:]}"


def set_many(db: Session, updates: dict[str, Any]) -> None:
    """
    Persist updates. Use empty string "" to clear a stored value (falls back to env).
    Secrets are encrypted before storing.
    """
    for key, raw in updates.items():
        if key not in ALLOWED_KEYS:
            continue
        is_secret = key in SECRET_KEYS
        row = db.query(AppSetting).filter(AppSetting.key == key).first()
        if raw is None or raw == "":
            if row:
                db.delete(row)
            continue
        stored = _encrypt(str(raw)) if is_secret else str(raw)
        if row:
            row.value = stored
            row.is_secret = is_secret
        else:
            db.add(AppSetting(key=key, value=stored, is_secret=is_secret))
    db.commit()
    invalidate_cache()


def iter_secret_keys() -> Iterable[str]:
    return iter(SECRET_KEYS)
