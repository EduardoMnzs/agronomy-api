from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import or_
from sqlalchemy.orm import Session

from api.deps import get_current_user, require_admin
from db.models import User, UserRole, UserStatus
from db.session import get_db
from services.auth import hash_password

router = APIRouter(prefix="/users", tags=["users"])


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
