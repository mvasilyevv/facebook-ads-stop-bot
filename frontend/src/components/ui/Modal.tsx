/**
 * Modal — центрированный Radix Dialog.
 * backdrop-blur-sm overlay, airy panel (hairline-граница + radius-3), Esc закрывает.
 * Sizes: sm 480px / md 640px / lg 800px.
 * focus-trap встроен в Radix Dialog.Content.
 */
import * as Dialog from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import { type ReactNode, type RefObject } from "react";
import { cn } from "@/lib/utils/cn";

interface ModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title?: ReactNode;
  description?: ReactNode;
  /** Accessible title when the caller renders a custom visible header. */
  ariaTitle?: string;
  /** Accessible description when the caller renders custom explanatory copy. */
  ariaDescription?: string;
  size?: "sm" | "md" | "lg";
  children: ReactNode;
  hideCloseButton?: boolean;
  /** Дополнительные классы для Content-панели. */
  contentClassName?: string;
  /** Explicit focus return for controlled dialogs without a Radix Trigger. */
  returnFocusRef?: RefObject<HTMLElement | null>;
}

const SIZE_CLASS: Record<NonNullable<ModalProps["size"]>, string> = {
  sm: "max-w-[480px]",
  md: "max-w-[640px]",
  lg: "max-w-[800px]",
};

export function Modal({
  open,
  onOpenChange,
  title,
  description,
  ariaTitle,
  ariaDescription,
  size = "sm",
  children,
  hideCloseButton,
  contentClassName,
  returnFocusRef,
}: ModalProps) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        {/* Overlay с blur */}
        <Dialog.Overlay className="fixed inset-0 bg-bg-0/70 backdrop-blur-sm z-[60] data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=closed]:animate-out data-[state=closed]:fade-out-0" />
        <Dialog.Content
          onCloseAutoFocus={(event) => {
            if (!returnFocusRef?.current) return;
            event.preventDefault();
            returnFocusRef.current.focus();
          }}
          className={cn(
            "fixed left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 z-[60]",
            "bg-bg-1 border border-[var(--color-hairline)] rounded-[var(--radius-3)]",
            "w-[calc(100vw-32px)] max-h-[calc(100vh-64px)] overflow-auto",
            "p-6",
            "focus:outline-none",
            // Анимация появления
            "data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95",
            "data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=closed]:zoom-out-95",
            SIZE_CLASS[size],
            contentClassName,
          )}
        >
          {!title && ariaTitle ? (
            <Dialog.Title className="sr-only">{ariaTitle}</Dialog.Title>
          ) : null}
          {!description && ariaDescription ? (
            <Dialog.Description className="sr-only">{ariaDescription}</Dialog.Description>
          ) : null}
          {!hideCloseButton ? (
            <Dialog.Close
              aria-label="Закрыть"
              className="absolute right-2 top-2 inline-flex size-11 items-center justify-center rounded-[var(--radius-2)] text-bg-9 transition-colors hover:bg-bg-2 hover:text-bg-11 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
            >
              <X size={16} aria-hidden="true" />
            </Dialog.Close>
          ) : null}

          {(title || description) && (
            <div className={cn("mb-5", !hideCloseButton && "pr-8")}>
              {title ? (
                <Dialog.Title className="font-display text-[18px] font-medium text-bg-11 m-0">
                  {title}
                </Dialog.Title>
              ) : null}
              {description ? (
                <Dialog.Description className="mt-1.5 text-bg-10 text-[13px]">
                  {description}
                </Dialog.Description>
              ) : null}
            </div>
          )}

          {children}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

/** Вспомогательные под-компоненты для структуры Modal без лишних div'ов. */
export function ModalFooter({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div
      className={cn(
        "flex justify-end gap-2 mt-6 pt-4 border-t border-[var(--color-hairline)]",
        className,
      )}
    >
      {children}
    </div>
  );
}
