from __future__ import annotations

import shutil
from pathlib import Path

from arq import ArqRedis
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.deps import get_current_user, require_admin
from core.config import settings
from db.models import DocumentCategory, IndexStatus, KnowledgeDocument, User
from db.session import get_db
from parsers.factory import SUPPORTED_EXTENSIONS

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


class KnowledgeDocumentOut(BaseModel):
    id: int
    name: str
    original_filename: str
    file_type: str
    category: str
    description: str | None
    indexed_at: str | None
    status: str
    status_message: str | None

    model_config = {"from_attributes": True}


def _serialize(d: KnowledgeDocument) -> KnowledgeDocumentOut:
    return KnowledgeDocumentOut(
        id=d.id,
        name=d.name,
        original_filename=d.original_filename,
        file_type=d.file_type,
        category=d.category.value if d.category else "outro",
        description=d.description,
        indexed_at=d.indexed_at.isoformat() if d.indexed_at else None,
        status=d.status.value if d.status else "queued",
        status_message=d.status_message,
    )


@router.get("", response_model=list[KnowledgeDocumentOut])
def list_knowledge(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    docs = db.query(KnowledgeDocument).order_by(KnowledgeDocument.id.desc()).all()
    return [_serialize(d) for d in docs]


@router.post("", status_code=status.HTTP_202_ACCEPTED, response_model=KnowledgeDocumentOut)
async def upload_knowledge(
    request: Request,
    file: UploadFile = File(...),
    name: str = Form(...),
    category: DocumentCategory = Form(DocumentCategory.outro),
    description: str = Form(""),
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    suffix = Path(file.filename).suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Formato não suportado: {suffix}")

    files_dir = Path(settings.KNOWLEDGE_FILES_DIR)
    files_dir.mkdir(parents=True, exist_ok=True)
    file_path = files_dir / file.filename

    with open(file_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    doc = KnowledgeDocument(
        name=name,
        original_filename=file.filename,
        file_type=suffix.lstrip("."),
        file_path=str(file_path),
        index_path=None,
        category=category,
        description=description or None,
        indexed_by=admin.id,
        status=IndexStatus.queued,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)

    arq: ArqRedis = request.app.state.arq
    await arq.enqueue_job("task_index_document", doc.id)

    return _serialize(doc)


@router.get("/{doc_id}/status", response_model=KnowledgeDocumentOut)
def get_status(doc_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    doc = db.query(KnowledgeDocument).filter(KnowledgeDocument.id == doc_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Documento não encontrado")
    return _serialize(doc)


@router.delete("/{doc_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_knowledge(doc_id: int, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    doc = db.query(KnowledgeDocument).filter(KnowledgeDocument.id == doc_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Documento não encontrado")

    for path in (doc.file_path, doc.index_path):
        if path:
            Path(path).unlink(missing_ok=True)

    db.delete(doc)
    db.commit()
