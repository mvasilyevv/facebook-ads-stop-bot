/**
 * API-модуль веб-чата с AI-ассистентом (плавающий виджет дашборда).
 *
 * Эндпоинт:
 *   POST /api/ai/chat  →  { answer, tool_calls, generated_at, model }
 *
 * Историю диалога держит КЛИЕНТ (стор chatWidget) — сервер её не хранит.
 * В запрос уходят последние ≤12 сообщений (role: user|assistant, content ≤4000
 * символов), последним — вопрос пользователя.
 *
 * Ошибки: 429 (лимит 30/час), 503 (AI-провайдеры не настроены) — приходят как
 * ApiError через apiSend, разбираются в сторе.
 */
import { apiSend } from "./client";

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

/** Отправляет историю диалога и возвращает ответ ассистента. */
export function sendAiChatMessage(messages: AiChatMessageIn[]): Promise<AiChatResponse> {
  return apiSend<AiChatResponse>("POST", "/ai/chat", { messages });
}
