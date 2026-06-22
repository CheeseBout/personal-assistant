from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
import uuid

from ...models.database import get_db, Message
from ...services.llm import LLMProvider
from ...services.rag import RAGEngine
from ...core.config import settings

router = APIRouter(prefix="/api/chat", tags=["chat"])
llm_provider = LLMProvider(
    api_key=settings.OPENAI_API_KEY,
    base_url=settings.OPENAI_BASE_URL,
    model=settings.MODEL
)
rag_engine = RAGEngine()

@router.post("")
async def chat(request: dict, db: Session = Depends(get_db)):
    """Chat endpoint with RAG"""
    message = request.get("message", "").strip()
    session_id = request.get("session_id", str(uuid.uuid4()))

    if not message:
        raise HTTPException(status_code=400, detail="Message cannot be empty")

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
    try:
        retrieval = await rag_engine.retrieve_and_rerank(message)
    except Exception as e:
        print(f"RAG error: {e}")
        retrieval = None

    if not retrieval:
        response = "Không tìm thấy tài liệu phù hợp."
        citations = []
    else:
        try:
            llm_response = await llm_provider.chat(
                messages=[{"role": "user", "content": message}],
                context=retrieval["context"]
            )
            response = llm_response.content
            citations = retrieval["sources"]
        except Exception as e:
            print(f"LLM error: {e}")
            response = "Xin lỗi, đã xảy ra lỗi khi xử lý câu trả lời."
            citations = []

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

@router.get("/history/{session_id}")
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

@router.delete("/history/{session_id}")
async def clear_chat_history(session_id: str, db: Session = Depends(get_db)):
    """Clear chat history for a session"""
    db.query(Message).filter(Message.session_id == session_id).delete()
    db.commit()
    return {"success": True, "message": f"Session {session_id} cleared"}
