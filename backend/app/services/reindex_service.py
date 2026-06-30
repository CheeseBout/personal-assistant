"""Auto re-index service (§10.2: "cập nhật index tự động khi file thay đổi").

Sync implementation so the scheduler (which runs on APScheduler's sync
BackgroundScheduler) can scan active documents, detect on-disk changes via
file-hash comparison, and create a new version for any that changed — without
the async DB session used by the upload API.
"""

import uuid
from datetime import datetime
from typing import Any, Dict

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from ..models.database import Document, DocumentVersion, Chunk, AuditLog
from .document import DocumentParser, Chunker
from .rag_singleton import get_rag_engine
from ..core.logging_config import logger


def _reindex_one(db: Session, doc: Document, current_hash: str) -> int:
    """Create + index a new version for a single document. Returns chunk count."""
    max_v = db.execute(
        select(func.max(DocumentVersion.version)).where(DocumentVersion.document_id == doc.id)
    ).scalar() or 1
    new_version = max_v + 1

    engine = get_rag_engine()
    # Drop previous version vectors/keywords from active retrieval.
    engine.vector_store.delete_by_doc_id(doc.id)
    engine.keyword_index.delete_by_doc_id(doc.id)

    segments = DocumentParser.parse_segments(doc.file_path)
    chunks_data = Chunker.chunk_segments(segments)

    for cd in chunks_data:
        db.add(Chunk(
            id=f"{doc.id}_v{new_version}_{cd['index']}",
            document_id=doc.id,
            version=new_version,
            chunk_index=cd['index'],
            content=cd['content'],
            embedding_id=f"{doc.id}_v{new_version}_{cd['index']}",
            metadata_json={"start": cd['start_char'], "end": cd['end_char'], **(cd.get('meta') or {})},
        ))

    result = engine.process_document(doc.id, doc.file_path, doc.filename, chunks_data, version=new_version)

    db.add(DocumentVersion(
        id=str(uuid.uuid4()),
        document_id=doc.id,
        version=new_version,
        file_path=doc.file_path,
        chunk_count=result["chunk_count"],
    ))
    doc.file_hash = current_hash
    doc.metadata_json = {**(doc.metadata_json or {}), "current_version": new_version}
    db.add(doc)
    db.add(AuditLog(
        id=str(uuid.uuid4()), actor="system", action="document_reindexed",
        details={"doc_id": doc.id, "filename": doc.filename, "version": new_version,
                 "chunks": result["chunk_count"], "trigger": "scheduler"},
    ))
    return result["chunk_count"]


def reindex_changed_documents(db: Session) -> Dict[str, Any]:
    """Scan active documents; re-index any whose file changed on disk.

    Returns a summary {scanned, reindexed, missing, details}.
    """
    from pathlib import Path

    docs = db.execute(select(Document).where(Document.is_active == True)).scalars().all()  # noqa: E712
    reindexed, missing, details = 0, 0, []
    for doc in docs:
        if not doc.file_path or not Path(doc.file_path).exists():
            missing += 1
            continue
        try:
            current_hash = Chunker.calculate_file_hash(doc.file_path)
        except OSError as e:
            logger.warning(f"auto-reindex: cannot hash {doc.file_path}: {e}")
            continue
        if current_hash == doc.file_hash:
            continue
        try:
            chunks = _reindex_one(db, doc, current_hash)
            db.commit()
            reindexed += 1
            details.append({"doc_id": doc.id, "filename": doc.filename, "chunks": chunks})
        except Exception as e:
            db.rollback()
            logger.error(f"auto-reindex failed for {doc.id}: {e}")

    if reindexed:
        logger.info(f"auto-reindex: re-indexed {reindexed} changed document(s)")
    return {"scanned": len(docs), "reindexed": reindexed, "missing": missing, "details": details}
