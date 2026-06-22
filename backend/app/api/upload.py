from fastapi import APIRouter, UploadFile, File, Depends
from sqlalchemy.orm import Session
import uuid
from pathlib import Path
from typing import Dict, Any

from ...models.database import get_db, Document, Chunk, AuditLog, DocumentMetadata
from ...services.document import DocumentParser, Chunker
from ...services.rag import RAGEngine
from ...core.config import settings

router = APIRouter(prefix="/api", tags=["documents"])
rag_engine = RAGEngine()

@router.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    db: Session = Depends(get_db)
) -> Dict[str, Any]:
    """Upload and process document"""
    doc_id = str(uuid.uuid4())

    # Validate file type
    allowed_extensions = ['.txt', '.pdf', '.md', '.docx', '.xlsx']
    file_ext = Path(file.filename).suffix.lower()
    if file_ext not in allowed_extensions:
        return {
            "success": False,
            "error": f"File type not supported. Allowed: {', '.join(allowed_extensions)}"
        }

    # Save file
    upload_dir = Path(settings.UPLOAD_DIR)
    upload_dir.mkdir(parents=True, exist_ok=True)
    file_path = upload_dir / f"{doc_id}_{file.filename}"

    try:
        content = await file.read()
        with open(file_path, "wb") as f:
            f.write(content)
    except Exception as e:
        return {"success": False, "error": f"Failed to save file: {str(e)}"}

    # Calculate hash
    try:
        file_hash = Chunker.calculate_file_hash(str(file_path))
    except Exception as e:
        return {"success": False, "error": f"Failed to calculate hash: {str(e)}"}

    # Create document record
    doc = Document(
        id=doc_id,
        filename=file.filename,
        file_path=str(file_path),
        mime_type=file.content_type,
        file_hash=file_hash
    )
    db.add(doc)

    # Store metadata
    metadata_fields = {
        "file_size": len(content),
        "file_extension": file_ext,
        "upload_date": doc.created_at.isoformat() if doc.created_at else ""
    }

    for key, value in metadata_fields.items():
        meta = DocumentMetadata(
            id=str(uuid.uuid4()),
            document_id=doc_id,
            key=key,
            value=str(value)
        )
        db.add(meta)

    db.commit()

    # Process with RAG
    try:
        result = await rag_engine.process_document(doc_id, str(file_path), file.filename)

        # Get chunks for DB storage
        chunks_data = Chunker.chunk_text(DocumentParser.parse_file(str(file_path)))

        # Create chunk records
        for chunk_data in chunks_data:
            chunk = Chunk(
                id=f"{doc_id}_{chunk_data['index']}",
                document_id=doc_id,
                chunk_index=chunk_data['index'],
                content=chunk_data['content'],
                metadata_json={
                    "start": chunk_data['start_char'],
                    "end": chunk_data['end_char']
                }
            )
            db.add(chunk)

        db.commit()

        # Audit log
        audit = AuditLog(
            action="document_uploaded",
            details={
                "doc_id": doc_id,
                "filename": file.filename,
                "chunks": result["chunk_count"]
            }
        )
        db.add(audit)
        db.commit()

        return {
            "success": True,
            "doc_id": doc_id,
            "filename": file.filename,
            "chunk_count": result["chunk_count"],
            "total_chars": result["total_chars"]
        }

    except Exception as e:
        db.rollback()
        # Clean up file
        if file_path.exists():
            file_path.unlink()
        return {"success": False, "error": f"RAG processing failed: {str(e)}"}

@router.get("/documents")
async def list_documents(db: Session = Depends(get_db)) -> list:
    """List all active documents"""
    docs = db.query(Document).filter(Document.is_active == True).all()
    return [
        {
            "id": d.id,
            "filename": d.filename,
            "uploaded_at": d.created_at.isoformat() if d.created_at else None,
            "file_size": d.file_size if hasattr(d, 'file_size') else None
        }
        for d in docs
    ]

@router.delete("/documents/{doc_id}")
async def delete_document(doc_id: str, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Delete document and its embeddings"""
    doc = db.query(Document).filter(Document.id == doc_id).first()
    if not doc:
        return {"success": False, "error": "Document not found"}

    try:
        # Delete from vector store
        rag_engine.vector_store.delete_by_doc_id(doc_id)

        # Delete chunks
        db.query(Chunk).filter(Chunk.document_id == doc_id).delete()

        # Delete metadata
        db.query(DocumentMetadata).filter(DocumentMetadata.document_id == doc_id).delete()

        # Soft delete document
        doc.is_active = False
        db.commit()

        # Audit log
        audit = AuditLog(
            action="document_deleted",
            details={"doc_id": doc_id, "filename": doc.filename}
        )
        db.add(audit)
        db.commit()

        # Delete file
        file_path = Path(doc.file_path)
        if file_path.exists():
            file_path.unlink()

        return {"success": True}

    except Exception as e:
        db.rollback()
        return {"success": False, "error": f"Failed to delete document: {str(e)}"}
