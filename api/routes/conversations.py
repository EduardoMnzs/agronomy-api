from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.deps import get_current_user
from db.models import Conversation, User
from db.session import get_db

router = APIRouter(prefix="/conversations", tags=["conversations"])


class ConversationSummary(BaseModel):
    id: str
    title: str
    pinned: bool
    created_at: str
    updated_at: str


class MessageOut(BaseModel):
    role: str
    content: str
    citations: list | None = None


class ConversationDetail(BaseModel):
    id: str
    title: str
    pinned: bool
    messages: list[MessageOut]
    created_at: str


class ConversationPatch(BaseModel):
    title: str | None = None
    pinned: bool | None = None


def _summary(c: Conversation) -> ConversationSummary:
    return ConversationSummary(
        id=str(c.id),
        title=c.title,
        pinned=c.pinned,
        created_at=c.created_at.isoformat(),
        updated_at=c.updated_at.isoformat(),
    )


def _get_or_404(conv_id: str, user_id: int, db: Session) -> Conversation:
    try:
        uid = uuid.UUID(conv_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Conversa não encontrada")
    conv = db.query(Conversation).filter(Conversation.id == uid, Conversation.user_id == user_id).first()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversa não encontrada")
    return conv


@router.get("", response_model=list[ConversationSummary])
def list_conversations(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    convs = (
        db.query(Conversation)
        .filter(Conversation.user_id == user.id)
        .order_by(Conversation.pinned.desc(), Conversation.updated_at.desc())
        .all()
    )
    return [_summary(c) for c in convs]


@router.get("/{conv_id}", response_model=ConversationDetail)
def get_conversation(conv_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    conv = _get_or_404(conv_id, user.id, db)
    return ConversationDetail(
        id=str(conv.id),
        title=conv.title,
        pinned=conv.pinned,
        messages=[MessageOut(**m) for m in (conv.messages or [])],
        created_at=conv.created_at.isoformat(),
    )


@router.patch("/{conv_id}", response_model=ConversationSummary)
def patch_conversation(
    conv_id: str,
    body: ConversationPatch,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    conv = _get_or_404(conv_id, user.id, db)
    if body.title is not None:
        conv.title = body.title.strip() or conv.title
    if body.pinned is not None:
        conv.pinned = body.pinned
    db.commit()
    db.refresh(conv)
    return _summary(conv)


@router.delete("/{conv_id}", status_code=204)
def delete_conversation(conv_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    conv = _get_or_404(conv_id, user.id, db)
    db.delete(conv)
    db.commit()
