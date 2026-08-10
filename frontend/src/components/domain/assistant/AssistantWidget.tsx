/**
 * AssistantWidget — плавающий AI-ассистент дашборда.
 *
 * Кнопка (fixed right-6 bottom-6) открывает панель чата (fixed right-6 bottom-20,
 * ~380px × min(560px, 70vh)). История — в сторе useChatWidget (в памяти вкладки,
 * без persist).
 *
 * Почасовой пульс: виджет раз в час вызывает POST /ai/pulse (сервер кэширует
 * ответ на календарный час — опрос дешёвый); в чат попадает сообщение ТОЛЬКО
 * при important=true, тишина — ничего не появляется. Опрос идёт лишь при
 * видимой вкладке; при возврате видимости после >55 мин простоя — опрос сразу.
 *
 * Esc закрывает панель — обычный div + window keydown listener (без Radix Dialog,
 * кнопка-триггер должна остаться видимой и кликабельной поверх панели).
 */
import { lazy, Suspense, useEffect, useRef } from "react";
import { Bot, X } from "lucide-react";
import { useChatWidget } from "@/stores/chatWidget";
import { cn } from "@/lib/utils/cn";
import {
  ASSISTANT_PANEL_POSITION,
  ASSISTANT_PANEL_WIDTH,
  ASSISTANT_TRIGGER_POSITION,
} from "./assistantGeometry";

const LazyAssistantPanel = lazy(() =>
  import("./AssistantPanel").then((module) => ({ default: module.AssistantPanel })),
);

/** Задержка первого опроса пульса после монтирования — не мешаем первичной загрузке. */
const PULSE_INITIAL_DELAY_MS = 5_000;
/** Период почасового опроса пульса. */
const PULSE_INTERVAL_MS = 60 * 60 * 1000;
/** Если вкладка стала видимой и с последнего опроса прошло больше — опросить сразу. */
const PULSE_STALE_MS = 55 * 60 * 1000;

export function AssistantWidget() {
  const triggerRef = useRef<HTMLButtonElement>(null);
  const open = useChatWidget((s) => s.open);
  const unread = useChatWidget((s) => s.unread);
  const setOpen = useChatWidget((s) => s.setOpen);
  const toggle = useChatWidget((s) => s.toggle);
  const fetchPulse = useChatWidget((s) => s.fetchPulse);

  // Почасовой опрос /ai/pulse: старт через ~5с после монтирования, затем раз в час.
  // Скрытая вкладка не опрашивает; при возврате видимости после >55 мин — опрос сразу.
  useEffect(() => {
    let lastPollAt = 0;

    function poll() {
      if (document.visibilityState !== "visible") return;
      lastPollAt = Date.now();
      void fetchPulse();
    }

    const initialTimer = window.setTimeout(poll, PULSE_INITIAL_DELAY_MS);
    const intervalTimer = window.setInterval(poll, PULSE_INTERVAL_MS);

    function onVisibilityChange() {
      if (document.visibilityState === "visible" && Date.now() - lastPollAt > PULSE_STALE_MS) {
        poll();
      }
    }
    document.addEventListener("visibilitychange", onVisibilityChange);

    return () => {
      window.clearTimeout(initialTimer);
      window.clearInterval(intervalTimer);
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, [fetchPulse]);

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
        ref={triggerRef}
        type="button"
        onClick={toggle}
        aria-label={open ? "Закрыть AI-ассистента" : "Открыть AI-ассистента"}
        aria-expanded={open}
        className={cn(
          // ВАЖНО: без `relative` — он побеждает `fixed` в порядке CSS-утилит Tailwind,
          // и кнопка выпадает из фиксированного угла в поток страницы. Бейджу хватает
          // positioning context от `fixed`.
          ASSISTANT_TRIGGER_POSITION,
          "z-[80]",
          "size-12 flex items-center justify-center",
          "bg-bg-1 border border-[var(--color-hairline-strong)] text-bg-11 rounded-[var(--radius-2)]",
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
              "rounded-full bg-warning text-bg-0 text-[12px] font-display font-semibold",
              "flex items-center justify-center leading-none",
            )}
          >
            {unread > 9 ? "9+" : unread}
          </span>
        ) : null}
      </button>

      {open ? (
        <Suspense fallback={<AssistantPanelFallback />}>
          <LazyAssistantPanel returnFocusRef={triggerRef} />
        </Suspense>
      ) : null}
    </>
  );
}

export function AssistantPanelFallback() {
  return (
    <div
      role="status"
      aria-label="Загрузка AI-ассистента"
      className={cn(
        ASSISTANT_PANEL_POSITION,
        ASSISTANT_PANEL_WIDTH,
        "z-[80] h-24 animate-pulse rounded-[var(--radius-3)] border border-[var(--color-hairline-strong)] bg-bg-1 motion-reduce:animate-none",
      )}
    />
  );
}
