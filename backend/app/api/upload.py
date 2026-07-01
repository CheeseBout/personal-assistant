from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, func
from typing import List, Dict, Any, Optional
from pathlib import Path
import io
import uuid
import zipfile

from ..models.async_db import get_async_db, Document, DocumentVersion, Chunk, DocumentMetadata, AuditLog
from ..services.document import DocumentParser, Chunker
from ..services.rag_singleton import get_rag_engine
from ..core.config import settings
from ..core.logging_config import logger

router = APIRouter(prefix="/api", tags=["documents"])

# Maximum total uncompressed size for DOCX/XLSX zip bombs (200 MB).
MAX_UNCOMPRESSED_SIZE = 200 * 1024 * 1024


def _matches_magic(ext: str, content: bytes) -> bool:
    """Check that the file's leading bytes match what the extension claims.

    Catches the simplest case of someone uploading malware renamed as .pdf.
    Plain-text formats (.txt, .md) are accepted as-is — no reliable signature.
    For DOCX/XLSX, also validates the internal OPC structure and rejects
    zip bombs whose total uncompressed size exceeds MAX_UNCOMPRESSED_SIZE.
    """
    if not content:
        return False
    if ext == ".pdf":
        return content[:5] == b"%PDF-"
    if ext in (".docx", ".xlsx"):
        # Both are zip-based OPC containers
        if content[:4] != b"PK\x03\x04":
            return False
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as zf:
                names = zf.namelist()
                if ext == ".docx" and "word/document.xml" not in names:
                    return False
                if ext == ".xlsx" and "xl/workbook.xml" not in names:
                    return False
                total_uncompressed = sum(info.file_size for info in zf.infolist())
                if total_uncompressed > MAX_UNCOMPRESSED_SIZE:
                    return False
        except zipfile.BadZipFile:
            return False
        return True
    if ext in (".txt", ".md"):
        # No signature — best we can do is reject obvious binaries (NUL bytes
        # are very rare in legitimate text files).
        return b"\x00" not in content[:512]
    return True


async def _index_new_version(
    db: AsyncSession,
    doc: Document,
    file_path: str,
    version: int,
) -> Dict[str, Any]:
    """Parse → chunk → persist chunks → embed + keyword index for one version."""
    segments = DocumentParser.parse_segments(file_path)
    chunks_data = Chunker.chunk_segments(segments)

    for chunk_data in chunks_data:
        chunk = Chunk(
            id=f"{doc.id}_v{version}_{chunk_data['index']}",
            document_id=doc.id,
            version=version,
            chunk_index=chunk_data['index'],
            content=chunk_data['content'],
            embedding_id=f"{doc.id}_v{version}_{chunk_data['index']}",
            metadata_json={
                "start": chunk_data['start_char'],
                "end": chunk_data['end_char'],
                **(chunk_data.get('meta') or {}),
            },
        )
        db.add(chunk)

    result = await get_rag_engine().process_document(
        doc.id, file_path, doc.filename, chunks_data, version=version
    )

    dv = DocumentVersion(
        id=str(uuid.uuid4()),
        document_id=doc.id,
        version=version,
        file_path=file_path,
        chunk_count=result["chunk_count"],
    )
    db.add(dv)
    return result


@router.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_async_db),
) -> Dict[str, Any]:
    """Upload and process a document, with hash-based versioning."""
    allowed_extensions = ['.txt', '.pdf', '.md', '.docx', '.xlsx']
    file_ext = Path(file.filename).suffix.lower()
    if file_ext not in allowed_extensions:
        raise HTTPException(
            status_code=400,
            detail=f"File type not supported. Allowed: {', '.join(allowed_extensions)}",
        )

    try:
        content = await file.read()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read file: {e}")

    if len(content) > settings.MAX_FILE_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"File too large. Maximum size is {settings.MAX_FILE_SIZE // (1024 * 1024)}MB",
        )

    if not _matches_magic(file_ext, content):
        raise HTTPException(
            status_code=400,
            detail=f"File content does not match its {file_ext} extension",
        )

    # Check for an existing document with the same filename (versioning).
    # Deleted documents are hard-deleted, so any remaining row is active.
    existing_stmt = select(Document).where(Document.filename == file.filename)
    existing = (await db.execute(existing_stmt)).scalar_one_or_none()

    upload_dir = Path(settings.UPLOAD_DIR)
    upload_dir.mkdir(parents=True, exist_ok=True)

    if existing is not None:
        # Compute hash of the new content to decide whether to make a new version
        import hashlib
        new_hash = hashlib.sha256(content).hexdigest()
        if new_hash == existing.file_hash:
            return {
                "success": True,
                "doc_id": existing.id,
                "filename": existing.filename,
                "unchanged": True,
                "message": "File không thay đổi (hash trùng), bỏ qua re-index.",
            }
        return await _create_new_version(db, existing, content, new_hash, upload_dir)

    # ---- First-time upload ----
    doc_id = str(uuid.uuid4())
    file_path = upload_dir / f"{doc_id}_v1_{file.filename}"
    try:
        with open(file_path, "wb") as f:
            f.write(content)
        file_hash = Chunker.calculate_file_hash(str(file_path))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save file: {e}")

    doc = Document(
        id=doc_id,
        filename=file.filename,
        file_path=str(file_path),
        mime_type=file.content_type,
        file_hash=file_hash,
        file_size=len(content),
        metadata_json={"current_version": 1},
    )
    db.add(doc)

    for key, value in {
        "file_size": len(content),
        "file_extension": file_ext,
        "current_version": 1,
    }.items():
        db.add(DocumentMetadata(id=str(uuid.uuid4()), document_id=doc_id, key=key, value=str(value)))

    await db.commit()

    try:
        result = await _index_new_version(db, doc, str(file_path), version=1)
        db.add(AuditLog(
            id=str(uuid.uuid4()), actor="user", action="document_uploaded",
            details={"doc_id": doc_id, "filename": file.filename, "version": 1, "chunks": result["chunk_count"]},
        ))
        await db.commit()
        return {
            "success": True, "doc_id": doc_id, "filename": file.filename,
            "version": 1, "chunk_count": result["chunk_count"],
            "total_chars": result["total_chars"], "file_size": len(content),
        }
    except Exception as e:
        await db.rollback()
        if file_path.exists():
            file_path.unlink()
        logger.error(f"Upload failed: {e}")
        raise HTTPException(status_code=500, detail=f"Upload failed: {e}")


async def _create_new_version(
    db: AsyncSession,
    doc: Document,
    content: bytes,
    new_hash: str,
    upload_dir: Path,
) -> Dict[str, Any]:
    """Create and index a new version of an existing document.

    Uses an atomic "add-new-then-delete-old" strategy: new version data is
    inserted alongside the old; the old version is only removed after all
    new inserts succeed.  On failure, partially-added new vectors are cleaned
    up and the old version remains intact.
    """
    # Determine next version number
    max_v = (await db.execute(
        select(func.max(DocumentVersion.version)).where(DocumentVersion.document_id == doc.id)
    )).scalar() or 1
    new_version = max_v + 1
    old_version = max_v

    file_path = upload_dir / f"{doc.id}_v{new_version}_{doc.filename}"
    try:
        with open(file_path, "wb") as f:
            f.write(content)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save file: {e}")

    engine = get_rag_engine()

    try:
        # --- Phase 1: add new version alongside old ---
        # Update document head pointer
        doc.file_path = str(file_path)
        doc.file_hash = new_hash
        doc.file_size = len(content)
        doc.metadata_json = {**(doc.metadata_json or {}), "current_version": new_version}
        db.add(doc)

        result = await _index_new_version(db, doc, str(file_path), version=new_version)

        # --- Phase 2: new version fully indexed — safe to drop old ---
        try:
            engine.vector_store.delete_by_version(doc.id, old_version)
        except Exception as e:
            logger.warning(f"new-version: failed to delete old vectors v{old_version} for {doc.id}: {e}")
        try:
            engine.keyword_index.delete_by_version(doc.id, old_version)
        except Exception as e:
            logger.warning(f"new-version: failed to delete old keywords v{old_version} for {doc.id}: {e}")

        db.add(AuditLog(
            id=str(uuid.uuid4()), actor="user", action="document_reindexed",
            details={"doc_id": doc.id, "filename": doc.filename, "version": new_version, "chunks": result["chunk_count"]},
        ))
        await db.commit()
        return {
            "success": True, "doc_id": doc.id, "filename": doc.filename,
            "version": new_version, "new_version": True,
            "chunk_count": result["chunk_count"], "total_chars": result["total_chars"],
        }
    except HTTPException:
        await db.rollback()
        # Rollback: remove any partially-added new-version vectors/keywords
        try:
            engine.vector_store.delete_by_version(doc.id, new_version)
        except Exception:
            pass
        try:
            engine.keyword_index.delete_by_version(doc.id, new_version)
        except Exception:
            pass
        if file_path.exists():
            file_path.unlink()
        raise
    except Exception as e:
        await db.rollback()
        # Rollback: remove any partially-added new-version vectors/keywords
        try:
            engine.vector_store.delete_by_version(doc.id, new_version)
        except Exception:
            pass
        try:
            engine.keyword_index.delete_by_version(doc.id, new_version)
        except Exception:
            pass
        if file_path.exists():
            file_path.unlink()
        logger.error(f"Re-index (new version) failed: {e}")
        raise HTTPException(status_code=500, detail=f"Re-index failed: {e}")


@router.post("/documents/{doc_id}/reindex")
async def reindex_document(doc_id: str, db: AsyncSession = Depends(get_async_db)) -> Dict[str, Any]:
    """Re-index a document if its file on disk changed (hash mismatch → new version)."""
    doc = (await db.execute(select(Document).where(Document.id == doc_id))).scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    file_path = Path(doc.file_path)
    if not file_path.exists():
        raise HTTPException(status_code=410, detail="Underlying file no longer exists on disk")

    current_hash = Chunker.calculate_file_hash(str(file_path))
    if current_hash == doc.file_hash:
        return {"success": True, "unchanged": True, "message": "File chưa thay đổi, không cần re-index."}

    with open(file_path, "rb") as f:
        content = f.read()
    return await _create_new_version(db, doc, content, current_hash, Path(settings.UPLOAD_DIR))


@router.get("/documents")
async def list_documents(db: AsyncSession = Depends(get_async_db)) -> List[Dict[str, Any]]:
    stmt = select(Document).where(Document.is_active == True)
    docs = (await db.execute(stmt)).scalars().all()
    return [
        {
            "id": d.id,
            "filename": d.filename,
            "uploaded_at": d.created_at.isoformat() if d.created_at else None,
            "file_size": d.file_size,
            "current_version": (d.metadata_json or {}).get("current_version", 1),
        }
        for d in docs
    ]


@router.get("/documents/{doc_id}/versions")
async def list_document_versions(doc_id: str, db: AsyncSession = Depends(get_async_db)) -> Dict[str, Any]:
    """Return the version history for a document (audit of indexing, §10.7)."""
    doc = (await db.execute(select(Document).where(Document.id == doc_id))).scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    rows = (await db.execute(
        select(DocumentVersion)
        .where(DocumentVersion.document_id == doc_id)
        .order_by(DocumentVersion.version.desc())
    )).scalars().all()

    current_version = (doc.metadata_json or {}).get("current_version", 1)
    return {
        "doc_id": doc_id,
        "filename": doc.filename,
        "current_version": current_version,
        "versions": [
            {
                "version": v.version,
                "chunk_count": v.chunk_count,
                "created_at": v.created_at.isoformat() if v.created_at else None,
                "is_current": v.version == current_version,
                "file_exists": bool(v.file_path and Path(v.file_path).exists()),
            }
            for v in rows
        ],
    }


@router.delete("/documents/{doc_id}")
async def delete_document(doc_id: str, db: AsyncSession = Depends(get_async_db)) -> Dict[str, Any]:
    """Delete a document and all of its derived data, then verify removal."""
    doc = (await db.execute(select(Document).where(Document.id == doc_id))).scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    try:
        engine = get_rag_engine()
        engine.vector_store.delete_by_doc_id(doc_id)
        engine.keyword_index.delete_by_doc_id(doc_id)

        # Collect every version's file path BEFORE deleting the rows, so we can
        # clean up all on-disk files (not just the current head) and avoid orphans.
        version_rows = (await db.execute(
            select(DocumentVersion.file_path).where(DocumentVersion.document_id == doc_id)
        )).scalars().all()
        file_paths = {p for p in version_rows if p}
        if doc.file_path:
            file_paths.add(doc.file_path)

        await db.execute(delete(Chunk).where(Chunk.document_id == doc_id))
        await db.execute(delete(DocumentMetadata).where(DocumentMetadata.document_id == doc_id))
        await db.execute(delete(DocumentVersion).where(DocumentVersion.document_id == doc_id))

        await db.delete(doc)

        db.add(AuditLog(
            id=str(uuid.uuid4()), actor="user", action="document_deleted",
            details={"doc_id": doc_id, "filename": doc.filename},
        ))
        await db.commit()

        # Remove every version's file from disk. A failed unlink is logged but
        # not fatal — the DB state is already consistent.
        for p in file_paths:
            try:
                fp = Path(p)
                if fp.exists():
                    fp.unlink()
            except OSError as e:
                logger.warning(f"Could not remove file {p}: {e}")

        # Verify embeddings + keyword entries are gone
        vec_remaining = engine.vector_store.count_by_doc_id(doc_id)
        kw_remaining = engine.keyword_index.count_by_doc_id(doc_id)

        return {
            "success": True,
            "verified": vec_remaining == 0 and kw_remaining == 0,
            "embeddings_remaining": vec_remaining,
            "keyword_entries_remaining": kw_remaining,
        }
    except HTTPException:
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        logger.error(f"Delete document failed: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to delete document: {e}")
