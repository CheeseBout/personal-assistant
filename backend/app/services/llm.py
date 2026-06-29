import os
import json
from typing import List, Dict, Any, Optional

from pydantic import BaseModel

class Message(BaseModel):
    role: str
    content: str

class ToolCall(BaseModel):
    name: str
    arguments: Dict[str, Any]

class LLMResponse(BaseModel):
    content: str
    tool_calls: List[ToolCall] = []

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
        """Synchronous LLM call with tools (used by the agent loop)."""
        formatted_messages = [
            {"role": m["role"], "content": m["content"]} for m in messages
        ]
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
                    tool_calls.append(ToolCall(name=tc.function.name, arguments=arguments))
            return LLMResponse(content=message.content or "", tool_calls=tool_calls)
        except Exception as e:
            print(f"LLM sync API error: {e}")
            raise

    async def chat_async(
        self,
        messages: List[Dict],
        context: str = None,
        tools: List[Dict] = None,
        temperature: float = 0.7
    ) -> LLMResponse:
        formatted_messages = []

        if context:
            formatted_messages.append({
                "role": "system",
                "content": f"""You are a helpful assistant. Use the following context from documents to answer questions.

Context:
{context}

Rules:
1. Answer based on the provided context.
2. If the context doesn't contain relevant information, say "KhÃ´ng tÃ¬m tháº¥y tÃ i liá»‡u phÃ¹ há»£p."
3. Cite sources using format: [filename] or [filename, chunk X]
4. Do not make up information outside the context.
5. Answer in the same language as the question."""
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
