from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from arq import ArqRedis
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import FileResponse, StreamingResponse
from jose import JWTError, jwt
from pydantic import BaseModel
from sqlalchemy import case, func, or_
from sqlalchemy.orm import Session

from api.deps import get_current_user, public_url, require_admin
from api.uploads import safe_extension, save_upload_async
from core import storage as store
from core.config import settings
from db.models import DocumentCategory, IndexStatus, KnowledgeDocument, QueryLog, User
from db.session import get_db
from parsers.factory import SUPPORTED_EXTENSIONS
from core.timefmt import utc_iso

router = APIRouter(prefix="/knowledge", tags=["knowledge"])

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


class KnowledgeDocumentOut(BaseModel):
    id: int
    name: str
    original_filename: str
    file_type: str
    category: str
    tags: list[str] = []
    description: str | None
    indexed_at: str | None
    indexed_by_name: str | None = None
    status: str
    status_message: str | None

    model_config = {"from_attributes": True}


class KnowledgeDocumentDetail(KnowledgeDocumentOut):
    url: str | None = None
    content: str | None = None


class KnowledgeStats(BaseModel):
    total_files: int
    storage_used_bytes: int
    total_queries: int
    health_score: int


_MAX_TAGS = 12
_MAX_TAG_LEN = 64


def _normalize_tags(raw) -> list[str]:
    """Trim, dedup (case-insensitive), cap length and count. Accepts list or
    JSON/CSV string (multipart form sends strings)."""
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = raw.strip()
        if not raw:
            return []
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                # Um objeto JSON não é uma lista de tags — virar str(dict) geraria
                # uma "tag" lixo. Trata como entrada inválida.
                return []
            items = parsed if isinstance(parsed, list) else [parsed]
        except (ValueError, TypeError):
            items = raw.split(",")
    elif isinstance(raw, (list, tuple)):
        items = list(raw)
    else:
        return []

    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        tag = str(item).strip()[:_MAX_TAG_LEN]
        if not tag:
            continue
        key = tag.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(tag)
        if len(out) >= _MAX_TAGS:
            break
    return out


def _display_name(full_name: str | None, email: str | None) -> str | None:
    if full_name and full_name.strip():
        return full_name.strip()
    return email


def _serialize(d: KnowledgeDocument, indexed_by_name: str | None = None) -> KnowledgeDocumentOut:
    return KnowledgeDocumentOut(
        id=d.id,
        name=d.name,
        original_filename=d.original_filename,
        file_type=d.file_type,
        category=d.category.value if d.category else "outro",
        tags=list(d.tags or []),
        description=d.description,
        indexed_at=utc_iso(d.indexed_at),
        indexed_by_name=indexed_by_name,
        status=d.status.value if d.status else "queued",
        status_message=d.status_message,
    )


def _make_download_token(doc_id: int, user_id: int) -> str:
    payload = {
        "doc_id": doc_id,
        "user_id": user_id,
        "type": "download",
        "exp": datetime.now(tz=timezone.utc).replace(tzinfo=None) + timedelta(minutes=_DOWNLOAD_TOKEN_TTL_MIN),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def _verify_download_token(token: str, doc_id: int) -> int:
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=401, detail="Token inválido ou expirado")
    if payload.get("type") != "download" or int(payload.get("doc_id", -1)) != doc_id:
        raise HTTPException(status_code=403, detail="Token não autorizado para este documento")
    return int(payload.get("user_id", -1))


def _read_text_preview(file_path: str, file_type: str) -> str | None:
    key = store._to_key(file_path)
    try:
        if file_type == "docx":
            with store.temp_download(key, suffix=".docx") as tmp:
                from parsers.docx_parser import DOCXParser
                text = DOCXParser().parse(tmp).text
        else:
            text = store.download_bytes(key).decode("utf-8", errors="replace")
    except Exception:
        return None

    if len(text) > _MAX_PREVIEW_CHARS:
        text = text[:_MAX_PREVIEW_CHARS] + "\n\n[... conteúdo truncado ...]"
    return text


@router.get("", response_model=list[KnowledgeDocumentOut])
def list_knowledge(
    search: str | None = Query(None),
    category: list[str] | None = Query(None),
    tags: list[str] | None = Query(None),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    q = (
        db.query(KnowledgeDocument, User.full_name, User.email)
        .outerjoin(User, KnowledgeDocument.indexed_by == User.id)
    )

    term = (search or "").strip()
    if term:
        like = f"%{term}%"
        q = q.filter(or_(
            KnowledgeDocument.name.ilike(like),
            KnowledgeDocument.original_filename.ilike(like),
            KnowledgeDocument.description.ilike(like),
        ))

    cats = [c.strip() for c in (category or []) if c and c.strip()]
    if cats:
        q = q.filter(KnowledgeDocument.category.in_(cats))

    # Filtro por tags: documento aparece se possuir QUALQUER uma das tags pedidas
    # (overlap). Usa o índice GIN.
    wanted = [t.strip() for t in (tags or []) if t and t.strip()]
    if wanted:
        q = q.filter(KnowledgeDocument.tags.overlap(wanted))

    rows = q.order_by(KnowledgeDocument.id.desc()).all()
    return [_serialize(doc, _display_name(full_name, email)) for doc, full_name, email in rows]


@router.get("/tags", response_model=list[str])
def list_tags(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    """Tags distintas em uso na base de conhecimento, em ordem alfabética."""
    rows = db.query(func.unnest(KnowledgeDocument.tags)).distinct().all()
    return sorted({r[0] for r in rows if r[0]}, key=lambda s: s.lower())


@router.post("", status_code=status.HTTP_202_ACCEPTED, response_model=KnowledgeDocumentOut)
async def upload_knowledge(
    request: Request,
    file: UploadFile = File(...),
    name: str = Form(...),
    category: DocumentCategory = Form(DocumentCategory.outro),
    description: str = Form(""),
    tags: str = Form(""),
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    suffix = safe_extension(file.filename, SUPPORTED_EXTENSIONS)

    files_dir = Path(settings.KNOWLEDGE_FILES_DIR)
    file_key, file_size = await save_upload_async(file, files_dir, suffix)

    doc = KnowledgeDocument(
        name=name,
        original_filename=file.filename,
        file_type=suffix.lstrip("."),
        file_path=file_key,
        index_path=None,
        category=category,
        tags=_normalize_tags(tags),
        description=description or None,
        indexed_by=admin.id,
        status=IndexStatus.queued,
        file_size_bytes=file_size,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)

    arq: ArqRedis = request.app.state.arq
    await arq.enqueue_job("task_index_document", doc.id)

    return _serialize(doc, _display_name(admin.full_name, admin.email))


@router.get("/stats", response_model=KnowledgeStats)
def get_stats(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    total_files, storage_used, done_count, error_count = db.query(
        func.count(KnowledgeDocument.id),
        func.coalesce(func.sum(KnowledgeDocument.file_size_bytes), 0),
        func.sum(case((KnowledgeDocument.status == IndexStatus.done, 1), else_=0)),
        func.sum(case((KnowledgeDocument.status == IndexStatus.error, 1), else_=0)),
    ).one()

    total_queries, failed_queries = db.query(
        func.count(QueryLog.id),
        func.sum(case((QueryLog.success.is_(False), 1), else_=0)),
    ).one()

    total_files = int(total_files or 0)
    done_count = int(done_count or 0)
    error_count = int(error_count or 0)
    total_queries = int(total_queries or 0)
    failed_queries = int(failed_queries or 0)

    index_health = (done_count / total_files) if total_files else 1.0
    query_health = (1 - failed_queries / total_queries) if total_queries else 1.0
    health_score = round(100 * (0.7 * index_health + 0.3 * query_health))
    if error_count and health_score == 100:
        health_score = 99

    return KnowledgeStats(
        total_files=total_files,
        storage_used_bytes=int(storage_used or 0),
        total_queries=total_queries,
        health_score=health_score,
    )


@router.get("/{doc_id}/status", response_model=KnowledgeDocumentOut)
def get_status(doc_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    row = (
        db.query(KnowledgeDocument, User.full_name, User.email)
        .outerjoin(User, KnowledgeDocument.indexed_by == User.id)
        .filter(KnowledgeDocument.id == doc_id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Documento não encontrado")
    doc, full_name, email = row
    return _serialize(doc, _display_name(full_name, email))


@router.get("/{doc_id}", response_model=KnowledgeDocumentDetail)
def get_knowledge(
    doc_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    doc = db.query(KnowledgeDocument).filter(KnowledgeDocument.id == doc_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Documento não encontrado")

    base = _serialize(doc).model_dump()
    detail = KnowledgeDocumentDetail(**base)

    ft = (doc.file_type or "").lower()

    token = _make_download_token(doc.id, user.id)
    detail.url = public_url(request, "download_knowledge_file", doc_id=doc.id) + f"?token={token}"

    if ft in _TEXT_PREVIEW_TYPES:
        detail.content = _read_text_preview(doc.file_path, ft)

    return detail


@router.get("/{doc_id}/file", name="download_knowledge_file")
def download_knowledge_file(
    doc_id: int,
    token: str = Query(...),
    db: Session = Depends(get_db),
):
    user_id = _verify_download_token(token, doc_id)

    issuer = db.query(User).filter(User.id == user_id).first()
    if not issuer or issuer.status.value == "inactive":
        raise HTTPException(status_code=403, detail="Token não autorizado")

    doc = db.query(KnowledgeDocument).filter(KnowledgeDocument.id == doc_id).first()
    if not doc or not doc.file_path:
        raise HTTPException(status_code=404, detail="Documento não encontrado")

    ft = (doc.file_type or "").lower()
    media_type = _MIME_TYPES.get(ft, "application/octet-stream")
    download_name = doc.original_filename or doc.name or f"documento.{ft}"
    if ft and not download_name.lower().endswith(f".{ft}"):
        download_name = f"{Path(download_name).stem}.{ft}"

    key = store._to_key(doc.file_path)

    if settings.STORAGE_BACKEND == "s3":
        s3 = store._storage()
        obj = s3._s3.get_object(Bucket=s3._bucket, Key=s3._fk(key))
        return StreamingResponse(
            obj["Body"].iter_chunks(1 << 16),
            media_type=media_type,
            headers={
                "Content-Disposition": f'attachment; filename="{download_name}"',
                "Content-Length": str(obj["ContentLength"]),
            },
        )

    local = store.resolve_local_path(key)
    if not local or not local.exists():
        raise HTTPException(status_code=404, detail="Arquivo não encontrado no disco")

    return FileResponse(
        local,
        media_type=media_type,
        filename=download_name,
        headers={"Content-Disposition": f'attachment; filename="{download_name}"'},
    )


class KnowledgeDocumentUpdate(BaseModel):
    name: str | None = None
    tags: list[str] | None = None
    # Reatribuir o responsável pela indexação. Necessário quando um admin sobe o
    # arquivo em nome de outra pessoa — a atribuição automática registra quem fez
    # o upload, não o autor real do documento.
    indexed_by: int | None = None


@router.patch("/{doc_id}", response_model=KnowledgeDocumentOut)
def update_knowledge(
    doc_id: int,
    body: KnowledgeDocumentUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    doc = db.query(KnowledgeDocument).filter(KnowledgeDocument.id == doc_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Documento não encontrado")

    if body.name is not None:
        name = body.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="O nome não pode ficar vazio")
        doc.name = name[:255]

    if body.tags is not None:
        doc.tags = _normalize_tags(body.tags)

    issuer: User | None = None
    if body.indexed_by is not None:
        issuer = db.query(User).filter(User.id == body.indexed_by).first()
        if not issuer:
            raise HTTPException(status_code=400, detail="Usuário responsável não encontrado")
        doc.indexed_by = issuer.id

    db.commit()
    db.refresh(doc)

    if issuer is None and doc.indexed_by:
        issuer = db.query(User).filter(User.id == doc.indexed_by).first()
    return _serialize(doc, _display_name(issuer.full_name, issuer.email) if issuer else None)


@router.delete("/{doc_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_knowledge(doc_id: int, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    doc = db.query(KnowledgeDocument).filter(KnowledgeDocument.id == doc_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Documento não encontrado")

    for path in (doc.file_path, doc.index_path):
        if path:
            store.delete_file(store._to_key(path))

    db.delete(doc)
    db.commit()
