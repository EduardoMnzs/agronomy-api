from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session

from api.deps import get_current_user
from db.models import Conversation, KnowledgeDocument, User, UserDocument, UserRole
from db.session import get_db

router = APIRouter(prefix="/search", tags=["search"])


class ConversationHit(BaseModel):
    id: str
    title: str
    snippet: str | None = None
    updated_at: str


class DocumentHit(BaseModel):
    id: int
    name: str
    file_type: str
    category: str | None
    status: str


class UserHit(BaseModel):
    id: int
    full_name: str
    email: str
    role: str
    avatar_url: str | None = None


class SearchResponse(BaseModel):
    query: str
    conversations: list[ConversationHit]
    knowledge_documents: list[DocumentHit]
    my_documents: list[DocumentHit]
    users: list[UserHit]


_MAX_PER_GROUP = 8


def _snippet_from_messages(messages: list | None, q: str) -> str | None:
    if not messages:
        return None
    lowered = q.lower()
    for m in messages:
        content = (m.get("content") or "")
        if lowered in content.lower():
            idx = content.lower().find(lowered)
            start = max(0, idx - 60)
            end = min(len(content), idx + len(q) + 60)
            prefix = "…" if start > 0 else ""
            suffix = "…" if end < len(content) else ""
            return f"{prefix}{content[start:end].strip()}{suffix}"
    return None


@router.get("", response_model=SearchResponse)
def search(
    q: str = Query(..., min_length=2, max_length=200),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    term = q.strip()
    like = f"%{term}%"

    # --- Conversas (só do usuário autenticado) ---
    conv_rows = (
        db.query(Conversation)
        .filter(Conversation.user_id == user.id)
        .filter(Conversation.title.ilike(like))
        .order_by(Conversation.updated_at.desc())
        .limit(_MAX_PER_GROUP)
        .all()
    )
    conversations_out = [
        ConversationHit(
            id=str(c.id),
            title=c.title,
            snippet=_snippet_from_messages(c.messages, term),
            updated_at=(c.updated_at or c.created_at).isoformat() + "Z" if (c.updated_at or c.created_at) else "",
        )
        for c in conv_rows
    ]

    # --- Base de conhecimento ---
    kb_rows = (
        db.query(KnowledgeDocument)
        .filter(or_(
            KnowledgeDocument.name.ilike(like),
            KnowledgeDocument.original_filename.ilike(like),
            KnowledgeDocument.description.ilike(like),
        ))
        .order_by(KnowledgeDocument.id.desc())
        .limit(_MAX_PER_GROUP)
        .all()
    )
    kb_out = [
        DocumentHit(
            id=d.id,
            name=d.name,
            file_type=d.file_type,
            category=d.category.value if d.category else None,
            status=d.status.value if d.status else "queued",
        )
        for d in kb_rows
    ]

    # --- Documentos pessoais (só do usuário) ---
    user_doc_rows = (
        db.query(UserDocument)
        .filter(UserDocument.user_id == user.id)
        .filter(or_(
            UserDocument.name.ilike(like),
            UserDocument.original_filename.ilike(like),
            UserDocument.description.ilike(like),
        ))
        .order_by(UserDocument.id.desc())
        .limit(_MAX_PER_GROUP)
        .all()
    )
    my_docs_out = [
        DocumentHit(
            id=d.id,
            name=d.name,
            file_type=d.file_type,
            category=d.category.value if d.category else None,
            status=d.status.value if d.status else "queued",
        )
        for d in user_doc_rows
    ]

    # --- Usuários (só admin) ---
    users_out: list[UserHit] = []
    if user.role == UserRole.admin:
        u_rows = (
            db.query(User)
            .filter(or_(User.full_name.ilike(like), User.email.ilike(like)))
            .order_by(User.full_name)
            .limit(_MAX_PER_GROUP)
            .all()
        )
        users_out = [
            UserHit(
                id=u.id,
                full_name=u.full_name,
                email=u.email,
                role=u.role.value,
                avatar_url=None,
            )
            for u in u_rows
        ]

    return SearchResponse(
        query=term,
        conversations=conversations_out,
        knowledge_documents=kb_out,
        my_documents=my_docs_out,
        users=users_out,
    )
