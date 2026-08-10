/**
 * Drawer — right-side slide-in.
 * Esc закрывает, focus-trap через Radix Dialog.Content.
 * Width: 480px (default) / 640px (timeline drill-down).
 */
import * as Dialog from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import { type ReactNode, type RefObject } from "react";
import { cn } from "@/lib/utils/cn";

interface DrawerProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title?: ReactNode;
  /** Подзаголовок / eyebrow-ID под title. */
  description?: ReactNode;
  /** Eyebrow-строка над title (например "06 · AD DETAIL"). */
  eyebrow?: ReactNode;
  width?: 480 | 560 | 640;
  /** Слот для footer с кнопками действий. */
  footer?: ReactNode;
  children: ReactNode;
  returnFocusRef?: RefObject<HTMLElement | null>;
}

export function Drawer({
  open,
  onOpenChange,
  title,
  description,
  eyebrow,
  width = 640,
  footer,
  children,
  returnFocusRef,
}: DrawerProps) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        {/* Overlay — scrim без blur (канон: depth через 1px border, не тени/blur) */}
        <Dialog.Overlay className="fixed inset-0 bg-[rgba(10,10,11,0.66)] z-[50] data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=closed]:animate-out data-[state=closed]:fade-out-0" />
        <Dialog.Content
          style={{ width: "100%", maxWidth: `${width}px` }}
          className={cn(
            "fixed top-0 right-0 bottom-0 z-[51]",
            "bg-bg-1 border-l border-[var(--color-hairline)] rounded-l-[var(--radius-3)] overflow-hidden",
            "flex flex-col",
            "focus:outline-none",
            // Slide-in анимация
            "data-[state=open]:animate-in data-[state=open]:slide-in-from-right",
            "data-[state=closed]:animate-out data-[state=closed]:slide-out-to-right",
            "duration-200",
          )}
          // aria-describedby нужен для Radix, если description не задан
          aria-describedby={description ? undefined : "drawer-desc-hidden"}
          onCloseAutoFocus={(event) => {
            if (!returnFocusRef?.current) return;
            event.preventDefault();
            returnFocusRef.current.focus();
          }}
        >
          {/* Скрытый span для Radix a11y когда description пустой */}
          {!description && (
            <Dialog.Description id="drawer-desc-hidden" className="sr-only">
              Панель деталей
            </Dialog.Description>
          )}

          {/* Header */}
          <div className="flex shrink-0 items-start justify-between gap-4 border-b border-[var(--color-hairline)] px-4 py-5 sm:px-8 sm:py-6">
            <div className="flex-1 min-w-0">
              {eyebrow ? (
                <div className="font-display text-[12px] tracking-[0.14em] uppercase text-bg-8 mb-2">
                  {eyebrow}
                </div>
              ) : null}
              {title ? (
                <Dialog.Title className="font-display text-[20px] font-medium text-bg-11 m-0 mb-1.5 truncate leading-[1.2]">
                  {title}
                </Dialog.Title>
              ) : null}
              {description ? (
                <Dialog.Description className="text-[12px] font-display text-bg-9 tracking-[0.02em]">
                  {description}
                </Dialog.Description>
              ) : null}
            </div>
            {/* Close-кнопка (квадрат с border согласно макету) */}
            <Dialog.Close
              aria-label="Закрыть"
              className={cn(
                "size-11 shrink-0 inline-flex items-center justify-center rounded-[var(--radius-2)]",
                "bg-transparent border border-[var(--color-hairline-strong)] text-bg-10",
                "hover:bg-bg-2 transition-colors duration-[120ms]",
                "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent",
              )}
            >
              <X size={14} aria-hidden="true" />
            </Dialog.Close>
          </div>

          {/* Body — прокручиваемая зона */}
          <div className="flex-1 overflow-y-auto px-4 py-5 sm:px-8 sm:py-6">{children}</div>

          {/* Footer — если задан */}
          {footer ? (
            <div className="flex shrink-0 items-center justify-between gap-3 border-t border-[var(--color-hairline)] bg-bg-1 px-4 py-4 sm:px-8">
              {footer}
            </div>
          ) : null}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
