/**
 * Drawer — right-side slide-in.
 * Спека: ads.html .drawer — 640px, overlay blur(2px), header/body/footer.
 * Esc закрывает, focus-trap через Radix Dialog.Content.
 * Width: 480px (default) / 640px (timeline drill-down).
 */
import * as Dialog from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import { type ReactNode } from "react";
import { cn } from "@/lib/utils/cn";

interface DrawerProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title?: ReactNode;
  /** Подзаголовок / eyebrow-ID под title. */
  description?: ReactNode;
  /** Eyebrow-строка над title (например "06 · AD DETAIL"). */
  eyebrow?: ReactNode;
  width?: 480 | 640;
  /** Слот для footer с кнопками действий. */
  footer?: ReactNode;
  children: ReactNode;
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
}: DrawerProps) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        {/* Overlay — blur(2px) как в макете */}
        <Dialog.Overlay className="fixed inset-0 bg-[rgba(10,10,11,0.65)] backdrop-blur-[2px] z-[50] data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=closed]:animate-out data-[state=closed]:fade-out-0" />
        <Dialog.Content
          style={{ width: `${width}px` }}
          className={cn(
            "fixed top-0 right-0 bottom-0 z-[51]",
            "bg-bg-1 border-l border-bg-5",
            "flex flex-col",
            "focus:outline-none",
            // Slide-in анимация
            "data-[state=open]:animate-in data-[state=open]:slide-in-from-right",
            "data-[state=closed]:animate-out data-[state=closed]:slide-out-to-right",
            "duration-200",
          )}
          // aria-describedby нужен для Radix, если description не задан
          aria-describedby={description ? undefined : "drawer-desc-hidden"}
        >
          {/* Скрытый span для Radix a11y когда description пустой */}
          {!description && (
            <Dialog.Description id="drawer-desc-hidden" className="sr-only">
              Панель деталей
            </Dialog.Description>
          )}

          {/* Header */}
          <div className="flex items-start justify-between border-b border-bg-5 px-8 py-6 gap-4 shrink-0">
            <div className="flex-1 min-w-0">
              {eyebrow ? (
                <div className="font-display text-[10px] tracking-[0.14em] uppercase text-bg-8 mb-2">
                  {eyebrow}
                </div>
              ) : null}
              {title ? (
                <Dialog.Title className="font-display text-[20px] font-medium text-bg-11 m-0 mb-1.5 truncate leading-[1.2]">
                  {title}
                </Dialog.Title>
              ) : null}
              {description ? (
                <Dialog.Description className="text-[11px] font-display text-bg-9 tracking-[0.02em]">
                  {description}
                </Dialog.Description>
              ) : null}
            </div>
            {/* Close-кнопка (квадрат с border согласно макету) */}
            <Dialog.Close
              aria-label="Закрыть"
              className={cn(
                "size-8 shrink-0 inline-flex items-center justify-center",
                "bg-transparent border border-bg-6 text-bg-10",
                "hover:bg-bg-2 transition-colors duration-[120ms]",
                "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent",
              )}
            >
              <X size={14} aria-hidden="true" />
            </Dialog.Close>
          </div>

          {/* Body — прокручиваемая зона */}
          <div className="flex-1 overflow-y-auto px-8 py-6">
            {children}
          </div>

          {/* Footer — если задан */}
          {footer ? (
            <div className="shrink-0 border-t border-bg-5 px-8 py-4 bg-bg-1 flex items-center justify-between gap-3">
              {footer}
            </div>
          ) : null}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
