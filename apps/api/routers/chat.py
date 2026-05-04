# -*- coding: utf-8 -*-
"""Роутер AI-чата.

POST /api/chat/ask — принимает историю сообщений + флаг allow_tools,
возвращает финальный ответ + список выполненных tool-вызовов.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from core.ai_assistant.chat import (
    ChatMessage,
    ChatRateLimitedError,
    ChatSession,
)
from core.ai_assistant.client import AIUnavailableError

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/chat", tags=["chat"])


class ChatMessageIn(BaseModel):
    role: str = Field(..., description="user | assistant")
    content: str


class ChatAskRequest(BaseModel):
    messages: list[ChatMessageIn] = Field(default_factory=list)
    allow_tools: bool = True


class ToolCallOut(BaseModel):
    name: str
    args: dict
    result: str
    error: str | None = None


class ChatAskResponse(BaseModel):
    answer: str
    tool_calls: list[ToolCallOut] = Field(default_factory=list)


def _client_key(request: Request) -> str:
    """Ключ для rate-limit: TMA user_id или IP."""
    state = getattr(request, "state", None)
    user_id = getattr(state, "tma_user_id", "") if state else ""
    if user_id:
        return f"tma:{user_id}"
    if request.client:
        return f"ip:{request.client.host}"
    return "anon"


@router.post("/ask", response_model=ChatAskResponse)
async def chat_ask(body: ChatAskRequest, request: Request) -> ChatAskResponse:
    """Отправить вопрос AI с опциональными tools."""
    history = [
        ChatMessage(role=m.role, content=m.content)
        for m in body.messages
        if m.role in ("user", "assistant") and (m.content or "").strip()
    ]
    if not history:
        raise HTTPException(status_code=400, detail="messages пустой")

    session = ChatSession(allow_tools=body.allow_tools)
    try:
        result = await session.ask(history, client_key=_client_key(request))
    except ChatRateLimitedError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except AIUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return ChatAskResponse(
        answer=result.answer,
        tool_calls=[
            ToolCallOut(name=t.name, args=t.args, result=t.result, error=t.error)
            for t in result.tool_calls
        ],
    )
