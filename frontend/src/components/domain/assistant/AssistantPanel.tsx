import { useEffect, useRef, type RefObject } from "react";
import { Trash2, X } from "lucide-react";

import { useChatWidget } from "@/stores/chatWidget";
import { cn } from "@/lib/utils/cn";

import { ChatComposer } from "./ChatComposer";
import { ChatMessageList } from "./ChatMessageList";
import {
  ASSISTANT_PANEL_HEIGHT,
  ASSISTANT_PANEL_POSITION,
  ASSISTANT_PANEL_WIDTH,
} from "./assistantGeometry";

export function AssistantPanel({
  returnFocusRef,
}: {
  returnFocusRef: RefObject<HTMLButtonElement | null>;
}) {
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const lastModel = useChatWidget((state) => state.lastModel);
  const setOpen = useChatWidget((state) => state.setOpen);
  const clearMessages = useChatWidget((state) => state.clearMessages);

  useEffect(() => {
    const returnFocus = returnFocusRef.current;
    closeButtonRef.current?.focus();
    return () => returnFocus?.focus();
  }, [returnFocusRef]);

  return (
    <div
      role="dialog"
      aria-label="AI-ассистент"
      className={cn(
        ASSISTANT_PANEL_POSITION,
        ASSISTANT_PANEL_WIDTH,
        ASSISTANT_PANEL_HEIGHT,
        "z-[80]",
        "bg-bg-1 border border-[var(--color-hairline-strong)] rounded-[var(--radius-3)]",
        "flex flex-col overflow-hidden",
      )}
    >
      <header className="flex items-center justify-between border-b border-[var(--color-hairline)] px-3.5 py-2.5 shrink-0 gap-2">
        <span className="font-display text-[12px] tracking-[0.14em] uppercase text-bg-8 shrink-0">
          Ассистент
        </span>
        <div className="flex items-center gap-1 min-w-0">
          {lastModel ? (
            <span
              className="font-display text-[12px] text-bg-8 truncate max-w-[120px]"
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
            className="size-11 shrink-0 flex items-center justify-center rounded-[var(--radius-2)] text-bg-9 hover:bg-bg-2 hover:text-bg-11 transition-colors duration-[120ms]"
          >
            <Trash2 size={14} aria-hidden="true" />
          </button>
          <button
            ref={closeButtonRef}
            type="button"
            onClick={() => setOpen(false)}
            aria-label="Закрыть"
            className="size-11 shrink-0 flex items-center justify-center rounded-[var(--radius-2)] text-bg-9 hover:bg-bg-2 hover:text-bg-11 transition-colors duration-[120ms]"
          >
            <X size={14} aria-hidden="true" />
          </button>
        </div>
      </header>

      <ChatMessageList />
      <ChatComposer />
    </div>
  );
}
