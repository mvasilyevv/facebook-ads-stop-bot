/**
 * API-модуль веб-чата с AI-ассистентом (плавающий виджет дашборда).
 *
 * Эндпоинты:
 *   POST /api/ai/chat   →  { answer, tool_calls, generated_at, model }
 *   GET  /api/ai/pulse  →  { important, text, generated_at }
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
import { apiGet, apiSend } from "./client";

export type AiChatRole = "user" | "assistant";

export interface AiChatMessageIn {
  role: AiChatRole;
  content: string;
}

export interface AiChatToolCall {
  name: string;
  error: string | null;
}

export interface AiChatResponse {
  answer: string;
  tool_calls: AiChatToolCall[];
  generated_at: string;
  model: string;
}

export interface AiPulseResponse {
  important: boolean;
  text: string | null;
  generated_at: string;
}

/** Отправляет историю диалога и возвращает ответ ассистента. */
export function sendAiChatMessage(messages: AiChatMessageIn[]): Promise<AiChatResponse> {
  return apiSend<AiChatResponse>("POST", "/ai/chat", { messages });
}

/** Почасовой пульс кабинета: important=false → тишина, виджет молчит. */
export function fetchAiPulse(): Promise<AiPulseResponse> {
  return apiGet<AiPulseResponse>("/ai/pulse");
}
