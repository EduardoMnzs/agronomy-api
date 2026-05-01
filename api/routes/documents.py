import shutil
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.deps import get_current_user
from core.config import settings
from core.indexer import index_document
from db.models import SessionDocument, User
from db.session import get_db
from parsers.factory import SUPPORTED_EXTENSIONS

router = APIRouter(prefix="/documents", tags=["documents"])

SESSION_DOC_TTL_HOURS = 24


class SessionDocumentOut(BaseModel):
    id: int
    original_filename: str
    file_type: str
    created_at: str
    expires_at: str | None


@router.get("", response_model=list[SessionDocumentOut])
def list_documents(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    docs = (
        db.query(SessionDocument)
        .filter(SessionDocument.user_id == user.id)
        .order_by(SessionDocument.created_at.desc())
        .all()
    )
    return [
        SessionDocumentOut(
            id=d.id,
            original_filename=d.original_filename,
            file_type=d.file_type,
            created_at=d.created_at.isoformat(),
            expires_at=d.expires_at.isoformat() if d.expires_at else None,
        )
        for d in docs
    ]


@router.post("", status_code=status.HTTP_201_CREATED, response_model=SessionDocumentOut)
def upload_document(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    suffix = Path(file.filename).suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Formato não suportado: {suffix}")

    files_dir = Path(settings.SESSION_FILES_DIR) / str(user.id)
    files_dir.mkdir(parents=True, exist_ok=True)
    file_path = files_dir / file.filename

    with open(file_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    indexes_dir = Path(settings.SESSION_INDEXES_DIR) / str(user.id)
    index_path = index_document(file_path, str(indexes_dir))

    now = datetime.utcnow()
    doc = SessionDocument(
        user_id=user.id,
        original_filename=file.filename,
        file_type=suffix.lstrip("."),
        file_path=str(file_path),
        index_path=str(index_path),
        created_at=now,
        expires_at=now + timedelta(hours=SESSION_DOC_TTL_HOURS),
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)

    return SessionDocumentOut(
        id=doc.id,
        original_filename=doc.original_filename,
        file_type=doc.file_type,
        created_at=doc.created_at.isoformat(),
        expires_at=doc.expires_at.isoformat() if doc.expires_at else None,
    )


@router.delete("/{doc_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(doc_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    doc = db.query(SessionDocument).filter(
        SessionDocument.id == doc_id, SessionDocument.user_id == user.id
    ).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Documento não encontrado")

    for path in (doc.file_path, doc.index_path):
        if path:
            Path(path).unlink(missing_ok=True)

    db.delete(doc)
    db.commit()
