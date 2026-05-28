/**
 * Toast — поверх Radix Toast.
 * Position bottom-right, stack 12px gap, max 4 visible.
 */

import * as RadixToast from "@radix-ui/react-toast";
import { CheckCircle2, AlertCircle, Info, AlertTriangle, X } from "lucide-react";
import { create } from "zustand";
import { type ReactNode, useEffect } from "react";
import { cn } from "@/lib/utils/cn";

export type ToastVariant = "success" | "error" | "info" | "warning";

interface ToastItem {
  id: string;
  title: ReactNode;
  description?: ReactNode;
  variant: ToastVariant;
  duration: number;
}

interface ToastStore {
  toasts: ToastItem[];
  add: (toast: Omit<ToastItem, "id">) => void;
  remove: (id: string) => void;
}

const useToastStore = create<ToastStore>((set) => ({
  toasts: [],
  add: (toast) => {
    const id = `t_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
    set((s) => ({ toasts: [...s.toasts, { ...toast, id }] }));
  },
  remove: (id) => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),
}));

/** Программный API для эмита toasts. */
export const toast = {
  success: (title: ReactNode, description?: ReactNode) =>
    useToastStore.getState().add({ title, description, variant: "success", duration: 4000 }),
  info: (title: ReactNode, description?: ReactNode) =>
    useToastStore.getState().add({ title, description, variant: "info", duration: 4000 }),
  warning: (title: ReactNode, description?: ReactNode) =>
    useToastStore.getState().add({ title, description, variant: "warning", duration: 8000 }),
  error: (title: ReactNode, description?: ReactNode) =>
    useToastStore.getState().add({ title, description, variant: "error", duration: 0 }),
};

const ICONS: Record<ToastVariant, ReactNode> = {
  success: <CheckCircle2 size={16} className="text-success" aria-hidden="true" />,
  error: <AlertCircle size={16} className="text-danger" aria-hidden="true" />,
  info: <Info size={16} className="text-info" aria-hidden="true" />,
  warning: <AlertTriangle size={16} className="text-warning" aria-hidden="true" />,
};

const VARIANT_CLASS: Record<ToastVariant, string> = {
  success: "border-[rgba(126,180,122,0.3)]",
  error: "border-[rgba(199,98,92,0.3)]",
  info: "border-[rgba(122,160,180,0.3)]",
  warning: "border-[rgba(212,168,88,0.3)]",
};

export function ToastViewport() {
  const toasts = useToastStore((s) => s.toasts);
  const remove = useToastStore((s) => s.remove);

  return (
    <RadixToast.Provider swipeDirection="right">
      {toasts.slice(-4).map((t) => (
        <ToastItemView key={t.id} item={t} onClose={() => remove(t.id)} />
      ))}
      <RadixToast.Viewport className="fixed bottom-4 right-4 z-[70] flex flex-col gap-3 w-[380px] m-0 list-none outline-none" />
    </RadixToast.Provider>
  );
}

function ToastItemView({ item, onClose }: { item: ToastItem; onClose: () => void }) {
  useEffect(() => {
    if (item.duration <= 0) return;
    const t = window.setTimeout(onClose, item.duration);
    return () => window.clearTimeout(t);
  }, [item.duration, onClose]);

  return (
    <RadixToast.Root
      open
      onOpenChange={(o) => !o && onClose()}
      role={item.variant === "error" ? "alert" : "status"}
      className={cn(
        "bg-bg-2 border p-4 flex items-start gap-3",
        VARIANT_CLASS[item.variant],
      )}
    >
      <span className="mt-0.5">{ICONS[item.variant]}</span>
      <div className="flex-1 min-w-0">
        <RadixToast.Title className="text-[13px] text-bg-11 font-medium font-body">
          {item.title}
        </RadixToast.Title>
        {item.description ? (
          <RadixToast.Description className="mt-1 text-[12px] text-bg-10">
            {item.description}
          </RadixToast.Description>
        ) : null}
      </div>
      <RadixToast.Close
        aria-label="Закрыть"
        className="text-bg-9 hover:text-bg-11 transition-colors"
      >
        <X size={14} aria-hidden="true" />
      </RadixToast.Close>
    </RadixToast.Root>
  );
}
