/**
 * API-модуль веб-чата с AI-ассистентом (плавающий виджет дашборда).
 *
 * Эндпоинты:
 *   POST /api/ai/chat   →  { answer, tool_calls, generated_at, model }
 *   POST /api/ai/pulse  →  { important, text, generated_at }
 *
 * Историю диалога держит КЛИЕНТ (стор chatWidget) — сервер её не хранит.
 * В запрос уходят последние ≤12 сообщений (role: user|assistant, content ≤4000
 * символов), последним — вопрос пользователя.
 *
 * Пульс: сервер кэширует результат на календарный час (UTC) — виджет может
 * опрашивать смело, AI дёргается максимум раз в час глобально. important=false
 * (text=null) — за час ничего значимого, виджет молчит.
 *
 * Ошибки: 429 (лимит 30/час), 503 (AI-провайдеры не настроены) — приходят как
 * ApiError через apiSend, разбираются в сторе.
 */
import type { components } from "@fb/shared/api/generated";
import { ApiError } from "./client";
import { generatedFetchApi } from "./generatedClient";

export type AiChatRole = components["schemas"]["ChatMessageIn"]["role"];
export type AiChatMessageIn = components["schemas"]["ChatMessageIn"];
export type AiChatResponse = components["schemas"]["AIChatResponse"];
export type AiPulseResponse = components["schemas"]["AIPulseResponse"];
export type AiChatToolCall = NonNullable<AiChatResponse["tool_calls"]>[number];

function generatedApiError(status: number, error: unknown): ApiError {
  const message =
    typeof error === "object" &&
    error !== null &&
    "message" in error &&
    typeof error.message === "string"
      ? error.message
      : `Ошибка API ${status}`;
  return new ApiError(message, status, error);
}

/** Отправляет историю диалога и возвращает ответ ассистента. */
export async function sendAiChatMessage(messages: AiChatMessageIn[]): Promise<AiChatResponse> {
  const { data, error, response } = await generatedFetchApi.POST("/api/ai/chat", { body: { messages } });
  if (!response.ok) throw generatedApiError(response.status, error);
  if (!data) throw new Error(`Пустой ответ API: ${response.status}`);
  return data;
}

/** Почасовой пульс кабинета: important=false → тишина, виджет молчит. */
export async function fetchAiPulse(): Promise<AiPulseResponse> {
  const { data, error, response } = await generatedFetchApi.POST("/api/ai/pulse");
  if (!response.ok) throw generatedApiError(response.status, error);
  if (!data) throw new Error(`Пустой ответ API: ${response.status}`);
  return data;
}
