from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.deps import get_current_user
from core.query_engine import QueryResult, query
from db.models import KnowledgeDocument, SessionDocument, User
from db.session import get_db

router = APIRouter(prefix="/query", tags=["query"])


class QueryRequest(BaseModel):
    question: str
    knowledge_ids: list[int] | None = None
    document_ids: list[int] | None = None
    user_data: dict | None = None
    model: str | None = None


class SourceOut(BaseModel):
    ref: int
    doc_id: int | str
    doc_name: str
    page: int
    section: str


class QueryResponse(BaseModel):
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

    # Base de conhecimento (todos disponíveis se nenhum for especificado)
    if body.knowledge_ids:
        kb_docs = db.query(KnowledgeDocument).filter(KnowledgeDocument.id.in_(body.knowledge_ids)).all()
    else:
        kb_docs = db.query(KnowledgeDocument).filter(KnowledgeDocument.index_path.isnot(None)).all()

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

    # Documentos do usuário
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

    result: QueryResult = query(
        question=body.question,
        index_entries=index_entries,
        user_data=body.user_data,
        model=body.model,
    )

    return QueryResponse(
        answer=result.answer,
        sources=[
            SourceOut(
                ref=s.ref,
                doc_id=s.doc_id,
                doc_name=s.doc_name,
                page=s.page,
                section=s.section,
            )
            for s in result.sources
        ],
        model_used=result.model_used,
    )
