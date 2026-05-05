from __future__ import annotations

import time
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.deps import get_current_user
from core.query_engine import QueryResult, query
from db.models import Conversation, IndexStatus, KnowledgeDocument, QueryLog, SessionDocument, User
from db.session import get_db

router = APIRouter(prefix="/query", tags=["query"])


class QueryRequest(BaseModel):
    question: str
    knowledge_ids: list[int] | None = None
    document_ids: list[int] | None = None
    user_data: dict | None = None
    model: str | None = None
    conversation_id: str | None = None


class SourceOut(BaseModel):
    ref: int
    doc_id: int | str
    doc_name: str
    page: int
    section: str


class QueryResponse(BaseModel):
    conversation_id: str
    answer: str
    sources: list[SourceOut]
    model_used: str


@router.post("", response_model=QueryResponse)
def run_query(
    body: QueryRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if not body.question.strip():
        raise HTTPException(status_code=400, detail="Pergunta não pode ser vazia")

    index_entries: list[dict] = []

    if body.knowledge_ids:
        kb_docs = db.query(KnowledgeDocument).filter(
            KnowledgeDocument.id.in_(body.knowledge_ids),
            KnowledgeDocument.status == IndexStatus.done,
        ).all()
    else:
        kb_docs = db.query(KnowledgeDocument).filter(
            KnowledgeDocument.status == IndexStatus.done,
            KnowledgeDocument.index_path.isnot(None),
        ).all()

    for doc in kb_docs:
        if doc.index_path:
            index_entries.append({
                "doc_id": doc.id,
                "doc_name": doc.name,
                "index_path": doc.index_path,
                "file_path": doc.file_path,
                "description": doc.description,
                "category": doc.category.value if doc.category else None,
            })

    if body.document_ids:
        session_docs = db.query(SessionDocument).filter(
            SessionDocument.id.in_(body.document_ids),
            SessionDocument.user_id == user.id,
        ).all()
        for doc in session_docs:
            if doc.index_path:
                index_entries.append({
                    "doc_id": f"session_{doc.id}",
                    "doc_name": doc.original_filename,
                    "index_path": doc.index_path,
                    "file_path": doc.file_path,
                })

    if not index_entries:
        raise HTTPException(status_code=400, detail="Nenhum documento indexado disponível para consulta")

    history: list[dict] = []
    if conv := (
        db.query(Conversation).filter(
            Conversation.id == uuid.UUID(body.conversation_id),
            Conversation.user_id == user.id,
        ).first()
        if body.conversation_id else None
    ):
        history = [
            {"role": m["role"], "content": m["content"]}
            for m in (conv.messages or [])
            if m.get("role") in ("user", "assistant") and m.get("content")
        ]

    started = time.monotonic()
    try:
        result: QueryResult = query(
            question=body.question,
            index_entries=index_entries,
            user_data=body.user_data,
            model=body.model,
            history=history,
        )
    except Exception as exc:
        _log_query(
            db,
            user_id=user.id,
            conversation_id=body.conversation_id,
            question=body.question,
            model_used=body.model,
            latency_ms=int((time.monotonic() - started) * 1000),
            success=False,
            error_message=str(exc)[:1000],
        )
        raise

    latency_ms = int((time.monotonic() - started) * 1000)

    sources_out = [
        SourceOut(ref=s.ref, doc_id=s.doc_id, doc_name=s.doc_name, page=s.page, section=s.section)
        for s in result.sources
    ]

    conv = _upsert_conversation(db, user.id, body.conversation_id, body.question, result.answer, sources_out)

    _log_query(
        db,
        user_id=user.id,
        conversation_id=str(conv.id),
        question=body.question,
        model_used=result.model_used,
        latency_ms=latency_ms,
        success=True,
        error_message=None,
    )

    return QueryResponse(
        conversation_id=str(conv.id),
        answer=result.answer,
        sources=sources_out,
        model_used=result.model_used,
    )


def _log_query(
    db: Session,
    *,
    user_id: int | None,
    conversation_id: str | None,
    question: str,
    model_used: str | None,
    latency_ms: int,
    success: bool,
    error_message: str | None,
) -> None:
    try:
        conv_uuid: uuid.UUID | None = None
        if conversation_id:
            try:
                conv_uuid = uuid.UUID(conversation_id)
            except ValueError:
                conv_uuid = None
        db.add(QueryLog(
            user_id=user_id,
            conversation_id=conv_uuid,
            question=question[:4000],
            model_used=(model_used or "")[:128] or None,
            latency_ms=latency_ms,
            success=success,
            error_message=error_message,
        ))
        db.commit()
    except Exception:
        db.rollback()


def _upsert_conversation(
    db: Session,
    user_id: int,
    conversation_id: str | None,
    question: str,
    answer: str,
    sources: list[SourceOut],
) -> Conversation:
    conv: Conversation | None = None

    if conversation_id:
        try:
            uid = uuid.UUID(conversation_id)
            conv = db.query(Conversation).filter(
                Conversation.id == uid,
                Conversation.user_id == user_id,
            ).first()
        except ValueError:
            pass

    new_messages = [
        {"role": "user", "content": question, "citations": None},
        {"role": "assistant", "content": answer, "citations": [s.model_dump() for s in sources]},
    ]

    if conv:
        conv.messages = (conv.messages or []) + new_messages
        conv.updated_at = datetime.utcnow()
    else:
        title = question[:60] + ("…" if len(question) > 60 else "")
        conv = Conversation(
            user_id=user_id,
            title=title,
            messages=new_messages,
        )
        db.add(conv)

    db.commit()
    db.refresh(conv)
    return conv
