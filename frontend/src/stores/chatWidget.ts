/**
 * Стор плавающего AI-ассистента (виджет дашборда). Non-persist — история живёт
 * только в памяти вкладки (перезагрузка страницы сбрасывает её, серверного
 * состояния у /ai/chat тоже нет).
 *
 * Три вида сообщений в ленте (`kind`):
 *   - "user" / "assistant" — обычный диалог, уходят в тело запроса (последние 12).
 *   - "notification" — 📟 почасовой пульс кабинета (POST /ai/pulse), в тело запроса
 *     НЕ попадает (иначе ассистент отвечал бы на собственные пуши как на вопрос).
 *
 * Пульс: fetchPulse() дёргает /ai/pulse; сервер кэширует ответ на календарный час
 * (UTC), поэтому опрос дешёвый. Сообщение попадает в ленту ТОЛЬКО при
 * important=true и только один раз за час (дедуп через lastPulseHour) — если
 * тихо, ничего не появляется.
 *
 * В ленте держим не больше MAX_DISPLAYED_MESSAGES — старые обрезаются.
 */
import { create } from "zustand";
import {
  fetchAiPulse,
  sendAiChatMessage,
  type AiChatMessageIn,
  type AiChatToolCall,
} from "@/lib/api/aiChat";
import { ApiError } from "@/lib/api/client";

export type ChatMessageKind = "user" | "assistant" | "notification";

export interface ChatWidgetMessage {
  id: string;
  kind: ChatMessageKind;
  content: string;
  /** Только для kind="assistant" — какие read-only инструменты дёргал ассистент. */
  toolCalls?: AiChatToolCall[];
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

/** Ключ календарного часа UTC ("YYYY-MM-DDTHH") — гранулярность кэша пульса на бэке. */
function hourKey(iso: string): string {
  return iso.slice(0, 13);
}

/** Разбирает ApiError/сетевую ошибку в текст сообщения ассистента (в ленту, не тостом). */
function errorToAssistantText(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 429) {
      return "Лимит запросов исчерпан, попробуй позже";
    }
    return `Ассистент недоступен: ${err.message}`;
  }
  const message = err instanceof Error ? err.message : "нет соединения с сервером";
  return `Ассистент недоступен: ${message}`;
}

interface ChatWidgetState {
  open: boolean;
  unread: number;
  messages: ChatWidgetMessage[];
  pending: boolean;
  /** Модель последнего успешного ответа — показываем в шапке панели. */
  lastModel: string | null;
  /** Час (UTC, "YYYY-MM-DDTHH") последнего показанного пульса — дедуп в ленте. */
  lastPulseHour: string | null;

  setOpen: (open: boolean) => void;
  toggle: () => void;
  clearUnread: () => void;
  clearMessages: () => void;
  sendMessage: (text: string) => Promise<void>;
  pushPulse: (text: string, generatedAt: string) => void;
  fetchPulse: () => Promise<void>;
}

export const useChatWidget = create<ChatWidgetState>((set, get) => ({
  open: false,
  unread: 0,
  messages: [],
  pending: false,
  lastModel: null,
  lastPulseHour: null,

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

    // Тело запроса: только user/assistant (нотификации-пульсы исключены), последние 12.
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

  pushPulse: (text, generatedAt) => {
    const pulse: ChatWidgetMessage = {
      id: makeId(),
      kind: "notification",
      content: text,
      createdAt: generatedAt,
    };
    set((s) => ({
      messages: [...s.messages, pulse].slice(-MAX_DISPLAYED_MESSAGES),
      unread: s.open ? s.unread : s.unread + 1,
    }));
  },

  fetchPulse: async () => {
    try {
      const resp = await fetchAiPulse();
      if (!resp.important || !resp.text) return; // тихо — виджет молчит
      const hour = hourKey(resp.generated_at);
      if (hour === get().lastPulseHour) return; // этот час уже показан — не дублируем
      get().pushPulse(resp.text, resp.generated_at);
      set({ lastPulseHour: hour });
    } catch (err) {
      // Пульс — фоновая некритичная фича: любая ошибка (сеть/503) — молчим.
      console.warn("Пульс ассистента недоступен:", err);
    }
  },
}));
