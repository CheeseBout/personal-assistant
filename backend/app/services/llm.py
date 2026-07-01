import os
import json
import time
import logging
from typing import List, Dict, Any, Optional, AsyncIterator

from pydantic import BaseModel

_logger = logging.getLogger(__name__)

_MAX_RETRIES = 3

class Message(BaseModel):
    role: str
    content: str

class ToolCall(BaseModel):
    id: Optional[str] = None
    name: str
    arguments: Dict[str, Any]

class LLMResponse(BaseModel):
    content: str
    tool_calls: List[ToolCall] = []
    usage: Optional[Dict[str, int]] = None

class LLMProvider:
    def __init__(self, api_key: str = None, base_url: str = None, model: str = "gpt-4o"):
        self.api_key = api_key or os.getenv("OPENAI_API_KEY") or ""
        self.base_url = base_url or os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
        self.model = model
        self._async_client = None
        self._sync_client = None

    def _require_key(self) -> str:
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY is required to call the LLM")
        return self.api_key

    def _get_sync_client(self):
        if self._sync_client is None:
            from openai import OpenAI
            self._sync_client = OpenAI(api_key=self._require_key(), base_url=self.base_url)
        return self._sync_client

    def _get_async_client(self):
        if self._async_client is None:
            from openai import AsyncOpenAI
            self._async_client = AsyncOpenAI(api_key=self._require_key(), base_url=self.base_url)
        return self._async_client

    def chat(
        self,
        messages: List[Dict],
        tools: List[Dict] = None,
        temperature: float = 0.7
    ) -> LLMResponse:
        """Synchronous LLM call with tools, with retry for transient errors."""
        formatted_messages = []
        for m in messages:
            fm = {"role": m["role"], "content": m.get("content") or ""}
            if m.get("tool_calls"):
                fm["tool_calls"] = m["tool_calls"]
            if m.get("tool_call_id"):
                fm["tool_call_id"] = m["tool_call_id"]
            formatted_messages.append(fm)

        from openai import RateLimitError, APITimeoutError, APIConnectionError
        transient_errors = (RateLimitError, APITimeoutError, APIConnectionError)

        for attempt in range(_MAX_RETRIES):
            try:
                response = self._get_sync_client().chat.completions.create(
                    model=self.model,
                    messages=formatted_messages,
                    tools=tools or None,
                    tool_choice="auto" if tools else None,
                    temperature=temperature,
                )
                message = response.choices[0].message
                tool_calls = []
                if message.tool_calls:
                    for tc in message.tool_calls:
                        try:
                            arguments = json.loads(tc.function.arguments)
                        except json.JSONDecodeError:
                            arguments = {}
                        tool_calls.append(ToolCall(id=tc.id, name=tc.function.name, arguments=arguments))
                usage = None
                if response.usage:
                    usage = {
                        "prompt_tokens": response.usage.prompt_tokens,
                        "completion_tokens": response.usage.completion_tokens,
                    }
                return LLMResponse(content=message.content or "", tool_calls=tool_calls, usage=usage)
            except transient_errors as e:
                if attempt == _MAX_RETRIES - 1:
                    raise
                wait = 2 ** attempt
                _logger.warning(f"LLM transient error (attempt {attempt + 1}/{_MAX_RETRIES}): {e}, retrying in {wait}s")
                time.sleep(wait)
            except Exception as e:
                _logger.error(f"LLM sync API error: {e}")
                raise

    async def chat_async(
        self,
        messages: List[Dict],
        context: str = None,
        tools: List[Dict] = None,
        temperature: float = 0.7
    ) -> LLMResponse:
        """Asynchronous LLM call.

        If ``context`` is provided, it is wrapped in the canonical RAG system
        prompt (see services.prompts.build_rag_system_prompt) and prepended.
        Callers that already build their own system message should pass
        ``context=None`` to avoid duplicating system instructions.
        """
        formatted_messages = []

        if context:
            from .prompts import build_rag_system_prompt
            formatted_messages.append({
                "role": "system",
                "content": build_rag_system_prompt(context),
            })

        for msg in messages:
            formatted_messages.append({
                "role": msg["role"],
                "content": msg["content"]
            })

        try:
            response = await self._get_async_client().chat.completions.create(
                model=self.model,
                messages=formatted_messages,
                tools=tools,
                tool_choice="auto" if tools else None,
                temperature=temperature
            )

            message = response.choices[0].message
            tool_calls = []

            if message.tool_calls:
                for tc in message.tool_calls:
                    try:
                        arguments = json.loads(tc.function.arguments)
                    except json.JSONDecodeError:
                        arguments = {}
                    tool_calls.append(ToolCall(
                        id=tc.id,
                        name=tc.function.name,
                        arguments=arguments
                    ))

            return LLMResponse(
                content=message.content or "",
                tool_calls=tool_calls
            )
        except Exception as e:
            print(f"LLM API error: {e}")
            raise

    async def chat_async_stream(
        self,
        messages: List[Dict],
        context: str = None,
        temperature: float = 0.7,
    ) -> AsyncIterator[str]:
        """Stream text chunks from the LLM (no tool-calling).

        Used by /api/chat/stream for token-by-token chat responses. Tools are
        intentionally not supported here — streaming chat is for RAG-only
        answers; tool-calling goes through the non-streaming /api/agent path.
        """
        formatted_messages = []

        if context:
            from .prompts import build_rag_system_prompt
            formatted_messages.append({
                "role": "system",
                "content": build_rag_system_prompt(context),
            })

        for msg in messages:
            formatted_messages.append({
                "role": msg["role"],
                "content": msg["content"],
            })

        stream = await self._get_async_client().chat.completions.create(
            model=self.model,
            messages=formatted_messages,
            temperature=temperature,
            stream=True,
        )
        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            piece = getattr(delta, "content", None)
            if piece:
                yield piece

    def vision(self, image_b64: str, prompt: str, temperature: float = 0.2) -> str:
        """Summarize/describe an image (e.g. a screenshot) via a vision-capable model.

        Synchronous to match the desktop perception service. ``image_b64`` is a
        base64-encoded PNG. Returns the model's text. Raises on API error so the
        caller can fall back to OCR-only summaries.
        """
        content = [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
        ]
        response = self._get_sync_client().chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": content}],
            temperature=temperature,
        )
        return response.choices[0].message.content or ""
