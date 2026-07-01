"""Chat endpoints — RAG-grounded chat with non-streaming and streaming variants,
plus chat session metadata CRUD for the sidebar.

The non-streaming endpoint /api/chat keeps the original synchronous shape.
The streaming endpoint /api/chat/stream emits Server-Sent Events (SSE) so the
UI can render tokens as they arrive.
"""

import json
import uuid
from typing import AsyncIterator, Dict, Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select, delete, desc, func
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.async_db import (
    get_async_db,
    Message as MessageModel,
    ChatSession,
)
from ..services.llm import LLMProvider
from ..services.rag_singleton import get_rag_engine
from ..services.prompts import (
    build_rag_system_prompt,
    RAG_NOT_FOUND,
    RAG_UNGROUNDED,
    RAG_INTERNAL_ERROR,
)
from ..services.grounding import verify_answer
from ..services.settings_manager import SettingsManager
from ..core.config import settings
from ..core.logging_config import logger

router = APIRouter(prefix="/api/chat", tags=["chat"])
llm_provider = LLMProvider(
    api_key=settings.OPENAI_API_KEY,
    base_url=settings.OPENAI_BASE_URL,
    model=settings.DEFAULT_MODEL,
)

MAX_TITLE_LEN = 60


# ---------- Helpers --------------------------------------------------------


async def _load_history(db: AsyncSession, session_id: str, limit: int = 20) -> List[Dict[str, str]]:
    result = await db.execute(
        MessageModel.__table__.select()
        .where(MessageModel.session_id == session_id)
        .order_by(MessageModel.created_at)
        .limit(limit)
    )
    return [{"role": m.role, "content": m.content} for m in result.fetchall()]


async def _ensure_session(db: AsyncSession, session_id: str, first_message: str) -> None:
    """Create the ChatSession row the first time a session_id is seen.

    The title is derived from the first user message (truncated). Subsequent
    messages just bump updated_at via the onupdate trigger.
    """
    existing = (
        await db.execute(select(ChatSession).where(ChatSession.id == session_id))
    ).scalar_one_or_none()
    if existing is None:
        title = (first_message or "Phiên mới").strip().splitlines()[0][:MAX_TITLE_LEN]
        db.add(ChatSession(id=session_id, title=title or "Phiên mới"))
    else:
        # Touch updated_at so the session moves to the top of the recent list.
        existing.title = existing.title or (first_message or "Phiên mới").strip().splitlines()[0][:MAX_TITLE_LEN]


def _parse_doc_ids(request: dict) -> Optional[List[str]]:
    """Extract an optional single-file/multi-file scope from the request.

    Accepts ``doc_ids`` (list) or ``doc_id`` (single string). Returns None for
    whole-corpus search (the default).
    """
    raw = request.get("doc_ids")
    if raw is None:
        single = request.get("doc_id")
        raw = [single] if single else None
    if not raw:
        return None
    return [str(d) for d in raw if d]


def _build_llm_messages(retrieval_context: str, history: List[Dict[str, str]]) -> List[Dict[str, str]]:
    out = [{"role": "system", "content": build_rag_system_prompt(retrieval_context)}]
    for msg in history[-10:]:
        if msg["role"] in ("user", "assistant"):
            out.append(msg)
    return out


# ---------- Non-streaming chat --------------------------------------------


@router.post("")
async def chat(request: dict, db: AsyncSession = Depends(get_async_db)) -> Dict[str, Any]:
    """Synchronous RAG-grounded chat. Returns full answer + citations."""
    message = request.get("message", "").strip()
    session_id = request.get("session_id", str(uuid.uuid4()))
    doc_ids = _parse_doc_ids(request)

    if not message:
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    try:
        history = await _load_history(db, session_id)
        await _ensure_session(db, session_id, message)

        db.add(MessageModel(
            id=str(uuid.uuid4()),
            session_id=session_id,
            role="user",
            content=message,
        ))

        retrieval = None
        try:
            retrieval = await get_rag_engine().retrieve_and_rerank(message, db, doc_ids=doc_ids)
        except Exception as e:
            logger.error(f"RAG retrieval error: {e}")

        if not retrieval:
            response, citations = RAG_NOT_FOUND, []
        else:
            try:
                llm_messages = _build_llm_messages(retrieval["context"], history + [{"role": "user", "content": message}])
                llm_response = await llm_provider.chat_async(
                    messages=llm_messages, context=None, temperature=0.7
                )
                response = llm_response.content
                citations = retrieval["sources"]

                verdict = verify_answer(
                    answer=response,
                    sources=retrieval["sources"],
                    chunks=retrieval.get("chunks", []),
                    min_citations=SettingsManager.get_instance().get_rag_settings().get(
                        "citation_coverage_min", settings.CITATION_COVERAGE_MIN
                    ),
                )
                if not verdict["accepted"]:
                    logger.info(f"Answer downgraded by verifier: {verdict}")
                    response, citations = RAG_UNGROUNDED, []
            except Exception as e:
                logger.error(f"LLM error: {e}")
                response, citations = RAG_INTERNAL_ERROR, []

        db.add(MessageModel(
            id=str(uuid.uuid4()),
            session_id=session_id,
            role="assistant",
            content=response,
            citations=citations,
        ))
        await db.commit()
        return {"response": response, "session_id": session_id, "citations": citations}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Chat endpoint error: {e}")
        await db.rollback()
        raise HTTPException(status_code=500, detail="Internal server error")


# ---------- Streaming chat (SSE) ------------------------------------------


def _sse_event(payload: Dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


@router.post("/stream")
async def chat_stream(request: dict, db: AsyncSession = Depends(get_async_db)) -> StreamingResponse:
    """RAG-grounded chat that streams tokens via SSE.

    Event types emitted:
      - retrieval : initial citations payload (or null when retrieval empty)
      - delta     : a piece of text from the LLM
      - verdict   : grounding/citation check result, sent after the stream
      - done      : final event with session_id
      - error     : on failure (then stream ends)
    """
    message = (request.get("message") or "").strip()
    session_id = request.get("session_id") or str(uuid.uuid4())
    doc_ids = _parse_doc_ids(request)
    if not message:
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    history = await _load_history(db, session_id)
    await _ensure_session(db, session_id, message)
    db.add(MessageModel(
        id=str(uuid.uuid4()), session_id=session_id, role="user", content=message,
    ))
    # Commit the user message + session row up front so we never lose them if
    # the stream is interrupted mid-flight.
    await db.commit()

    async def generator() -> AsyncIterator[str]:
        full_answer_parts: List[str] = []
        citations: List[Dict[str, Any]] = []
        verdict_payload: Optional[Dict[str, Any]] = None
        try:
            retrieval = None
            try:
                retrieval = await get_rag_engine().retrieve_and_rerank(message, db, doc_ids=doc_ids)
            except Exception as e:
                logger.error(f"RAG retrieval error: {e}")

            if not retrieval:
                # No evidence — refuse without calling the LLM.
                yield _sse_event({"type": "retrieval", "sources": []})
                yield _sse_event({"type": "delta", "content": RAG_NOT_FOUND})
                full_answer_parts.append(RAG_NOT_FOUND)
            else:
                citations = retrieval["sources"]
                yield _sse_event({"type": "retrieval", "sources": citations})

                llm_messages = _build_llm_messages(
                    retrieval["context"], history + [{"role": "user", "content": message}]
                )
                try:
                    async for piece in llm_provider.chat_async_stream(
                        messages=llm_messages, context=None, temperature=0.7
                    ):
                        full_answer_parts.append(piece)
                        yield _sse_event({"type": "delta", "content": piece})
                except Exception as e:
                    logger.error(f"LLM stream error: {e}")
                    yield _sse_event({"type": "error", "message": RAG_INTERNAL_ERROR})
                    return

                # Verify grounding AFTER the full answer is in hand
                full_text = "".join(full_answer_parts)
                verdict_payload = verify_answer(
                    answer=full_text,
                    sources=citations,
                    chunks=retrieval.get("chunks", []),
                    min_citations=SettingsManager.get_instance().get_rag_settings().get(
                        "citation_coverage_min", settings.CITATION_COVERAGE_MIN
                    ),
                )
                yield _sse_event({"type": "verdict", **verdict_payload})

                if not verdict_payload.get("accepted"):
                    yield _sse_event({"type": "ungrounded", "message": "Câu trả lời chưa đủ căn cứ từ tài liệu."})

            # Persist assistant message
            final_text = "".join(full_answer_parts)
            ungrounded = bool(verdict_payload and not verdict_payload.get("accepted"))
            stored_citations = [] if ungrounded else citations
            try:
                db.add(MessageModel(
                    id=str(uuid.uuid4()),
                    session_id=session_id,
                    role="assistant",
                    content=final_text,
                    citations=stored_citations,
                ))
                await db.commit()
            except Exception as e:
                logger.error(f"Failed to persist assistant message: {e}")
                await db.rollback()

            yield _sse_event({"type": "done", "session_id": session_id})
        except Exception as e:
            logger.error(f"Stream generator error: {e}")
            yield _sse_event({"type": "error", "message": str(e)})

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


# ---------- Chat history --------------------------------------------------


@router.get("/history/{session_id}")
async def get_chat_history(
    session_id: str,
    db: AsyncSession = Depends(get_async_db),
    limit: int = 50,
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
            "timestamp": msg.created_at.isoformat() if msg.created_at else None,
        }
        for msg in messages
    ]


@router.delete("/history/{session_id}")
async def clear_chat_history(
    session_id: str,
    db: AsyncSession = Depends(get_async_db),
) -> Dict[str, Any]:
    """Clear chat history for a session"""
    stmt = delete(MessageModel).where(MessageModel.session_id == session_id)
    await db.execute(stmt)
    await db.commit()
    return {"success": True, "message": f"Session {session_id} cleared"}


# ---------- Sessions CRUD -------------------------------------------------


@router.get("/sessions")
async def list_sessions(
    limit: int = 20,
    db: AsyncSession = Depends(get_async_db),
) -> List[Dict[str, Any]]:
    """List recent non-archived chat sessions, newest first."""
    # Pull sessions plus a message count via subquery so the UI can render
    # things like "(5 messages)" without N+1 queries.
    msg_count_subq = (
        select(MessageModel.session_id, func.count(MessageModel.id).label("cnt"))
        .group_by(MessageModel.session_id)
        .subquery()
    )
    last_msg_subq = (
        select(MessageModel.session_id, func.max(MessageModel.created_at).label("last_at"))
        .group_by(MessageModel.session_id)
        .subquery()
    )
    stmt = (
        select(
            ChatSession,
            msg_count_subq.c.cnt,
            last_msg_subq.c.last_at,
        )
        .outerjoin(msg_count_subq, msg_count_subq.c.session_id == ChatSession.id)
        .outerjoin(last_msg_subq, last_msg_subq.c.session_id == ChatSession.id)
        .where(ChatSession.archived == False)  # noqa: E712
        .order_by(desc(ChatSession.updated_at))
        .limit(limit)
    )
    rows = (await db.execute(stmt)).all()
    out: List[Dict[str, Any]] = []
    for sess, cnt, last_at in rows:
        out.append({
            "id": sess.id,
            "title": sess.title,
            "created_at": sess.created_at.isoformat() if sess.created_at else None,
            "updated_at": sess.updated_at.isoformat() if sess.updated_at else None,
            "message_count": int(cnt or 0),
            "last_message_at": last_at.isoformat() if last_at else None,
        })
    return out


@router.patch("/sessions/{session_id}")
async def rename_session(
    session_id: str,
    request: dict,
    db: AsyncSession = Depends(get_async_db),
) -> Dict[str, Any]:
    """Rename a session (set title)."""
    title = (request.get("title") or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="title is required")
    sess = (
        await db.execute(select(ChatSession).where(ChatSession.id == session_id))
    ).scalar_one_or_none()
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")
    sess.title = title[:MAX_TITLE_LEN]
    await db.commit()
    return {"success": True, "id": session_id, "title": sess.title}


@router.delete("/sessions/{session_id}")
async def delete_session(
    session_id: str,
    db: AsyncSession = Depends(get_async_db),
) -> Dict[str, Any]:
    """Archive a session (soft delete) and remove its messages."""
    sess = (
        await db.execute(select(ChatSession).where(ChatSession.id == session_id))
    ).scalar_one_or_none()
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")
    sess.archived = True
    await db.execute(delete(MessageModel).where(MessageModel.session_id == session_id))
    await db.commit()
    return {"success": True, "id": session_id}
