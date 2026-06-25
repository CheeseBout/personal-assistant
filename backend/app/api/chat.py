from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional, List, Dict, Any
import uuid

from ..models.async_db import get_async_db, Message as MessageModel
from ..services.llm import LLMProvider
from ..services.rag import RAGEngine
from ..core.config import settings
from ..core.logging_config import logger

router = APIRouter(prefix="/api/chat", tags=["chat"])
llm_provider = LLMProvider(
    api_key=settings.OPENAI_API_KEY,
    base_url=settings.OPENAI_BASE_URL,
    model=settings.DEFAULT_MODEL
)
_rag_engine: Optional[RAGEngine] = None

def get_rag_engine() -> RAGEngine:
    global _rag_engine
    if _rag_engine is None:
        _rag_engine = RAGEngine()
    return _rag_engine

@router.post("")
async def chat(
    request: dict,
    db: AsyncSession = Depends(get_async_db)
) -> Dict[str, Any]:
    """Chat endpoint with RAG and conversation context"""
    message = request.get("message", "").strip()
    session_id = request.get("session_id", str(uuid.uuid4()))

    if not message:
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    try:
        result = await db.execute(
            MessageModel.__table__.select()
            .where(MessageModel.session_id == session_id)
            .order_by(MessageModel.created_at)
            .limit(20)
        )
        history_messages = result.fetchall()

        conversation_context = []
        for msg in history_messages:
            conversation_context.append({
                "role": msg.role,
                "content": msg.content
            })

        user_msg = MessageModel(
            id=str(uuid.uuid4()),
            session_id=session_id,
            role="user",
            content=message
        )
        db.add(user_msg)

        retrieval = None
        try:
            retrieval = await get_rag_engine().retrieve_and_rerank(message, db)
        except Exception as e:
            logger.error(f"RAG retrieval error: {e}")
            retrieval = None

        if not retrieval:
            response = "Khong tim thay tai lieu phu hop de tra loi cau hoi cua ban."
            citations = []
        else:
            try:
                llm_messages = []
                system_prompt = f"""Ban la mot tro ly AI huu ich. Su dung thong tin tu cac tai lieu da cung cap de tra loi cau hoi.

Ngu canh tu tai lieu:
{retrieval['context']}

Huong dan:
1. Tra loi dua tren ngu canh duoc cung cap.
2. Neu ngu canh khong chua thong tin lien quan, hay noi "Khong tim thay thong tin phu hop trong tai lieu."
3. Luon trich dan nguon bang dinh dang: [ten file] hoac [ten file, chunk X]
4. Khong duoc bia thong tin ngoai ngu canh.
5. Tra loi bang cung ngon ngu voi cau hoi.
6. Neu co nhieu nguon, hay tong hop thong tin tu tat ca cac nguon.
7. Neu ngu canh mau thuan, hay de cap den su mau thuan nay."""

                llm_messages.append({"role": "system", "content": system_prompt})
                for msg in conversation_context[-10:]:
                    if msg["role"] in ["user", "assistant"]:
                        llm_messages.append(msg)

                llm_response = await llm_provider.chat_async(
                    messages=llm_messages,
                    context=None,
                    temperature=0.7
                )
                response = llm_response.content
                citations = retrieval["sources"]

                from ..services.grounding import verify_answer
                verdict = verify_answer(
                    answer=response,
                    sources=retrieval["sources"],
                    chunks=retrieval.get("chunks", []),
                    min_citations=settings.CITATION_COVERAGE_MIN,
                )
                if not verdict["accepted"]:
                    logger.info(f"Answer downgraded by verifier: {verdict}")
                    response = "Khong tim thay thong tin phu hop trong tai lieu de tra loi dang tin cay."
                    citations = []
            except Exception as e:
                logger.error(f"LLM error: {e}")
                response = "Xin loi, da xay ra loi khi xu ly cau tra loi. Vui long thu lai."
                citations = []

        assistant_msg = MessageModel(
            id=str(uuid.uuid4()),
            session_id=session_id,
            role="assistant",
            content=response,
            citations=citations
        )
        db.add(assistant_msg)

        await db.commit()

        return {
            "response": response,
            "session_id": session_id,
            "citations": citations
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Chat endpoint error: {e}")
        await db.rollback()
        raise HTTPException(status_code=500, detail="Internal server error")

@router.get("/history/{session_id}")
async def get_chat_history(
    session_id: str,
    db: AsyncSession = Depends(get_async_db),
    limit: int = 50
) -> List[Dict[str, Any]]:
    """Get chat history for session"""
    result = await db.execute(
        MessageModel.__table__.select()
        .where(MessageModel.session_id == session_id)
        .order_by(MessageModel.created_at)
        .limit(limit)
    )
    messages = result.fetchall()

    return [
        {
            "id": msg.id,
            "role": msg.role,
            "content": msg.content,
            "citations": msg.citations or [],
            "timestamp": msg.created_at.isoformat() if msg.created_at else None
        }
        for msg in messages
    ]

@router.delete("/history/{session_id}")
async def clear_chat_history(
    session_id: str,
    db: AsyncSession = Depends(get_async_db)
) -> Dict[str, Any]:
    """Clear chat history for a session"""
    from sqlalchemy import delete
    stmt = delete(MessageModel).where(MessageModel.session_id == session_id)
    await db.execute(stmt)
    await db.commit()
    return {"success": True, "message": f"Session {session_id} cleared"}
