/**
 * ChatComposer — инпут AssistantWidget: textarea 1-3 строки (авто-рост, cap),
 * Enter отправляет, Shift+Enter — перенос строки. Disabled пока pending.
 */
import { useRef, useState, type KeyboardEvent } from "react";
import { Send } from "lucide-react";
import { useChatWidget } from "@/stores/chatWidget";
import { cn } from "@/lib/utils/cn";

/** ~3 строки при текущем размере шрифта/паддинге. */
const MAX_TEXTAREA_HEIGHT_PX = 72;

export function ChatComposer() {
  const [value, setValue] = useState("");
  const pending = useChatWidget((s) => s.pending);
  const sendMessage = useChatWidget((s) => s.sendMessage);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  function autosize(el: HTMLTextAreaElement | null) {
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, MAX_TEXTAREA_HEIGHT_PX)}px`;
  }

  async function submit() {
    const text = value.trim();
    if (!text || pending) return;
    setValue("");
    autosize(textareaRef.current);
    await sendMessage(text);
    textareaRef.current?.focus();
  }

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void submit();
    }
  }

  return (
    <div className="shrink-0 border-t border-[var(--hairline)] p-2.5 flex items-end gap-2">
      <textarea
        ref={textareaRef}
        value={value}
        onChange={(e) => {
          setValue(e.target.value);
          autosize(e.target);
        }}
        onKeyDown={onKeyDown}
        disabled={pending}
        rows={1}
        placeholder="Спроси что-нибудь…"
        aria-label="Сообщение ассистенту"
        className={cn(
          "flex-1 resize-none bg-bg-2 border border-[var(--hairline-strong)] text-bg-11",
          "rounded-[var(--radius-2)] px-3 py-2 text-[13px] font-body leading-[1.4]",
          "placeholder:text-bg-9 max-h-[72px]",
          "focus:bg-bg-3 focus:border-accent focus:outline-none",
          "disabled:opacity-50 disabled:cursor-not-allowed",
        )}
      />
      <button
        type="button"
        onClick={() => void submit()}
        disabled={pending || !value.trim()}
        aria-label="Отправить сообщение"
        className={cn(
          "size-9 shrink-0 flex items-center justify-center rounded-[var(--radius-2)]",
          "bg-accent border border-accent text-bg-0",
          "hover:bg-accent-muted hover:border-accent-muted transition-colors duration-[120ms]",
          "disabled:opacity-40 disabled:cursor-not-allowed",
        )}
      >
        <Send size={15} aria-hidden="true" />
      </button>
    </div>
  );
}
