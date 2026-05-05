from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from arq import ArqRedis

from core.config import settings
from core.indexer import index_document_async
from db.models import IndexStatus, KnowledgeDocument
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
                Path(doc.file_path.replace("\\", "/")),
                settings.KNOWLEDGE_INDEXES_DIR,
            )
            doc.index_path = str(index_path)
            doc.status = IndexStatus.done
            doc.indexed_at = datetime.utcnow()
            doc.status_message = None
        except Exception as exc:  # noqa: BLE001
            logger.exception("Indexing failed for doc_id=%d", doc_id)
            doc.status = IndexStatus.error
            doc.status_message = str(exc)

        db.commit()
    finally:
        db.close()
