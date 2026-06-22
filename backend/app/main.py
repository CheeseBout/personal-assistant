from fastapi import FastAPI, UploadFile, File, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from pathlib import Path
import uuid
import json

from .models.database import init_db, get_db, Document, Chunk, Message, AuditLog, DocumentMetadata
from .services.document import DocumentParser, Chunker
from .services.embedding import EmbeddingService, VectorStore
from .services.rag import RAGEngine
from .services.llm import LLMProvider
from .core.config import settings
from .core.logging_config import setup_logging

# Initialize logging
setup_logging()

# Initialize services
rag_engine = RAGEngine()
llm_provider = LLMProvider(
    api_key=settings.OPENAI_API_KEY,
    base_url=settings.OPENAI_BASE_URL,
    model=settings.MODEL
)

app = FastAPI(title="Local RAG Agent", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000", "*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
async def startup_event():
    init_db()
    print("Database initialized")

@app.get("/")
async def root():
    return {"message": "Local RAG Agent API", "version": "0.1.0"}

@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Upload and process document"""
    doc_id = str(uuid.uuid4())

    # Validate file extension
    allowed_extensions = {'.txt', '.pdf', '.md', '.docx', '.xlsx'}
    file_ext = Path(file.filename).suffix.lower()
    if file_ext not in allowed_extensions:
        raise HTTPException(status_code=400, detail=f"Unsupported file type. Supported: {', '.join(allowed_extensions)}")

    # Save file
    upload_dir = Path("../data/uploads")
    upload_dir.mkdir(parents=True, exist_ok=True)
    file_path = upload_dir / f"{doc_id}_{file.filename}"

    try:
        content = await file.read()
        with open(file_path, "wb") as f:
            f.write(content)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save file: {str(e)}")

    # Calculate hash
    try:
        file_hash = Chunker.calculate_file_hash(str(file_path))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to calculate file hash: {str(e)}")

    # Create document record
    doc = Document(
        id=doc_id,
        filename=file.filename,
        file_path=str(file_path),
        mime_type=file.content_type,
        file_hash=file_hash
    )
    db.add(doc)

    # Save metadata
    metadata_fields = {
        "file_size": len(content),
        "file_extension": file_ext,
        "original_filename": file.filename
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
    db.refresh(doc)

    # Process with RAG
    try:
        result = await rag_engine.process_document(doc_id, str(file_path), file.filename)

        # Get parsed text for chunking
        parsed_text = Chunker.parse_file(str(file_path))
        chunks_data = Chunker.chunk_text(parsed_text)

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
            details={"doc_id": doc_id, "filename": file.filename, "chunks": result["chunk_count"]}
        )
        db.add(audit)
        db.commit()

        return {"success": True, "doc_id": doc_id, **result}

    except Exception as e:
        db.rollback()
        # Clean up file
        if file_path.exists():
            file_path.unlink()
        raise HTTPException(status_code=500, detail=f"Failed to process document: {str(e)}")

@app.get("/api/documents")
async def list_documents(db: Session = Depends(get_db)):
    """List all documents"""
    docs = db.query(Document).filter(Document.is_active == True).order_by(Document.created_at.desc()).all()
    return [
        {
            "id": d.id,
            "filename": d.filename,
            "uploaded_at": d.created_at.isoformat() if d.created_at else None,
            "mime_type": d.mime_type
        }
        for d in docs
    ]

@app.get("/api/documents/{doc_id}")
async def get_document(doc_id: str, db: Session = Depends(get_db)):
    """Get document details"""
    doc = db.query(Document).filter(Document.id == doc_id, Document.is_active == True).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    # Count chunks
    chunk_count = db.query(Chunk).filter(Chunk.document_id == doc_id).count()

    # Get metadata
    metadata = db.query(DocumentMetadata).filter(DocumentMetadata.document_id == doc_id).all()
    metadata_dict = {m.key: m.value for m in metadata}

    return {
        "id": doc.id,
        "filename": doc.filename,
        "uploaded_at": doc.created_at.isoformat() if doc.created_at else None,
        "chunk_count": chunk_count,
        "metadata": metadata_dict
    }

@app.delete("/api/documents/{doc_id}")
async def delete_document(doc_id: str, db: Session = Depends(get_db)):
    """Delete document and its embeddings"""
    doc = db.query(Document).filter(Document.id == doc_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    # Delete from vector store
    try:
        rag_engine.vector_store.delete_by_doc_id(doc_id)
    except Exception as e:
        print(f"Warning: Failed to delete from vector store: {e}")

    # Delete chunks
    db.query(Chunk).filter(Chunk.document_id == doc_id).delete()

    # Delete metadata
    db.query(DocumentMetadata).filter(DocumentMetadata.document_id == doc_id).delete()

    # Soft delete document
    doc.is_active = False
    db.commit()

    # Delete file
    try:
        file_path = Path(doc.file_path)
        if file_path.exists():
            file_path.unlink()
    except Exception as e:
        print(f"Warning: Failed to delete file: {e}")

    # Audit log
    audit = AuditLog(
        action="document_deleted",
        details={"doc_id": doc_id, "filename": doc.filename}
    )
    db.add(audit)
    db.commit()

    return {"success": True, "message": "Document deleted"}

@app.post("/api/chat")
async def chat(request: dict, db: Session = Depends(get_db)):
    """Chat endpoint with RAG"""
    message = request.get("message", "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    session_id = request.get("session_id", str(uuid.uuid4()))

    try:
        # Save user message
        user_msg = Message(
            id=str(uuid.uuid4()),
            session_id=session_id,
            role="user",
            content=message
        )
        db.add(user_msg)
        db.commit()

        # Retrieve context
        retrieval = await rag_engine.retrieve_and_rerank(message)

        if not retrieval:
            response = "Không tìm thấy tài liệu phù hợp."
            citations = []
        else:
            # Call LLM with context
            llm_response = await llm_provider.chat(
                messages=[{"role": "user", "content": message}],
                context=retrieval["context"]
            )
            response = llm_response.content
            citations = retrieval["sources"]

        # Save assistant message
        assistant_msg = Message(
            id=str(uuid.uuid4()),
            session_id=session_id,
            role="assistant",
            content=response,
            citations=citations
        )
        db.add(assistant_msg)
        db.commit()

        return {
            "response": response,
            "session_id": session_id,
            "citations": citations
        }

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Chat error: {str(e)}")

@app.get("/api/chat/history/{session_id}")
async def get_chat_history(session_id: str, db: Session = Depends(get_db)):
    """Get chat history for session"""
    messages = db.query(Message).filter(
        Message.session_id == session_id
    ).order_by(Message.created_at).all()

    return [
        {
            "role": m.role,
            "content": m.content,
            "citations": m.citations or [],
            "timestamp": m.created_at.isoformat() if m.created_at else None
        }
        for m in messages
    ]

@app.get("/api/debug/retrieve")
async def debug_retrieve(q: str = "", db: Session = Depends(get_db)):
    """Debug endpoint: xem chunks được retrieve"""
    if not q:
        raise HTTPException(status_code=400, detail="Query parameter 'q' is required")

    try:
        results = await rag_engine.vector_store.search(q, n_results=10)

        formatted = []
        for r in results:
            doc = db.query(Document).filter(Document.id == r['metadata']['doc_id']).first()
            formatted.append({
                "filename": doc.filename if doc else r['metadata']['doc_id'],
                "chunk_index": r['metadata']['chunk_index'],
                "content_preview": r['content'][:200] + "...",
                "metadata": r['metadata']
            })

        return {"query": q, "results": formatted}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Search error: {str(e)}")

@app.get("/api/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "timestamp": uuid.uuid4()}
