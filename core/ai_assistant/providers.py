# -*- coding: utf-8 -*-
"""Провайдеры LLM: Anthropic (primary) + OpenAI-совместимый (fallback).

Оба провайдера принимают одинаковый формат сообщений и whitelist tools,
а возвращают унифицированный AIResponse — разница в форматах скрыта внутри.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)


@dataclass
class ToolUse:
    """LLM попросил выполнить инструмент."""

    id: str
    name: str
    input: dict[str, Any]


@dataclass
class AIResponse:
    """Унифицированный ответ LLM (текст + опциональные tool_use)."""

    text: str = ""
    tool_uses: list[ToolUse] = field(default_factory=list)
    stop_reason: str = ""
    provider: str = ""
    model: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def has_tool_uses(self) -> bool:
        return bool(self.tool_uses)


class ProviderError(Exception):
    """Ошибка при обращении к провайдеру (сеть, 5xx, невалидный ответ)."""


def _decode_response_json(resp: httpx.Response, *, provider: str) -> dict[str, Any]:
    """Разобрать 2xx JSON так, чтобы битый gateway-ответ включал fallback."""
    try:
        data = resp.json()
    except (ValueError, TypeError) as exc:
        raise ProviderError(f"{provider}: невалидный JSON в успешном ответе") from exc
    if not isinstance(data, dict):
        raise ProviderError(f"{provider}: ожидался JSON-object, получен {type(data).__name__}")
    return data


class AnthropicProvider:
    """Клиент Anthropic Messages API.

    Использует совместимый формат tools (Claude tool_use). baseURL может
    быть проксированным, например ``https://api.claudehub.fun/v1``.
    """

    name = "anthropic"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout: float = 20.0,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout

    async def chat(
        self,
        *,
        messages: list[dict[str, Any]],
        system: str = "",
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 1024,
    ) -> AIResponse:
        url = f"{self._base_url}/messages"
        headers = {
            "x-api-key": self._api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        body: dict[str, Any] = {
            "model": self._model,
            "max_tokens": max_tokens,
            "messages": messages,
        }
        if system:
            body["system"] = system
        if tools:
            body["tools"] = tools

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(url, headers=headers, json=body)
        except httpx.HTTPError as exc:
            raise ProviderError(f"anthropic: сетевая ошибка ({type(exc).__name__}): {exc}") from exc

        if resp.status_code >= 500 or resp.status_code in (429,):
            raise ProviderError(f"anthropic: HTTP {resp.status_code}: {resp.text[:200]}")
        if resp.status_code >= 400:
            raise ProviderError(f"anthropic: HTTP {resp.status_code}: {resp.text[:200]}")

        data = _decode_response_json(resp, provider=self.name)
        content = data.get("content")
        if not isinstance(content, list):
            raise ProviderError("anthropic: поле content отсутствует или не является списком")
        text_parts: list[str] = []
        tool_uses: list[ToolUse] = []
        for block in content:
            if not isinstance(block, dict):
                raise ProviderError("anthropic: элемент content имеет неверный формат")
            block_type = block.get("type")
            if block_type == "text":
                block_text = block.get("text", "")
                if not isinstance(block_text, str):
                    raise ProviderError("anthropic: text block содержит не строку")
                text_parts.append(block_text)
            elif block_type == "tool_use":
                tool_input = block.get("input") or {}
                if not isinstance(tool_input, dict):
                    raise ProviderError("anthropic: tool_use.input имеет неверный формат")
                tool_uses.append(
                    ToolUse(
                        id=block.get("id", ""),
                        name=block.get("name", ""),
                        input=dict(tool_input),
                    )
                )

        return AIResponse(
            text="\n".join(t for t in text_parts if t),
            tool_uses=tool_uses,
            stop_reason=data.get("stop_reason", ""),
            provider=self.name,
            model=self._model,
            raw=data,
        )


class OpenAIProvider:
    """Клиент OpenAI-совместимого Chat Completions API.

    Преобразует Claude-формат сообщений (с blocks) в плоские OpenAI-сообщения,
    и наоборот, ответ сериализует обратно в AIResponse.
    """

    name = "openai"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout: float = 20.0,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout

    async def chat(
        self,
        *,
        messages: list[dict[str, Any]],
        system: str = "",
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 1024,
    ) -> AIResponse:
        oai_messages: list[dict[str, Any]] = []
        if system:
            oai_messages.append({"role": "system", "content": system})
        for m in messages:
            oai_messages.extend(_anthropic_to_openai_message(m))

        oai_tools = None
        if tools:
            oai_tools = [
                {
                    "type": "function",
                    "function": {
                        "name": t["name"],
                        "description": t.get("description", ""),
                        "parameters": t.get("input_schema") or {"type": "object"},
                    },
                }
                for t in tools
            ]

        url = f"{self._base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        body: dict[str, Any] = {
            "model": self._model,
            "messages": oai_messages,
            "max_tokens": max_tokens,
        }
        if oai_tools:
            body["tools"] = oai_tools

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(url, headers=headers, json=body)
        except httpx.HTTPError as exc:
            raise ProviderError(f"openai: сетевая ошибка ({type(exc).__name__}): {exc}") from exc

        if resp.status_code >= 400:
            raise ProviderError(f"openai: HTTP {resp.status_code}: {resp.text[:200]}")

        data = _decode_response_json(resp, provider=self.name)
        choices = data.get("choices") or []
        if not isinstance(choices, list) or not choices:
            raise ProviderError("openai: пустой choices в ответе")
        first_choice = choices[0]
        if not isinstance(first_choice, dict):
            raise ProviderError("openai: неверный формат choices[0]")
        msg = first_choice.get("message", {}) or {}
        if not isinstance(msg, dict):
            raise ProviderError("openai: неверный формат message")
        text = msg.get("content") or ""
        if not isinstance(text, str):
            raise ProviderError("openai: message.content имеет неверный формат")
        tool_uses: list[ToolUse] = []
        raw_tool_calls = msg.get("tool_calls") or []
        if not isinstance(raw_tool_calls, list):
            raise ProviderError("openai: message.tool_calls имеет неверный формат")
        for tc in raw_tool_calls:
            if not isinstance(tc, dict):
                raise ProviderError("openai: элемент tool_calls имеет неверный формат")
            fn = tc.get("function") or {}
            if not isinstance(fn, dict):
                raise ProviderError("openai: tool_call.function имеет неверный формат")
            args_raw = fn.get("arguments") or "{}"
            try:
                import json as _json

                args = _json.loads(args_raw) if isinstance(args_raw, str) else dict(args_raw)
            except Exception:
                args = {}
            tool_uses.append(
                ToolUse(
                    id=tc.get("id", ""),
                    name=fn.get("name", ""),
                    input=args,
                )
            )
        return AIResponse(
            text=text,
            tool_uses=tool_uses,
            stop_reason=first_choice.get("finish_reason", ""),
            provider=self.name,
            model=self._model,
            raw=data,
        )


def _anthropic_to_openai_message(m: dict[str, Any]) -> list[dict[str, Any]]:
    """Преобразует одно anthropic-сообщение в одно или несколько openai-сообщений."""
    role = m.get("role", "user")
    content = m.get("content")

    # Простой текстовый случай
    if isinstance(content, str):
        return [{"role": role, "content": content}]

    if not isinstance(content, list):
        return [{"role": role, "content": str(content or "")}]

    # Сложный случай: список блоков (text, tool_use, tool_result)
    text_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    tool_results: list[dict[str, Any]] = []
    import json as _json

    for block in content:
        btype = block.get("type")
        if btype == "text":
            text_parts.append(block.get("text", ""))
        elif btype == "tool_use":
            tool_calls.append(
                {
                    "id": block.get("id", ""),
                    "type": "function",
                    "function": {
                        "name": block.get("name", ""),
                        "arguments": _json.dumps(block.get("input") or {}, ensure_ascii=False),
                    },
                }
            )
        elif btype == "tool_result":
            res_content = block.get("content")
            if isinstance(res_content, list):
                res_text = "\n".join(
                    b.get("text", "") for b in res_content if b.get("type") == "text"
                )
            else:
                res_text = str(res_content or "")
            tool_results.append(
                {
                    "role": "tool",
                    "tool_call_id": block.get("tool_use_id", ""),
                    "content": res_text,
                }
            )

    out: list[dict[str, Any]] = []
    if role == "assistant":
        msg: dict[str, Any] = {"role": "assistant"}
        if text_parts:
            msg["content"] = "\n".join(text_parts)
        else:
            msg["content"] = None
        if tool_calls:
            msg["tool_calls"] = tool_calls
        out.append(msg)
    elif role == "user":
        if tool_results:
            out.extend(tool_results)
            if text_parts:
                out.append({"role": "user", "content": "\n".join(text_parts)})
        else:
            out.append({"role": "user", "content": "\n".join(text_parts)})
    else:
        out.append({"role": role, "content": "\n".join(text_parts)})
    return out
