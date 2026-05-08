from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import or_
from sqlalchemy.orm import Session

from api.deps import get_current_user, require_admin
from db.models import User, UserRole, UserStatus
from db.session import get_db
from services.auth import hash_password

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


class UserOut(BaseModel):
    id: int
    full_name: str
    email: str
    role: str
    status: str
    last_active_at: str | None
    created_at: str

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


def _to_out(user: User) -> UserOut:
    return UserOut(
        id=user.id,
        full_name=user.full_name,
        email=user.email,
        role=user.role.value,
        status=user.status.value if user.status else UserStatus.active.value,
        last_active_at=user.last_active_at.isoformat() + "Z" if user.last_active_at else None,
        created_at=user.created_at.isoformat() + "Z" if user.created_at else None,
    )


@router.get("", response_model=UsersPage)
def list_users(
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
        items=[_to_out(u) for u in rows],
        total=total,
        page=page,
        limit=limit,
    )


@router.get("/me", response_model=UserOut)
def get_me(current_user: User = Depends(get_current_user)):
    return _to_out(current_user)


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
def create_user(body: UserCreate, db: Session = Depends(get_db), _: User = Depends(require_admin)):
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
    return _to_out(user)


@router.patch("/{user_id}", response_model=UserOut)
def update_user(user_id: int, body: UserUpdate, db: Session = Depends(get_db), _: User = Depends(require_admin)):
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
    return _to_out(user)


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
