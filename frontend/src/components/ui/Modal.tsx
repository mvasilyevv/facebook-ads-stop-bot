/**
 * Modal — Centered dialog поверх Radix.
 * Max-width: sm 480px / md 640px / lg 800px.
 */

import * as Dialog from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import { type ReactNode } from "react";
import { cn } from "@/lib/utils/cn";

interface ModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title?: ReactNode;
  description?: ReactNode;
  size?: "sm" | "md" | "lg";
  children: ReactNode;
  hideCloseButton?: boolean;
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
}: ModalProps) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-bg-0/70 backdrop-blur-sm z-[60] data-[state=open]:animate-in data-[state=open]:fade-in-0" />
        <Dialog.Content
          className={cn(
            "fixed left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 z-[60]",
            "bg-bg-1 border border-bg-5 rounded-[4px]",
            "w-[calc(100vw-32px)] max-h-[calc(100vh-64px)] overflow-auto",
            "p-6",
            "focus:outline-none",
            SIZE_CLASS[size],
          )}
        >
          {(title || description) && (
            <div className="mb-5">
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
          {!hideCloseButton ? (
            <Dialog.Close
              aria-label="Закрыть"
              className="absolute top-4 right-4 size-7 inline-flex items-center justify-center text-bg-9 hover:text-bg-11 transition-colors"
            >
              <X size={16} aria-hidden="true" />
            </Dialog.Close>
          ) : null}
          {children}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
