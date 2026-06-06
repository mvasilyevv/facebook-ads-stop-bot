/**
 * Modal — центрированный Radix Dialog.
 * backdrop-blur-sm overlay, sharp panel (border-bg-5), Esc закрывает.
 * Sizes: sm 480px / md 640px / lg 800px.
 * focus-trap встроен в Radix Dialog.Content.
 */
import * as Dialog from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import { type ReactNode } from "react";
import { cn } from "./cn";

interface ModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title?: ReactNode;
  description?: ReactNode;
  size?: "sm" | "md" | "lg";
  children: ReactNode;
  hideCloseButton?: boolean;
  /** Дополнительные классы для Content-панели. */
  contentClassName?: string;
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
  size = "sm",
  children,
  hideCloseButton,
  contentClassName,
}: ModalProps) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        {/* Overlay с blur */}
        <Dialog.Overlay className="fixed inset-0 bg-bg-0/70 backdrop-blur-sm z-[60] data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=closed]:animate-out data-[state=closed]:fade-out-0" />
        <Dialog.Content
          className={cn(
            "fixed left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 z-[60]",
            "bg-bg-1 border border-bg-5",
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
          {!hideCloseButton ? (
            <Dialog.Close
              aria-label="Закрыть"
              className="absolute top-4 right-4 size-7 inline-flex items-center justify-center text-bg-9 hover:text-bg-11 transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
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
export function ModalFooter({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex justify-end gap-2 mt-6 pt-4 border-t border-bg-5", className)}>
      {children}
    </div>
  );
}
