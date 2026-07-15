/**
 * ChatMessageList — прокручиваемая лента сообщений AssistantWidget.
 * Автоскролл вниз при новом сообщении и при появлении/скрытии индикатора «думает».
 */
import { useEffect, useRef } from "react";
import { useChatWidget } from "@/stores/chatWidget";
import { ChatMessageItem } from "./ChatMessageItem";

export function ChatMessageList() {
  const messages = useChatWidget((s) => s.messages);
  const pending = useChatWidget((s) => s.pending);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages.length, pending]);

  return (
    <div
      ref={scrollRef}
      className="flex-1 overflow-y-auto px-3 py-3 flex flex-col gap-2.5"
      aria-live="polite"
    >
      {messages.length === 0 ? (
        <p className="text-[12px] text-bg-9 text-center mt-6 px-2">
          Спроси про алерты, объявления или статус воркеров — отвечу, проверив данные.
        </p>
      ) : (
        messages.map((m) => <ChatMessageItem key={m.id} message={m} />)
      )}
      {pending ? (
        <div
          className="flex items-center gap-1 self-start px-3 py-2 border border-[var(--hairline)] bg-bg-2 rounded-[var(--radius-2)]"
          aria-label="Ассистент думает"
        >
          <span className="size-1.5 rounded-full bg-bg-8 animate-pulse" />
          <span className="size-1.5 rounded-full bg-bg-8 animate-pulse [animation-delay:150ms]" />
          <span className="size-1.5 rounded-full bg-bg-8 animate-pulse [animation-delay:300ms]" />
          <span className="text-[11px] text-bg-9 ml-1 font-display">думает…</span>
        </div>
      ) : null}
    </div>
  );
}
