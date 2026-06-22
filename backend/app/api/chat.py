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
        # Get conversation history for context (last 10 messages)
        result = await db.execute(
            MessageModel.__table__.select()
            .where(MessageModel.session_id == session_id)
            .order_by(MessageModel.created_at)
            .limit(20)  # Get up to 20 for context window
        )
        history_messages = result.fetchall()

        # Build conversation context for LLM
        conversation_context = []
        for msg in history_messages:
            conversation_context.append({
                "role": msg.role,
                "content": msg.content
            })

        # Save user message
        user_msg = MessageModel(
            id=str(uuid.uuid4()),
            session_id=session_id,
            role="user",
            content=message
        )
        db.add(user_msg)

        # Get RAG retrieval
        retrieval = None
        try:
            retrieval = await get_rag_engine().retrieve_and_rerank(message, db)
        except Exception as e:
            logger.error(f"RAG retrieval error: {e}")
            retrieval = None

        if not retrieval:
            response = "Không tìm thấy tài liệu phù hợp để trả lời câu hỏi của bạn."
            citations = []
        else:
            try:
                # Build messages with RAG context and conversation history
                llm_messages = []

                # Add system prompt with RAG context
                system_prompt = f"""Bạn là một trợ lý AI hữu ích. Sử dụng thông tin từ các tài liệu đã cung cấp để trả lời câu hỏi.

Ngữ cảnh từ tài liệu:
{retrieval['context']}

Hướng dẫn:
1. Trả lời dựa trên ngữ cảnh được cung cấp.
2. Nếu ngữ cảnh không chứa thông tin liên quan, hãy nói "Không tìm thấy thông tin phù hợp trong tài liệu."
3. Luôn trích dẫn nguồn bằng định dạng: [tên file] hoặc [tên file, chunk X]
4. Không được bịa thông tin ngoài ngữ cảnh.
5. Trả lời bằng cùng ngôn ngữ với câu hỏi.
6. Nếu có nhiều nguồn, hãy tổng hợp thông tin từ tất cả các nguồn.
7. Nếu ngữ cảnh mâu thuẫn, hãy đề cập đến sự mâu thuẫn này."""

                llm_messages.append({"role": "system", "content": system_prompt})

                # Add conversation history (exclude current user message as it's separate)
                for msg in conversation_context[-10:]:  # Last 10 messages for context
                    if msg["role"] in ["user", "assistant"]:
                        llm_messages.append(msg)

                llm_response = await llm_provider.chat(
                    messages=llm_messages,
                    context=None,  # Already in system prompt
                    temperature=0.7
                )
                response = llm_response.content
                citations = retrieval["sources"]

                # Citation coverage + grounding verification (section 10.5)
                from ..services.grounding import verify_answer
                verdict = verify_answer(
                    answer=response,
                    sources=retrieval["sources"],
                    chunks=retrieval.get("chunks", []),
                    min_citations=settings.CITATION_COVERAGE_MIN,
                )
                if not verdict["accepted"]:
                    logger.info(f"Answer downgraded by verifier: {verdict}")
                    response = "Không tìm thấy thông tin phù hợp trong tài liệu để trả lời đáng tin cậy."
                    citations = []
            except Exception as e:
                logger.error(f"LLM error: {e}")
                response = "Xin lỗi, đã xảy ra lỗi khi xử lý câu trả lời. Vui lòng thử lại."
                citations = []

        # Save assistant message
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
