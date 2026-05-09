from __future__ import annotations

import logging
from datetime import datetime, timezone

from core.config import settings
from core.indexer import index_document_async
from db.models import IndexStatus, KnowledgeDocument, UserDocument
from db.session import SessionLocal

logger = logging.getLogger(__name__)


async def task_index_document(ctx: dict, doc_id: int) -> None:
    db = SessionLocal()
    try:
        doc = db.query(KnowledgeDocument).filter(KnowledgeDocument.id == doc_id).first()
        if not doc:
            logger.error("task_index_document: doc_id=%d not found", doc_id)
            return

        doc.status = IndexStatus.processing
        doc.status_message = None
        db.commit()

        try:
            index_path = await index_document_async(
                doc.file_path,
                settings.KNOWLEDGE_INDEXES_DIR,
            )
            doc.index_path = index_path
            doc.status = IndexStatus.done
            doc.indexed_at = datetime.now(tz=timezone.utc).replace(tzinfo=None)
            doc.status_message = None
        except Exception as exc:  # noqa: BLE001
            logger.exception("Indexing failed for doc_id=%d", doc_id)
            doc.status = IndexStatus.error
            doc.status_message = str(exc)

        db.commit()
    finally:
        db.close()


async def task_index_user_document(ctx: dict, doc_id: int) -> None:
    db = SessionLocal()
    try:
        doc = db.query(UserDocument).filter(UserDocument.id == doc_id).first()
        if not doc:
            logger.error("task_index_user_document: doc_id=%d not found", doc_id)
            return

        doc.status = IndexStatus.processing
        doc.status_message = None
        db.commit()

        try:
            indexes_dir = f"{settings.USER_DOCS_INDEXES_DIR}/{doc.user_id}"
            index_path = await index_document_async(
                doc.file_path,
                indexes_dir,
            )
            doc.index_path = index_path
            doc.status = IndexStatus.done
            doc.indexed_at = datetime.now(tz=timezone.utc).replace(tzinfo=None)
            doc.status_message = None
        except Exception as exc:  # noqa: BLE001
            logger.exception("User doc indexing failed for doc_id=%d", doc_id)
            doc.status = IndexStatus.error
            doc.status_message = str(exc)

        db.commit()
    finally:
        db.close()
