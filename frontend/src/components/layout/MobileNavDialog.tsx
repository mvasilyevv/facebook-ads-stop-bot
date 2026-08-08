import * as Dialog from "@radix-ui/react-dialog";
import type { RefObject } from "react";

import { Sidebar } from "./Sidebar";

interface MobileNavDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  returnFocusRef?: RefObject<HTMLElement | null>;
}

export function MobileNavDialog({ open, onOpenChange, returnFocusRef }: MobileNavDialogProps) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-[60] bg-[rgba(10,10,11,0.72)] md:hidden" />
        <Dialog.Content
          onCloseAutoFocus={(event) => {
            if (!returnFocusRef?.current) return;
            event.preventDefault();
            returnFocusRef.current.focus();
          }}
          className="fixed inset-y-0 left-0 z-[61] outline-none md:hidden data-[state=open]:animate-in data-[state=open]:slide-in-from-left data-[state=closed]:animate-out data-[state=closed]:slide-out-to-left"
          aria-describedby="mobile-nav-description"
        >
          <Dialog.Title className="sr-only">Навигация</Dialog.Title>
          <Dialog.Description id="mobile-nav-description" className="sr-only">
            Основные разделы панели управления
          </Dialog.Description>
          <Sidebar mobile onNavigate={() => onOpenChange(false)} />
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
