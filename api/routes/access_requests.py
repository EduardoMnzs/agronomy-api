from __future__ import annotations

import secrets
import string
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from api.deps import require_admin
from core.config import settings
from db.models import AccessRequest, AccessRequestStatus, User, UserRole, UserStatus
from db.session import get_db
from services.auth import hash_password
from services.email import access_request_decision_email, send_email

router = APIRouter(prefix="/access-requests", tags=["access-requests"])


class AccessRequestCreate(BaseModel):
    full_name: str = Field(min_length=2, max_length=255)
    email: EmailStr
    organization: str | None = Field(default=None, max_length=255)
    message: str | None = Field(default=None, max_length=2000)


class AccessRequestOut(BaseModel):
    id: int
    full_name: str
    email: str
    organization: str | None
    message: str | None
    status: str
    rejection_reason: str | None
    created_at: str
    decided_at: str | None


class AccessRequestsPage(BaseModel):
    items: list[AccessRequestOut]
    total: int
    page: int
    limit: int


class AccessRequestDecision(BaseModel):
    action: Literal["approve", "reject"]
    role: UserRole | None = UserRole.user
    rejection_reason: str | None = None


def _serialize(r: AccessRequest) -> AccessRequestOut:
    return AccessRequestOut(
        id=r.id,
        full_name=r.full_name,
        email=r.email,
        organization=r.organization,
        message=r.message,
        status=r.status.value,
        rejection_reason=r.rejection_reason,
        created_at=r.created_at.isoformat() + "Z" if r.created_at else "",
        decided_at=r.decided_at.isoformat() + "Z" if r.decided_at else None,
    )


def _generate_temp_password() -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(12))


@router.post("", status_code=status.HTTP_201_CREATED, response_model=AccessRequestOut)
def create_request(body: AccessRequestCreate, db: Session = Depends(get_db)):
    # endpoint público — sem auth
    if db.query(User).filter(User.email == body.email).first():
        raise HTTPException(
            status_code=409,
            detail="Este e-mail já possui conta. Use 'Esqueci minha senha' se não lembra a senha.",
        )
    # se já tem um pending, não duplica
    existing = db.query(AccessRequest).filter(
        AccessRequest.email == body.email,
        AccessRequest.status == AccessRequestStatus.pending,
    ).first()
    if existing:
        return _serialize(existing)

    req = AccessRequest(
        full_name=body.full_name.strip(),
        email=body.email,
        organization=(body.organization or "").strip() or None,
        message=(body.message or "").strip() or None,
        status=AccessRequestStatus.pending,
    )
    db.add(req)
    db.commit()
    db.refresh(req)
    return _serialize(req)


@router.get("", response_model=AccessRequestsPage)
def list_requests(
    status_: AccessRequestStatus | None = Query(default=None, alias="status"),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    q = db.query(AccessRequest)
    if status_ is not None:
        q = q.filter(AccessRequest.status == status_)
    total = q.count()
    rows = (
        q.order_by(AccessRequest.created_at.desc())
        .offset((page - 1) * limit)
        .limit(limit)
        .all()
    )
    return AccessRequestsPage(
        items=[_serialize(r) for r in rows],
        total=total,
        page=page,
        limit=limit,
    )


class ApproveResponse(BaseModel):
    request: AccessRequestOut
    user_id: int | None = None
    temporary_password: str | None = None


@router.post("/{request_id}/decide", response_model=ApproveResponse)
def decide_request(
    request_id: int,
    body: AccessRequestDecision,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    req = db.query(AccessRequest).filter(AccessRequest.id == request_id).first()
    if not req:
        raise HTTPException(status_code=404, detail="Solicitação não encontrada")
    if req.status != AccessRequestStatus.pending:
        raise HTTPException(status_code=400, detail="Solicitação já decidida")

    login_url = settings.APP_BASE_URL.rstrip("/") + "/login"

    if body.action == "approve":
        if db.query(User).filter(User.email == req.email).first():
            raise HTTPException(status_code=409, detail="Já existe um usuário com este e-mail")
        temp_password = _generate_temp_password()
        new_user = User(
            email=req.email,
            full_name=req.full_name,
            password_hash=hash_password(temp_password),
            role=body.role or UserRole.user,
            status=UserStatus.pending,
        )
        db.add(new_user)
        db.flush()  # pega o id
        req.status = AccessRequestStatus.approved
        req.decided_at = datetime.utcnow()
        req.decided_by = admin.id
        req.created_user_id = new_user.id
        db.commit()
        db.refresh(req)

        subject, html, text = access_request_decision_email(
            req.full_name, approved=True, login_url=login_url
        )
        # anexa credencial temporária ao e-mail aprovado
        html += (
            f"<p style='margin-top:20px;padding:12px;background:#fff8e7;border-left:4px solid #EC6608;'>"
            f"<strong>Senha temporária:</strong> <code style='font-size:14px;'>{temp_password}</code><br/>"
            f"<span style='color:#666;'>Você deverá trocá-la no primeiro login.</span></p>"
        )
        text += f"\n\nSenha temporária: {temp_password}\nVocê deverá trocá-la no primeiro login."
        send_email(req.email, subject, html, text)

        return ApproveResponse(
            request=_serialize(req),
            user_id=new_user.id,
            temporary_password=temp_password,
        )

    # reject
    req.status = AccessRequestStatus.rejected
    req.rejection_reason = (body.rejection_reason or "").strip() or None
    req.decided_at = datetime.utcnow()
    req.decided_by = admin.id
    db.commit()
    db.refresh(req)

    subject, html, text = access_request_decision_email(
        req.full_name, approved=False, login_url=login_url, reason=req.rejection_reason
    )
    send_email(req.email, subject, html, text)

    return ApproveResponse(request=_serialize(req))


@router.delete("/{request_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_request(
    request_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    req = db.query(AccessRequest).filter(AccessRequest.id == request_id).first()
    if not req:
        raise HTTPException(status_code=404, detail="Solicitação não encontrada")
    db.delete(req)
    db.commit()
