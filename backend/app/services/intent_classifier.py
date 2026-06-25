"""Intent Classifier — determine user intent to route and confirm actions.

Hybrid strategy:
- Tier 1 (rule): fast keyword/regex matching for clear cases. No API cost.
- Tier 2 (LLM fallback): only when rules are inconclusive, ask the LLM to
  classify into one of the known intents.

Intents:
- chat_rag      : question answering over uploaded documents
- agent_action  : create/modify/delete files or run tools (needs care)
- smalltalk     : greetings, thanks, chit-chat
- unknown       : could not be determined

The classifier returns whether the request needs explicit user confirmation
before the agent acts (agent_action, or low confidence).
"""

import json
import re
from typing import Dict, Any, Optional

from sqlalchemy.orm import Session

from .llm import LLMProvider
from ..core.config import settings
from ..core.logging_config import logger


INTENTS = ("chat_rag", "agent_action", "smalltalk", "unknown")

# Tier-1 rule patterns (Vietnamese + English). Matched case-insensitively.
_ACTION_PATTERNS = [
    r"\b(tạo|ghi|viết|sửa|xóa|xoá|đổi tên|di chuyển|cập nhật|lưu)\b.*\b(file|tập tin|tệp|thư mục)\b",
    r"\b(create|write|edit|modify|delete|remove|rename|move|save|update)\b.*\b(file|folder|directory)\b",
    r"\bfile\.(read|write|list|delete|undo)\b",
    r"\b(chạy|thực thi|run|execute)\b",
    r"\b(undo|hoàn tác|khôi phục|rollback)\b",
]

_SMALLTALK_PATTERNS = [
    r"^\s*(xin chào|chào|hello|hi|hey|chào bạn)\b",
    r"\b(cảm ơn|cám ơn|thank you|thanks|tạm biệt|bye)\b",
    r"^\s*(bạn là ai|bạn khỏe không|how are you|who are you)\b",
]

_QUESTION_PATTERNS = [
    r"\?\s*$",
    r"^\s*(là gì|cái gì|thế nào|tại sao|khi nào|ở đâu|ai|bao nhiêu)\b",
    r"^\s*(what|how|why|when|where|who|which|tóm tắt|summarize|giải thích|explain)\b",
    r"\b(theo tài liệu|trong tài liệu|tài liệu nói|document says)\b",
]


class IntentClassifier:
    """Classify user messages into intents to route and gate actions."""

    def __init__(self, llm_provider: Optional[LLMProvider] = None):
        self._llm = llm_provider
        self.confidence_min = settings.INTENT_CONFIDENCE_MIN
        self.use_llm_fallback = settings.INTENT_USE_LLM_FALLBACK

    @property
    def llm(self) -> LLMProvider:
        # Lazy init so importing this module never requires an API key.
        if self._llm is None:
            self._llm = LLMProvider(
                api_key=settings.OPENAI_API_KEY,
                base_url=settings.OPENAI_BASE_URL,
                model=settings.DEFAULT_MODEL,
            )
        return self._llm

    def classify(self, message: str, session_id: str = "",
                 db: Optional[Session] = None) -> Dict[str, Any]:
        """Return {intent, confidence, needs_confirmation, source, suggested_route}."""
        text = (message or "").strip()
        if not text:
            return self._result("unknown", 0.0, "rule")

        rule = self._classify_rule(text)
        if rule["confidence"] >= self.confidence_min:
            return self._finalize(rule)

        # Inconclusive → optional LLM fallback
        if self.use_llm_fallback:
            try:
                llm_res = self._classify_llm(text)
                if llm_res:
                    return self._finalize(llm_res)
            except Exception as e:
                logger.warning(f"Intent LLM fallback failed: {e}")

        # Fall back to the best rule guess (low confidence)
        return self._finalize(rule)

    # --- Tier 1: rules -----------------------------------------------------

    def _classify_rule(self, text: str) -> Dict[str, Any]:
        low = text.lower()

        for pat in _ACTION_PATTERNS:
            if re.search(pat, low, re.IGNORECASE):
                return self._result("agent_action", 0.9, "rule")

        for pat in _SMALLTALK_PATTERNS:
            if re.search(pat, low, re.IGNORECASE):
                return self._result("smalltalk", 0.85, "rule")

        for pat in _QUESTION_PATTERNS:
            if re.search(pat, low, re.IGNORECASE):
                return self._result("chat_rag", 0.75, "rule")

        # No clear signal
        return self._result("chat_rag", 0.4, "rule")

    # --- Tier 2: LLM -------------------------------------------------------

    def _classify_llm(self, text: str) -> Optional[Dict[str, Any]]:
        prompt = (
            "Classify the user's message into exactly one intent and return JSON only.\n"
            "Intents:\n"
            "- chat_rag: asking a question to be answered from documents/knowledge.\n"
            "- agent_action: asking to create/modify/delete files or run a tool.\n"
            "- smalltalk: greeting, thanks, or chit-chat.\n"
            "- unknown: cannot tell.\n"
            'Return: {"intent": "<one>", "confidence": <0..1>}\n\n'
            f"Message: {text}"
        )
        response = self.llm.chat(
            messages=[{"role": "user", "content": prompt}],
            tools=None,
            temperature=0.0,
        )
        raw = (response.content or "").strip()
        # Strip code fences if present
        raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.MULTILINE).strip()
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            logger.warning(f"Intent LLM returned non-JSON: {raw[:120]}")
            return None
        intent = data.get("intent", "unknown")
        if intent not in INTENTS:
            intent = "unknown"
        try:
            confidence = float(data.get("confidence", 0.5))
        except (TypeError, ValueError):
            confidence = 0.5
        confidence = max(0.0, min(1.0, confidence))
        return self._result(intent, confidence, "llm")

    # --- helpers -----------------------------------------------------------

    def _result(self, intent: str, confidence: float, source: str) -> Dict[str, Any]:
        return {"intent": intent, "confidence": confidence, "source": source}

    def _finalize(self, res: Dict[str, Any]) -> Dict[str, Any]:
        intent = res["intent"]
        confidence = res["confidence"]
        needs_confirmation = intent == "agent_action" or confidence < self.confidence_min
        suggested_route = "agent" if intent == "agent_action" else "chat"
        return {
            "intent": intent,
            "confidence": confidence,
            "source": res.get("source", "rule"),
            "needs_confirmation": needs_confirmation,
            "suggested_route": suggested_route,
        }
