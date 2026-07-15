/**
 * AssistantWidget — плавающий AI-ассистент дашборда.
 *
 * Кнопка (fixed right-6 bottom-6) открывает панель чата (fixed right-6 bottom-20,
 * ~380px × min(560px, 70vh)). История — в сторе useChatWidget (в памяти вкладки,
 * без persist). WS-нотификации (fb_agent:alert:created) прилетают через
 * useRealtimeInvalidation → useChatWidget.pushNotification и растят unread-бейдж,
 * пока панель закрыта.
 *
 * Esc закрывает панель — обычный div + window keydown listener (без Radix Dialog,
 * кнопка-триггер должна остаться видимой и кликабельной поверх панели).
 */
import { useEffect } from "react";
import { Bot, Trash2, X } from "lucide-react";
import { useChatWidget } from "@/stores/chatWidget";
import { cn } from "@/lib/utils/cn";
import { ChatMessageList } from "./ChatMessageList";
import { ChatComposer } from "./ChatComposer";

export function AssistantWidget() {
  const open = useChatWidget((s) => s.open);
  const unread = useChatWidget((s) => s.unread);
  const lastModel = useChatWidget((s) => s.lastModel);
  const setOpen = useChatWidget((s) => s.setOpen);
  const toggle = useChatWidget((s) => s.toggle);
  const clearMessages = useChatWidget((s) => s.clearMessages);

  // Esc закрывает панель, пока она открыта.
  useEffect(() => {
    if (!open) return;
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [open, setOpen]);

  return (
    <>
      <button
        type="button"
        onClick={toggle}
        aria-label={open ? "Закрыть AI-ассистента" : "Открыть AI-ассистента"}
        aria-expanded={open}
        className={cn(
          // ВАЖНО: без `relative` — он побеждает `fixed` в порядке CSS-утилит Tailwind,
          // и кнопка выпадает из фиксированного угла в поток страницы. Бейджу хватает
          // positioning context от `fixed`.
          "fixed right-6 bottom-6 z-[80]",
          "size-12 flex items-center justify-center",
          "bg-bg-1 border border-[var(--hairline-strong)] text-bg-11 rounded-[var(--radius-2)]",
          "hover:bg-bg-2 hover:border-accent transition-colors duration-[120ms]",
          "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent",
        )}
      >
        {open ? <X size={20} aria-hidden="true" /> : <Bot size={20} aria-hidden="true" />}
        {!open && unread > 0 ? (
          <span
            aria-hidden="true"
            className={cn(
              "absolute -top-1.5 -right-1.5 min-w-[18px] h-[18px] px-1",
              "rounded-full bg-warning text-bg-0 text-[10px] font-display font-semibold",
              "flex items-center justify-center leading-none",
            )}
          >
            {unread > 9 ? "9+" : unread}
          </span>
        ) : null}
      </button>

      {open ? (
        <div
          role="dialog"
          aria-label="AI-ассистент"
          className={cn(
            "fixed right-6 bottom-20 z-[80]",
            "w-[380px] max-w-[calc(100vw-1.5rem)]",
            "h-[min(560px,70vh)]",
            "bg-bg-1 border border-[var(--hairline-strong)] rounded-[var(--radius-3)]",
            "flex flex-col overflow-hidden",
          )}
        >
          <header className="flex items-center justify-between border-b border-[var(--hairline)] px-3.5 py-2.5 shrink-0 gap-2">
            <span className="font-display text-[10px] tracking-[0.14em] uppercase text-bg-8 shrink-0">
              Ассистент
            </span>
            <div className="flex items-center gap-1 min-w-0">
              {lastModel ? (
                <span
                  className="font-display text-[10px] text-bg-8 truncate max-w-[120px]"
                  title={lastModel}
                >
                  {lastModel}
                </span>
              ) : null}
              <button
                type="button"
                onClick={clearMessages}
                aria-label="Очистить чат"
                title="Очистить чат"
                className="size-7 shrink-0 flex items-center justify-center rounded-[var(--radius-1)] text-bg-9 hover:bg-bg-2 hover:text-bg-11 transition-colors duration-[120ms]"
              >
                <Trash2 size={14} aria-hidden="true" />
              </button>
              <button
                type="button"
                onClick={() => setOpen(false)}
                aria-label="Закрыть"
                className="size-7 shrink-0 flex items-center justify-center rounded-[var(--radius-1)] text-bg-9 hover:bg-bg-2 hover:text-bg-11 transition-colors duration-[120ms]"
              >
                <X size={14} aria-hidden="true" />
              </button>
            </div>
          </header>

          <ChatMessageList />
          <ChatComposer />
        </div>
      ) : null}
    </>
  );
}
