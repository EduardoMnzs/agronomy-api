from __future__ import annotations

import shutil
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import FileResponse
from jose import JWTError, jwt
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import or_
from sqlalchemy.orm import Session

from api.deps import get_current_user, require_admin
from core.config import settings
from db.models import User, UserRole, UserStatus
from db.session import get_db
from services.auth import hash_password, verify_password

router = APIRouter(prefix="/users", tags=["users"])


_VALID_STATES = {
    "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA",
    "MT", "MS", "MG", "PA", "PB", "PR", "PE", "PI", "RJ", "RN",
    "RS", "RO", "RR", "SC", "SP", "SE", "TO",
}
_VALID_PLANTING = {"direto", "convencional", "cultivo_minimo", "misto"}
_VALID_UNITS = {"metrico", "sacas"}


_STATE_TO_BIOME = {
    # aproximação — estados que atravessam múltiplos biomas ficam no dominante
    "AC": "Amazônia", "AM": "Amazônia", "AP": "Amazônia", "PA": "Amazônia",
    "RO": "Amazônia", "RR": "Amazônia", "TO": "Cerrado",
    "MA": "Cerrado", "PI": "Cerrado", "MT": "Cerrado", "GO": "Cerrado",
    "DF": "Cerrado", "MS": "Cerrado", "MG": "Cerrado",
    "CE": "Caatinga", "RN": "Caatinga", "PB": "Caatinga",
    "PE": "Caatinga", "AL": "Caatinga", "SE": "Caatinga", "BA": "Caatinga",
    "ES": "Mata Atlântica", "RJ": "Mata Atlântica", "SP": "Mata Atlântica",
    "PR": "Mata Atlântica", "SC": "Mata Atlântica",
    "RS": "Pampa",
}


_AVATAR_EXTENSIONS = {"jpg", "jpeg", "png", "webp", "gif"}
_AVATAR_MIME = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
    "gif": "image/gif",
}
_AVATAR_MAX_BYTES = 5 * 1024 * 1024
_AVATAR_TOKEN_TTL_MIN = 60


def _make_avatar_token(user_id: int) -> str:
    payload = {
        "user_id": user_id,
        "type": "avatar",
        "exp": datetime.utcnow() + timedelta(minutes=_AVATAR_TOKEN_TTL_MIN),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def _verify_avatar_token(token: str, user_id: int) -> None:
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=401, detail="Token inválido ou expirado")
    if payload.get("type") != "avatar" or int(payload.get("user_id", -1)) != user_id:
        raise HTTPException(status_code=403, detail="Token não autorizado")


def _avatar_url(request: Request | None, user: User) -> str | None:
    if not user.avatar_path or not Path(user.avatar_path).exists():
        return None
    if request is None:
        return None
    token = _make_avatar_token(user.id)
    return str(request.url_for("get_user_avatar", user_id=user.id)) + f"?token={token}"


class UserOut(BaseModel):
    id: int
    full_name: str
    email: str
    role: str
    status: str
    last_active_at: str | None
    created_at: str
    avatar_url: str | None = None

    model_config = {"from_attributes": True}


class UsersPage(BaseModel):
    items: list[UserOut]
    total: int
    page: int
    limit: int


class UserCreate(BaseModel):
    full_name: str = Field(min_length=1, max_length=255)
    email: EmailStr
    password: str = Field(min_length=6, max_length=255)
    role: UserRole = UserRole.user


class UserUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=255)
    email: EmailStr | None = None
    password: str | None = Field(default=None, min_length=6, max_length=255)
    role: UserRole | None = None
    status: UserStatus | None = None


def _to_out(user: User, request: Request | None = None) -> UserOut:
    return UserOut(
        id=user.id,
        full_name=user.full_name,
        email=user.email,
        role=user.role.value,
        status=user.status.value if user.status else UserStatus.active.value,
        last_active_at=user.last_active_at.isoformat() + "Z" if user.last_active_at else None,
        created_at=user.created_at.isoformat() + "Z" if user.created_at else None,
        avatar_url=_avatar_url(request, user),
    )


@router.get("", response_model=UsersPage)
def list_users(
    request: Request,
    search: str | None = Query(default=None),
    role: UserRole | None = Query(default=None),
    status_: UserStatus | None = Query(default=None, alias="status"),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=10, ge=1, le=100),
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    q = db.query(User)

    if search:
        like = f"%{search.strip()}%"
        q = q.filter(or_(User.full_name.ilike(like), User.email.ilike(like)))
    if role is not None:
        q = q.filter(User.role == role)
    if status_ is not None:
        q = q.filter(User.status == status_)

    total = q.count()
    rows = (
        q.order_by(User.created_at.desc())
        .offset((page - 1) * limit)
        .limit(limit)
        .all()
    )

    return UsersPage(
        items=[_to_out(u, request) for u in rows],
        total=total,
        page=page,
        limit=limit,
    )


@router.get("/me", response_model=UserOut)
def get_me(request: Request, current_user: User = Depends(get_current_user)):
    return _to_out(current_user, request)


class SelfUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=255)


@router.patch("/me", response_model=UserOut)
def update_me(
    body: SelfUpdate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if body.full_name is not None:
        current_user.full_name = body.full_name
    db.commit()
    db.refresh(current_user)
    return _to_out(current_user, request)


class SelfPasswordChange(BaseModel):
    current_password: str
    new_password: str = Field(min_length=6, max_length=255)


@router.post("/me/password", status_code=status.HTTP_204_NO_CONTENT)
def change_my_password(
    body: SelfPasswordChange,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not verify_password(body.current_password, current_user.password_hash):
        raise HTTPException(status_code=400, detail="Senha atual incorreta")
    if body.current_password == body.new_password:
        raise HTTPException(status_code=400, detail="A nova senha deve ser diferente da atual")
    current_user.password_hash = hash_password(body.new_password)
    if current_user.status == UserStatus.pending:
        current_user.status = UserStatus.active
    db.commit()


@router.post("/me/avatar", response_model=UserOut)
async def upload_avatar(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    suffix = (Path(file.filename or "").suffix or "").lower().lstrip(".")
    if suffix not in _AVATAR_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Formato não suportado: .{suffix or '?'}")

    avatars_dir = Path(settings.AVATARS_DIR)
    avatars_dir.mkdir(parents=True, exist_ok=True)

    # apaga avatar anterior pra não deixar lixo com outra extensão
    if current_user.avatar_path:
        try:
            Path(current_user.avatar_path).unlink(missing_ok=True)
        except OSError:
            pass

    target = avatars_dir / f"{current_user.id}.{suffix}"
    total = 0
    with open(target, "wb") as out:
        while chunk := await file.read(1 << 16):
            total += len(chunk)
            if total > _AVATAR_MAX_BYTES:
                out.close()
                target.unlink(missing_ok=True)
                raise HTTPException(status_code=400, detail="Imagem excede o limite de 5 MB")
            out.write(chunk)

    current_user.avatar_path = str(target)
    db.commit()
    db.refresh(current_user)
    return _to_out(current_user, request)


@router.delete("/me/avatar", response_model=UserOut)
def delete_avatar(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.avatar_path:
        try:
            Path(current_user.avatar_path).unlink(missing_ok=True)
        except OSError:
            pass
        current_user.avatar_path = None
        db.commit()
        db.refresh(current_user)
    return _to_out(current_user, request)


@router.get("/{user_id}/avatar", name="get_user_avatar")
def get_user_avatar(user_id: int, token: str = Query(...), db: Session = Depends(get_db)):
    _verify_avatar_token(token, user_id)
    user = db.query(User).filter(User.id == user_id).first()
    if not user or not user.avatar_path:
        raise HTTPException(status_code=404, detail="Avatar não encontrado")
    path = Path(user.avatar_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Arquivo não encontrado")
    suffix = path.suffix.lstrip(".").lower()
    media = _AVATAR_MIME.get(suffix, "application/octet-stream")
    return FileResponse(path, media_type=media)


class UserProfileOut(BaseModel):
    state: str | None
    city: str | None
    biome: str | None
    main_crop: str | None
    planting_system: str | None
    preferred_units: str | None
    profile_updated_at: str | None


class UserProfileUpdate(BaseModel):
    state: str | None = None
    city: str | None = Field(default=None, max_length=128)
    main_crop: str | None = Field(default=None, max_length=64)
    planting_system: str | None = None
    preferred_units: str | None = None


def _profile_out(u: User) -> UserProfileOut:
    return UserProfileOut(
        state=u.state,
        city=u.city,
        biome=u.biome,
        main_crop=u.main_crop,
        planting_system=u.planting_system,
        preferred_units=u.preferred_units,
        profile_updated_at=u.profile_updated_at.isoformat() + "Z" if u.profile_updated_at else None,
    )


@router.get("/me/profile", response_model=UserProfileOut)
def get_my_profile(current_user: User = Depends(get_current_user)):
    return _profile_out(current_user)


@router.patch("/me/profile", response_model=UserProfileOut)
def update_my_profile(
    body: UserProfileUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    changed = False
    if body.state is not None:
        s = (body.state or "").upper().strip() or None
        if s and s not in _VALID_STATES:
            raise HTTPException(status_code=400, detail="UF inválida")
        current_user.state = s
        current_user.biome = _STATE_TO_BIOME.get(s) if s else None
        changed = True
    if body.city is not None:
        current_user.city = body.city.strip() or None
        changed = True
    if body.main_crop is not None:
        current_user.main_crop = body.main_crop.strip() or None
        changed = True
    if body.planting_system is not None:
        val = (body.planting_system or "").strip() or None
        if val and val not in _VALID_PLANTING:
            raise HTTPException(status_code=400, detail="Sistema de plantio inválido")
        current_user.planting_system = val
        changed = True
    if body.preferred_units is not None:
        val = (body.preferred_units or "").strip() or None
        if val and val not in _VALID_UNITS:
            raise HTTPException(status_code=400, detail="Unidade inválida")
        current_user.preferred_units = val
        changed = True

    if changed:
        current_user.profile_updated_at = datetime.utcnow()
        db.commit()
        db.refresh(current_user)
    return _profile_out(current_user)


@router.post("", status_code=status.HTTP_201_CREATED, response_model=UserOut)
def create_user(body: UserCreate, request: Request, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    if db.query(User).filter(User.email == body.email).first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email já cadastrado")

    user = User(
        email=body.email,
        full_name=body.full_name,
        password_hash=hash_password(body.password),
        role=body.role,
        status=UserStatus.pending,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return _to_out(user, request)


@router.patch("/{user_id}", response_model=UserOut)
def update_user(user_id: int, body: UserUpdate, request: Request, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")

    if body.email is not None and body.email != user.email:
        if db.query(User).filter(User.email == body.email, User.id != user_id).first():
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email já cadastrado")
        user.email = body.email
    if body.full_name is not None:
        user.full_name = body.full_name
    if body.role is not None:
        user.role = body.role
    if body.status is not None:
        if body.status == UserStatus.pending:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="O status 'pending' é definido automaticamente ao criar ou redefinir senha",
            )
        user.status = body.status
    if body.password is not None:
        user.password_hash = hash_password(body.password)
        user.status = UserStatus.pending

    db.commit()
    db.refresh(user)
    return _to_out(user, request)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(user_id: int, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    if user_id == admin.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Não é possível deletar o próprio usuário",
        )

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")

    db.delete(user)
    db.commit()
