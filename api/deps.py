from datetime import datetime, timedelta

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError
from sqlalchemy.orm import Session

from core.config import settings
from db.models import User, UserRole
from db.session import get_db
from services.auth import decode_token

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


def public_url(request: Request, route_name: str, **path_params) -> str:
    """Build a fully-qualified URL for a named route.

    Behind a TLS proxy (Caddy), the internal request arrives as http:// even
    though the client used https://. We detect this via X-Forwarded-Proto sent
    by Caddy and rewrite the scheme so the browser never sees mixed-content URLs.
    """
    url = str(request.url_for(route_name, **path_params))
    forwarded_proto = request.headers.get("x-forwarded-proto", "")
    if forwarded_proto == "https" or (not settings.DEBUG and not url.startswith("https://")):
        url = url.replace("http://", "https://", 1)
    return url

_LAST_ACTIVE_THROTTLE = timedelta(minutes=5)


def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Token inválido ou expirado",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = decode_token(token)
        if payload.get("type") != "access":
            raise credentials_exception
        user_id: int = payload.get("sub")
        if user_id is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    user = db.query(User).filter(User.id == int(user_id)).first()
    if user is None:
        raise credentials_exception

    now = datetime.utcnow()
    if user.last_active_at is None or (now - user.last_active_at) > _LAST_ACTIVE_THROTTLE:
        try:
            user.last_active_at = now
            db.commit()
        except Exception:
            db.rollback()

    return user


def require_admin(current_user: User = Depends(get_current_user)) -> User:
    if current_user.role != UserRole.admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Acesso restrito a administradores")
    return current_user
