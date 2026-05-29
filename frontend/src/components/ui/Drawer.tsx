/**
 * Drawer — right-side slide-in.
 * Width 480px по умолчанию, 640px для timeline drill-down.
 */

import * as Dialog from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import { type ReactNode } from "react";
import { cn } from "@/lib/utils/cn";

interface DrawerProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title?: ReactNode;
  description?: ReactNode;
  width?: 480 | 640;
  children: ReactNode;
}

export function Drawer({
  open,
  onOpenChange,
  title,
  description,
  width = 480,
  children,
}: DrawerProps) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-bg-0/70 z-[50] data-[state=open]:animate-in data-[state=open]:fade-in-0" />
        <Dialog.Content
          style={{ width: `${width}px` }}
          className={cn(
            "fixed top-0 right-0 bottom-0 z-[50]",
            "bg-bg-1 border-l border-bg-5",
            "flex flex-col",
            "focus:outline-none",
            "data-[state=open]:animate-in data-[state=open]:slide-in-from-right-1/2",
          )}
        >
          {(title || description) && (
            <div className="flex items-start justify-between border-b border-bg-5 p-6">
              <div className="flex-1 min-w-0 mr-4">
                {title ? (
                  <Dialog.Title className="font-display text-[18px] font-medium text-bg-11 m-0 truncate">
                    {title}
                  </Dialog.Title>
                ) : null}
                {description ? (
                  <Dialog.Description className="mt-1.5 text-bg-10 text-[13px]">
                    {description}
                  </Dialog.Description>
                ) : null}
              </div>
              <Dialog.Close
                aria-label="Закрыть"
                className="size-7 shrink-0 inline-flex items-center justify-center text-bg-9 hover:text-bg-11 transition-colors"
              >
                <X size={16} aria-hidden="true" />
              </Dialog.Close>
            </div>
          )}
          <div className="flex-1 overflow-auto p-6">{children}</div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
