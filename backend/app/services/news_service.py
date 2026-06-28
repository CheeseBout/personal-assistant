"""News service (Phase 8) — search multiple sources, summarize, keep links.

Pipeline (REQUIREMENTS §13.2):
    query -> web search -> dedupe -> summarize (LLM, fact vs opinion, cite links)
    -> store NewsReport -> return.

Summaries are grounded ONLY in the fetched snippets; the LLM is instructed not
to invent facts and to always attach the original source links. If search
returns nothing, no report is generated and the caller is told so.
"""

import uuid
from typing import Any, Dict, List, Optional
from datetime import datetime

from sqlalchemy.orm import Session

from ..models.database import get_sync_db, NewsReport
from ..core.config import settings
from ..core.logging_config import logger
from .web_search import web_search
from .llm import LLMProvider


_SUMMARY_SYSTEM = """Ban la tro ly tom tat tin tuc. Chi dung thong tin trong cac nguon duoc cung cap.

Nguyen tac:
1. Chi tom tat dua tren noi dung nguon ben duoi. KHONG bia, KHONG suy doan ngoai du lieu.
2. Phan biet ro FACT (su kien) va NHAN DINH (y kien).
3. Neu cac nguon mau thuan, neu ro su khac biet.
4. Moi y chinh phai kem so thu tu nguon [1], [2]... tuong ung danh sach nguon.
5. Tra loi bang ngon ngu cua truy van. Ngan gon, co cau truc.
6. Neu nguon khong du de ket luan, noi ro dieu do."""


def _dedupe(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Drop duplicate URLs and empty entries, preserving order."""
    seen = set()
    out = []
    for r in results:
        url = (r.get("url") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        out.append(r)
    return out


def _build_sources_block(sources: List[Dict[str, Any]]) -> str:
    lines = []
    for i, s in enumerate(sources, 1):
        published = f" ({s['published']})" if s.get("published") else ""
        lines.append(f"[{i}] {s.get('title', '')}{published}\n{s.get('url', '')}\n{s.get('snippet', '')}")
    return "\n\n".join(lines)


def generate_report(query: str, max_sources: Optional[int] = None,
                    task_id: Optional[str] = None,
                    llm: Optional[LLMProvider] = None,
                    db: Optional[Session] = None) -> Dict[str, Any]:
    """Search, summarize, and persist a news report.

    Returns {"status", "report": {...}} or {"status": "error"/"no_results", ...}.
    """
    query = (query or "").strip()
    if not query:
        return {"status": "error", "error": "Truy van rong"}

    max_sources = max_sources or settings.NEWS_DEFAULT_MAX_SOURCES

    search = web_search(query, max_results=max(max_sources * 2, max_sources))
    if search.get("error"):
        return {"status": "error", "error": f"Tim kiem that bai: {search['error']}"}

    sources = _dedupe(search.get("results", []))[:max_sources]
    if not sources:
        return {"status": "no_results", "error": "Khong tim thay nguon phu hop.", "query": query}

    llm = llm or LLMProvider(
        api_key=settings.OPENAI_API_KEY,
        base_url=settings.OPENAI_BASE_URL,
        model=settings.MODEL,
    )

    sources_block = _build_sources_block(sources)
    messages = [
        {"role": "system", "content": _SUMMARY_SYSTEM},
        {"role": "user", "content": (
            f"Truy van: {query}\n\nCac nguon:\n{sources_block}\n\n"
            f"Hay tom tat tin tuc lien quan den truy van, kem trich dan nguon [so]."
        )},
    ]

    try:
        resp = llm.chat(messages=messages, temperature=0.3)
        summary = resp.content or ""
    except Exception as e:
        logger.error(f"News summarization failed: {e}")
        return {"status": "error", "error": f"Tom tat that bai: {e}", "query": query}

    close_db = False
    if db is None:
        db = next(get_sync_db())
        close_db = True
    try:
        report = NewsReport(
            id=str(uuid.uuid4()),
            task_id=task_id,
            query=query,
            summary=summary,
            sources_json=sources,
            created_at=datetime.utcnow(),
        )
        db.add(report)
        db.commit()
        return {
            "status": "success",
            "report": {
                "id": report.id,
                "query": query,
                "summary": summary,
                "sources": sources,
                "created_at": report.created_at.isoformat(),
            },
        }
    finally:
        if close_db:
            db.close()


def list_reports(limit: int = 30, task_id: Optional[str] = None,
                 db: Optional[Session] = None) -> List[Dict[str, Any]]:
    """List recent news reports (newest first)."""
    from sqlalchemy import select

    close_db = False
    if db is None:
        db = next(get_sync_db())
        close_db = True
    try:
        stmt = select(NewsReport)
        if task_id:
            stmt = stmt.where(NewsReport.task_id == task_id)
        stmt = stmt.order_by(NewsReport.created_at.desc()).limit(limit)
        rows = db.execute(stmt).scalars().all()
        return [
            {
                "id": r.id,
                "task_id": r.task_id,
                "query": r.query,
                "summary": r.summary,
                "sources": r.sources_json or [],
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]
    finally:
        if close_db:
            db.close()
