import secrets
from datetime import datetime, timedelta

from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from core.config import settings
from db.models import User, UserStatus

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

MIN_PASSWORD_LEN = 8

TOKEN_TYPE_ACCESS = "access"
TOKEN_TYPE_REFRESH = "refresh"


def _truncate_for_bcrypt(password: str) -> str:
    # bcrypt ignora bytes acima de 72 — trunca explicitamente.
    encoded = password.encode("utf-8")
    if len(encoded) <= 72:
        return password
    return encoded[:72].decode("utf-8", errors="ignore")


def hash_password(password: str) -> str:
    return pwd_context.hash(_truncate_for_bcrypt(password))


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(_truncate_for_bcrypt(plain), hashed)


def _build_token(payload: dict, *, token_type: str, expires_delta: timedelta) -> str:
    now = datetime.utcnow()
    body = {
        **payload,
        "type": token_type,
        "iat": now,
        "exp": now + expires_delta,
        "jti": secrets.token_urlsafe(16),
    }
    return jwt.encode(body, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def create_access_token(data: dict, persistent: bool = False) -> str:
    if persistent:
        ttl = timedelta(days=settings.REMEMBER_ME_DAYS)
    else:
        ttl = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    return _build_token(data, token_type=TOKEN_TYPE_ACCESS, expires_delta=ttl)


def create_refresh_token(data: dict) -> str:
    return _build_token(
        data,
        token_type=TOKEN_TYPE_REFRESH,
        expires_delta=timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
    )


def decode_token(token: str) -> dict:
    return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])


def authenticate_user(db: Session, email: str, password: str) -> User | None:
    user = db.query(User).filter(User.email == email).first()
    if not user or not verify_password(password, user.password_hash):
        return None
    return user


def is_active_user(user: User | None) -> bool:
    return user is not None and user.status != UserStatus.inactive
