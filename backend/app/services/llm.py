import os
from typing import List, Dict, Any
from openai import AsyncOpenAI, OpenAI
from pydantic import BaseModel
import json

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
        resolved_key = api_key or os.getenv("OPENAI_API_KEY")
        resolved_url = base_url or os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
        self.client = AsyncOpenAI(api_key=resolved_key, base_url=resolved_url)
        self.sync_client = OpenAI(api_key=resolved_key, base_url=resolved_url)
        self.model = model

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
            response = self.sync_client.chat.completions.create(
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
        """Call LLM with messages and optional context"""
        formatted_messages = []

        # Add system prompt with RAG context if provided
        if context:
            formatted_messages.append({
                "role": "system",
                "content": f"""You are a helpful assistant. Use the following context from documents to answer questions.

Context:
{context}

Rules:
1. Answer based on the provided context.
2. If the context doesn't contain relevant information, say "Không tìm thấy tài liệu phù hợp."
3. Cite sources using format: [filename] or [filename, chunk X]
4. Do not make up information outside the context.
5. Answer in the same language as the question."""
            })

        # Add conversation messages
        for msg in messages:
            formatted_messages.append({
                "role": msg["role"],
                "content": msg["content"]
            })

        try:
            response = await self.client.chat.completions.create(
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
