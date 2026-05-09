import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from jose import JWTError
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from api.deps import get_current_user
from api.rate_limit import limiter
from core.config import settings
from db.models import PasswordResetToken, User, UserStatus
from db.session import get_db
from services.auth import (
    MIN_PASSWORD_LEN,
    TOKEN_TYPE_REFRESH,
    authenticate_user,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    is_active_user,
    verify_password,
)
from services.email import password_reset_email, send_email

router = APIRouter(prefix="/auth", tags=["auth"])


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


_GENERIC_AUTH_ERROR = "Email ou senha incorretos"


@router.post("/login", response_model=TokenResponse)
@limiter.limit("10/minute")
def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    remember_me: bool = Form(False),
    db: Session = Depends(get_db),
):
    user = authenticate_user(db, username, password)
    # Mensagem unificada — evita enumeração (existente vs inativo vs inexistente).
    if not user or not is_active_user(user):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=_GENERIC_AUTH_ERROR,
        )

    return TokenResponse(
        access_token=create_access_token({"sub": str(user.id)}, persistent=remember_me),
        refresh_token=create_refresh_token({"sub": str(user.id)}),
    )


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=MIN_PASSWORD_LEN, max_length=255)


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(
    body: ChangePasswordRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Senha atual incorreta")

    if body.current_password == body.new_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A nova senha deve ser diferente da atual",
        )

    user.password_hash = hash_password(body.new_password)
    if user.status == UserStatus.pending:
        user.status = UserStatus.active
    db.commit()


_RESET_TOKEN_TTL_MIN = 30


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


@router.post("/forgot-password", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("5/hour")
def forgot_password(request: Request, body: ForgotPasswordRequest, db: Session = Depends(get_db)):
    # Sempre 204, independente do e-mail existir — evita enumeração.
    user = db.query(User).filter(User.email == body.email).first()
    if user and user.status != UserStatus.inactive:
        # invalida tokens antigos não usados
        db.query(PasswordResetToken).filter(
            PasswordResetToken.user_id == user.id,
            PasswordResetToken.used_at.is_(None),
        ).delete()
        raw = secrets.token_urlsafe(32)
        db.add(PasswordResetToken(
            user_id=user.id,
            token_hash=_hash_token(raw),
            expires_at=datetime.now(tz=timezone.utc).replace(tzinfo=None) + timedelta(minutes=_RESET_TOKEN_TTL_MIN),
        ))
        db.commit()
        base = settings.APP_BASE_URL.rstrip("/")
        reset_url = f"{base}/reset-password?token={raw}"
        subject, html_body, text = password_reset_email(user.full_name, reset_url)
        send_email(user.email, subject, html_body, text)
    return None


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str = Field(min_length=MIN_PASSWORD_LEN, max_length=255)


@router.post("/reset-password", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("10/hour")
def reset_password(request: Request, body: ResetPasswordRequest, db: Session = Depends(get_db)):
    row = db.query(PasswordResetToken).filter(
        PasswordResetToken.token_hash == _hash_token(body.token)
    ).first()
    if not row or row.used_at is not None or row.expires_at < datetime.now(tz=timezone.utc).replace(tzinfo=None):
        raise HTTPException(status_code=400, detail="Token inválido ou expirado")

    user = db.query(User).filter(User.id == row.user_id).first()
    if not user:
        raise HTTPException(status_code=400, detail="Token inválido ou expirado")

    user.password_hash = hash_password(body.new_password)
    if user.status == UserStatus.pending:
        user.status = UserStatus.active
    row.used_at = datetime.now(tz=timezone.utc).replace(tzinfo=None)
    db.commit()


@router.post("/refresh", response_model=TokenResponse)
@limiter.limit("30/minute")
def refresh(request: Request, body: RefreshRequest, db: Session = Depends(get_db)):
    invalid = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token inválido"
    )
    try:
        payload = decode_token(body.refresh_token)
    except JWTError:
        raise invalid

    if payload.get("type") != TOKEN_TYPE_REFRESH:
        raise invalid

    user_id = payload.get("sub")
    if not user_id:
        raise invalid

    try:
        uid_int = int(user_id)
    except (TypeError, ValueError):
        raise invalid

    # Revoga sessão de usuário deletado/inativado — evita persistência indefinida.
    user = db.query(User).filter(User.id == uid_int).first()
    if not is_active_user(user):
        raise invalid

    return TokenResponse(
        access_token=create_access_token({"sub": str(user.id)}),
        refresh_token=create_refresh_token({"sub": str(user.id)}),
    )
