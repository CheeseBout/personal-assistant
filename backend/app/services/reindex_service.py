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
    """Create + index a new version for a single document. Returns chunk count.

    Uses an atomic "add-new-then-delete-old" strategy: new version data is
    inserted first; old version data is only removed after all new inserts
    succeed.  If the new indexing fails midway, partially-added new vectors
    are cleaned up and the old version remains intact.
    """
    max_v = db.execute(
        select(func.max(DocumentVersion.version)).where(DocumentVersion.document_id == doc.id)
    ).scalar() or 1
    new_version = max_v + 1
    old_version = max_v

    engine = get_rag_engine()

    # --- Phase 1: add new version alongside old ---
    try:
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

        result = engine.process_document_sync(doc.id, doc.file_path, doc.filename, chunks_data, version=new_version)
    except Exception:
        # Rollback: remove any partially-added new-version vectors/keywords
        try:
            engine.vector_store.delete_by_version(doc.id, new_version)
        except Exception:
            pass
        try:
            engine.keyword_index.delete_by_version(doc.id, new_version)
        except Exception:
            pass
        raise

    # --- Phase 2: new version fully indexed — safe to drop old ---
    try:
        engine.vector_store.delete_by_version(doc.id, old_version)
    except Exception as e:
        logger.warning(f"auto-reindex: failed to delete old vectors v{old_version} for {doc.id}: {e}")
    try:
        engine.keyword_index.delete_by_version(doc.id, old_version)
    except Exception as e:
        logger.warning(f"auto-reindex: failed to delete old keywords v{old_version} for {doc.id}: {e}")

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

        # Prefilter by mtime + size to avoid hashing unchanged files
        try:
            stat = Path(doc.file_path).stat()
            meta = doc.metadata_json or {}
            last_mtime = meta.get("last_mtime")
            last_size = meta.get("last_size")
            if last_mtime is not None and last_size is not None:
                if stat.st_mtime == last_mtime and stat.st_size == last_size:
                    continue
        except OSError:
            pass

        try:
            current_hash = Chunker.calculate_file_hash(doc.file_path)
        except OSError as e:
            logger.warning(f"auto-reindex: cannot hash {doc.file_path}: {e}")
            continue

        # Update mtime/size even if hash unchanged
        doc.metadata_json = {
            **(doc.metadata_json or {}),
            "last_mtime": stat.st_mtime,
            "last_size": stat.st_size,
        }

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
