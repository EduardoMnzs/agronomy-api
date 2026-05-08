from __future__ import annotations

import shutil
from datetime import datetime, timedelta
from pathlib import Path

from arq import ArqRedis
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import FileResponse
from jose import JWTError, jwt
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.deps import get_current_user
from core.config import settings
from db.models import DocumentCategory, IndexStatus, User, UserDocument
from db.session import get_db
from parsers.factory import SUPPORTED_EXTENSIONS

router = APIRouter(prefix="/my-documents", tags=["my-documents"])

_DOWNLOAD_TOKEN_TTL_MIN = 15
_TEXT_PREVIEW_TYPES = {"md", "json", "csv", "docx", "txt"}
_MAX_PREVIEW_CHARS = 2_000_000

_MIME_TYPES = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "xls": "application/vnd.ms-excel",
    "csv": "text/csv; charset=utf-8",
    "json": "application/json; charset=utf-8",
    "md": "text/markdown; charset=utf-8",
    "txt": "text/plain; charset=utf-8",
}


class UserDocumentOut(BaseModel):
    id: int
    name: str
    original_filename: str
    file_type: str
    category: str
    description: str | None
    status: str
    status_message: str | None
    indexed_at: str | None
    created_at: str
    expires_at: str | None

    model_config = {"from_attributes": True}


class UserDocumentDetail(UserDocumentOut):
    url: str | None = None
    content: str | None = None


def _serialize(d: UserDocument) -> UserDocumentOut:
    return UserDocumentOut(
        id=d.id,
        name=d.name,
        original_filename=d.original_filename,
        file_type=d.file_type,
        category=d.category.value if d.category else "outro",
        description=d.description,
        status=d.status.value if d.status else "queued",
        status_message=d.status_message,
        indexed_at=d.indexed_at.isoformat() + "Z" if d.indexed_at else None,
        created_at=d.created_at.isoformat() + "Z" if d.created_at else None,
        expires_at=d.expires_at.isoformat() + "Z" if d.expires_at else None,
    )


def _make_download_token(doc_id: int, user_id: int) -> str:
    payload = {
        "doc_id": doc_id,
        "user_id": user_id,
        "type": "user_download",
        "exp": datetime.utcnow() + timedelta(minutes=_DOWNLOAD_TOKEN_TTL_MIN),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def _verify_download_token(token: str, doc_id: int) -> int:
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=401, detail="Token inválido ou expirado")
    if payload.get("type") != "user_download" or int(payload.get("doc_id", -1)) != doc_id:
        raise HTTPException(status_code=403, detail="Token não autorizado para este documento")
    return int(payload.get("user_id", -1))


def _read_text_preview(file_path: str, file_type: str) -> str | None:
    path = Path(file_path)
    if not path.exists():
        return None

    if file_type == "docx":
        try:
            from parsers.docx_parser import DOCXParser
            parsed = DOCXParser().parse(path)
            text = parsed.text
        except Exception:
            return None
    else:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return None

    if len(text) > _MAX_PREVIEW_CHARS:
        text = text[:_MAX_PREVIEW_CHARS] + "\n\n[... conteúdo truncado ...]"
    return text


@router.get("", response_model=list[UserDocumentOut])
def list_user_documents(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    now = datetime.utcnow()
    docs = (
        db.query(UserDocument)
        .filter(UserDocument.user_id == user.id)
        .filter((UserDocument.expires_at.is_(None)) | (UserDocument.expires_at > now))
        .order_by(UserDocument.id.desc())
        .all()
    )
    return [_serialize(d) for d in docs]


@router.post("", status_code=status.HTTP_202_ACCEPTED, response_model=UserDocumentOut)
async def upload_user_document(
    request: Request,
    file: UploadFile = File(...),
    name: str = Form(...),
    category: DocumentCategory = Form(DocumentCategory.outro),
    description: str = Form(""),
    persistent: bool = Form(True),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    suffix = Path(file.filename).suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Formato não suportado: {suffix}")

    files_dir = Path(settings.USER_DOCS_FILES_DIR) / str(user.id)
    files_dir.mkdir(parents=True, exist_ok=True)
    file_path = files_dir / file.filename

    with open(file_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    try:
        file_size = file_path.stat().st_size
    except OSError:
        file_size = None

    now = datetime.utcnow()
    doc = UserDocument(
        user_id=user.id,
        name=name,
        original_filename=file.filename,
        file_type=suffix.lstrip("."),
        file_path=str(file_path),
        index_path=None,
        category=category,
        description=description or None,
        file_size_bytes=file_size,
        status=IndexStatus.queued,
        created_at=now,
        expires_at=None if persistent else now + timedelta(hours=24),
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)

    arq: ArqRedis = request.app.state.arq
    await arq.enqueue_job("task_index_user_document", doc.id)

    return _serialize(doc)


@router.get("/{doc_id}/status", response_model=UserDocumentOut)
def get_status(
    doc_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    doc = db.query(UserDocument).filter(
        UserDocument.id == doc_id,
        UserDocument.user_id == user.id,
    ).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Documento não encontrado")
    return _serialize(doc)


@router.get("/{doc_id}", response_model=UserDocumentDetail)
def get_user_document(
    doc_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    doc = db.query(UserDocument).filter(
        UserDocument.id == doc_id,
        UserDocument.user_id == user.id,
    ).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Documento não encontrado")

    base = _serialize(doc).model_dump()
    detail = UserDocumentDetail(**base)

    ft = (doc.file_type or "").lower()
    token = _make_download_token(doc.id, user.id)
    detail.url = str(request.url_for("download_user_document", doc_id=doc.id)) + f"?token={token}"

    if ft in _TEXT_PREVIEW_TYPES:
        detail.content = _read_text_preview(doc.file_path, ft)

    return detail


@router.get("/{doc_id}/file", name="download_user_document")
def download_user_document(
    doc_id: int,
    token: str = Query(...),
    db: Session = Depends(get_db),
):
    user_id = _verify_download_token(token, doc_id)

    doc = db.query(UserDocument).filter(
        UserDocument.id == doc_id,
        UserDocument.user_id == user_id,
    ).first()
    if not doc or not doc.file_path:
        raise HTTPException(status_code=404, detail="Documento não encontrado")

    path = Path(doc.file_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Arquivo não encontrado no disco")

    ft = (doc.file_type or "").lower()
    media_type = _MIME_TYPES.get(ft, "application/octet-stream")

    download_name = doc.original_filename or doc.name or f"documento.{ft}"
    if ft and not download_name.lower().endswith(f".{ft}"):
        download_name = f"{Path(download_name).stem}.{ft}"

    return FileResponse(
        path,
        media_type=media_type,
        filename=download_name,
        headers={"Content-Disposition": f'attachment; filename="{download_name}"'},
    )


@router.delete("/{doc_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user_document(
    doc_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    doc = db.query(UserDocument).filter(
        UserDocument.id == doc_id,
        UserDocument.user_id == user.id,
    ).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Documento não encontrado")

    for path in (doc.file_path, doc.index_path):
        if path:
            Path(path).unlink(missing_ok=True)

    db.delete(doc)
    db.commit()
