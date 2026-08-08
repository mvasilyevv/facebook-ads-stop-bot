/**
 * Sheet — bottom-sheet модалка на Radix Dialog.
 * Скруглённые верхние углы (radius-3), hairline-граница (airy-вид).
 * Telegram content safe area применяется к портальному контенту.
 * prefers-reduced-motion: анимация отключается.
 */
import * as Dialog from "@radix-ui/react-dialog";
import type { ReactNode } from "react";
import { X } from "lucide-react";
import { cn } from "@/lib/cn";

interface SheetProps {
  open: boolean;
  onClose: () => void;
  title?: string;
  /** Eyebrow над title. */
  eyebrow?: string;
  children: ReactNode;
  className?: string;
}

export function Sheet({
  open,
  onClose,
  title,
  eyebrow,
  children,
  className,
}: SheetProps) {
  return (
    <Dialog.Root
      open={open}
      onOpenChange={(v) => {
        if (!v) onClose();
      }}
    >
      <Dialog.Portal>
        {/* Затемнение фона */}
        <Dialog.Overlay
          className={cn(
            "fixed inset-0 bg-black/60 z-40",
            "data-[state=open]:animate-[fade-in_200ms_ease]",
            "data-[state=closed]:animate-[fade-out_150ms_ease]",
            "motion-reduce:animate-none",
          )}
        />
        {/* Сам sheet — снизу, без radius-top */}
        <Dialog.Content
          aria-describedby={undefined}
          className={cn(
            "fixed bottom-0 z-50 flex flex-col",
            "left-[max(var(--tg-content-safe-left,0px),env(safe-area-inset-left))]",
            "right-[max(var(--tg-content-safe-right,0px),env(safe-area-inset-right))]",
            "max-h-[calc(var(--tg-viewport-stable-height,100dvh)-max(var(--tg-content-safe-top,0px),env(safe-area-inset-top)))]",
            "bg-[var(--color-bg-1)] border-t border-[var(--color-hairline)]",
            "rounded-t-[var(--radius-3)] overflow-hidden",
            "max-w-[480px] mx-auto",
            // анимация slide-up
            "data-[state=open]:animate-[slide-up_250ms_var(--ease-out)]",
            "data-[state=closed]:animate-[slide-down_180ms_var(--ease-in)]",
            "motion-reduce:animate-none",
            // Keep actions above Telegram chrome and the native gesture area.
            "pb-[max(16px,var(--tg-content-safe-bottom,0px),env(safe-area-inset-bottom))]",
            className,
          )}
        >
          {/* Drag indicator */}
          <div className="flex shrink-0 justify-center pt-3 pb-2">
            <div className="w-10 h-1 bg-[var(--color-bg-6)] rounded-full" />
          </div>
          <Dialog.Close asChild>
            <button
              type="button"
              aria-label="Закрыть"
              className="absolute right-2 top-2 inline-flex size-11 touch-manipulation items-center justify-center rounded-[var(--radius-2)] text-bg-9 hover:bg-bg-2 hover:text-bg-11 focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
            >
              <X size={20} aria-hidden="true" />
            </button>
          </Dialog.Close>

          {/* Заголовок */}
          {(eyebrow || title) && (
            <div className="shrink-0 px-4 pb-4 border-b border-[var(--color-hairline)]">
              {eyebrow && (
                <p className="text-[12px] uppercase tracking-[0.08em] text-[var(--color-bg-9)] font-mono mb-1">
                  {eyebrow}
                </p>
              )}
              {title && (
                <Dialog.Title className="text-[16px] font-semibold text-[var(--color-bg-11)] font-display">
                  {title}
                </Dialog.Title>
              )}
            </div>
          )}

          {/* Контент */}
          <div
            className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-4 pt-4 [scrollbar-gutter:stable]"
            data-sheet-scroll
          >
            {children}
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
