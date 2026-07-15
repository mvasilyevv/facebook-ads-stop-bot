/**
 * Стор плавающего AI-ассистента (виджет дашборда). Non-persist — история живёт
 * только в памяти вкладки (перезагрузка страницы сбрасывает её, серверного
 * состояния у /ai/chat тоже нет).
 *
 * Три вида сообщений в ленте (`kind`):
 *   - "user" / "assistant" — обычный диалог, уходят в тело запроса (последние 12).
 *   - "notification" — 🔔 алерт из WS (fb_agent:alert:created), в тело запроса
 *     НЕ попадает (иначе ассистент отвечал бы на собственные пуши как на вопрос).
 *
 * В ленте держим не больше MAX_DISPLAYED_MESSAGES — старые обрезаются.
 */
import { create } from "zustand";
import { sendAiChatMessage, type AiChatMessageIn, type AiChatToolCall } from "@/lib/api/aiChat";
import { ApiError } from "@/lib/api/client";

export type ChatMessageKind = "user" | "assistant" | "notification";

/** Сырой payload канала fb_agent:alert:created (форвардится через /ws/dashboard). */
export interface AlertCreatedNotificationPayload {
  fb_ad_id?: string | null;
  ad_name?: string | null;
  offer_code?: string | null;
  stage: string;
  matched_rule_codes?: string[] | null;
}

export interface ChatWidgetMessage {
  id: string;
  kind: ChatMessageKind;
  content: string;
  /** Только для kind="assistant" — какие read-only инструменты дёргал ассистент. */
  toolCalls?: AiChatToolCall[];
  /** Только для kind="notification" — стадия алерта (влияет на цвет левой рамки). */
  stage?: string;
  createdAt: string;
}

/** Сколько сообщений держим в ленте одновременно (UI, не запрос). */
const MAX_DISPLAYED_MESSAGES = 24;
/** Сколько последних user/assistant сообщений уходит в тело запроса (контракт бэка). */
const MAX_HISTORY_FOR_REQUEST = 12;

function makeId(): string {
  return `chat_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
}

function nowIso(): string {
  return new Date().toISOString();
}

/** Разбирает ApiError/сетевую ошибку в текст сообщения ассистента (в ленту, не тостом). */
function errorToAssistantText(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 429) {
      return "Лимит запросов исчерпан, попробуй позже";
    }
    const detail = typeof err.detail === "string" ? err.detail : err.message;
    return `Ассистент недоступен: ${detail}`;
  }
  const detail = err instanceof Error ? err.message : "нет соединения с сервером";
  return `Ассистент недоступен: ${detail}`;
}

interface ChatWidgetState {
  open: boolean;
  unread: number;
  messages: ChatWidgetMessage[];
  pending: boolean;
  /** Модель последнего успешного ответа — показываем в шапке панели. */
  lastModel: string | null;

  setOpen: (open: boolean) => void;
  toggle: () => void;
  clearUnread: () => void;
  clearMessages: () => void;
  sendMessage: (text: string) => Promise<void>;
  pushNotification: (payload: AlertCreatedNotificationPayload) => void;
}

export const useChatWidget = create<ChatWidgetState>((set, get) => ({
  open: false,
  unread: 0,
  messages: [],
  pending: false,
  lastModel: null,

  setOpen: (open) => set({ open, unread: open ? 0 : get().unread }),

  toggle: () =>
    set((s) => {
      const open = !s.open;
      return { open, unread: open ? 0 : s.unread };
    }),

  clearUnread: () => set({ unread: 0 }),

  clearMessages: () => set({ messages: [] }),

  sendMessage: async (text) => {
    const trimmed = text.trim();
    if (!trimmed || get().pending) return;

    const userMsg: ChatWidgetMessage = {
      id: makeId(),
      kind: "user",
      content: trimmed,
      createdAt: nowIso(),
    };
    set((s) => ({
      messages: [...s.messages, userMsg].slice(-MAX_DISPLAYED_MESSAGES),
      pending: true,
    }));

    // Тело запроса: только user/assistant (нотификации исключены), последние 12.
    const history: AiChatMessageIn[] = get()
      .messages.filter((m) => m.kind === "user" || m.kind === "assistant")
      .slice(-MAX_HISTORY_FOR_REQUEST)
      .map((m) => ({ role: m.kind === "user" ? "user" : "assistant", content: m.content }));

    try {
      const resp = await sendAiChatMessage(history);
      const assistantMsg: ChatWidgetMessage = {
        id: makeId(),
        kind: "assistant",
        content: resp.answer,
        toolCalls: resp.tool_calls,
        createdAt: resp.generated_at,
      };
      set((s) => ({
        messages: [...s.messages, assistantMsg].slice(-MAX_DISPLAYED_MESSAGES),
        pending: false,
        lastModel: resp.model,
      }));
    } catch (err) {
      const assistantMsg: ChatWidgetMessage = {
        id: makeId(),
        kind: "assistant",
        content: errorToAssistantText(err),
        createdAt: nowIso(),
      };
      set((s) => ({
        messages: [...s.messages, assistantMsg].slice(-MAX_DISPLAYED_MESSAGES),
        pending: false,
      }));
    }
  },

  pushNotification: (payload) => {
    const isStop = payload.stage === "stop";
    const label = isStop ? "STOP" : payload.stage === "warning" ? "WARNING" : payload.stage.toUpperCase();
    const name = payload.ad_name ?? payload.fb_ad_id ?? "—";
    const offerPart = payload.offer_code ? ` [${payload.offer_code}]` : "";
    const codes = (payload.matched_rule_codes ?? []).filter(Boolean);
    const codesPart = codes.length > 0 ? ` — ${codes.join(", ")}` : "";

    const notification: ChatWidgetMessage = {
      id: makeId(),
      kind: "notification",
      content: `🔔 ${label}: ${name}${offerPart}${codesPart}`,
      stage: payload.stage,
      createdAt: nowIso(),
    };

    set((s) => ({
      messages: [...s.messages, notification].slice(-MAX_DISPLAYED_MESSAGES),
      unread: s.open ? s.unread : s.unread + 1,
    }));
  },
}));
