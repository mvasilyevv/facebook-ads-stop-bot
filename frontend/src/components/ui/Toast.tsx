/**
 * Toast — Radix Toast + Zustand-стор для программного вызова.
 * Position: bottom-right, max 4 тоста, gap 12px.
 * error: duration 0 (нет автозакрытия), warning: 8s, остальные 4s.
 *
 * Использование:
 *   import { toast } from "@/components/ui/Toast";
 *   toast.success("Сохранено");
 *   toast.error("Ошибка", err.message);
 *
 * В layout-shell рендерить <ToastViewport /> один раз.
 */
import * as RadixToast from "@radix-ui/react-toast";
import { CheckCircle2, AlertCircle, Info, AlertTriangle, X } from "lucide-react";
import { type ReactNode, useEffect } from "react";
import { cn } from "@/lib/utils/cn";
import { useToastStore, type ToastItem, type ToastVariant } from "./toastStore";

export { toast } from "./toastStore";
export type { ToastItem, ToastVariant } from "./toastStore";

const ICONS: Record<ToastVariant, ReactNode> = {
  success: <CheckCircle2 size={16} className="text-success" aria-hidden="true" />,
  error: <AlertCircle size={16} className="text-danger" aria-hidden="true" />,
  info: <Info size={16} className="text-info" aria-hidden="true" />,
  warning: <AlertTriangle size={16} className="text-warning" aria-hidden="true" />,
};

const VARIANT_BORDER: Record<ToastVariant, string> = {
  success: "border-[rgba(126,180,122,0.3)]",
  error: "border-[rgba(199,98,92,0.3)]",
  info: "border-[rgba(122,160,180,0.3)]",
  warning: "border-[rgba(212,168,88,0.3)]",
};

/** Рендерит стек тостов. Положить один раз в layout. */
export function ToastViewport() {
  const toasts = useToastStore((s) => s.toasts);
  const remove = useToastStore((s) => s.remove);

  return (
    <RadixToast.Provider swipeDirection="right">
      {toasts.slice(-4).map((t) => (
        <ToastItemView key={t.id} item={t} onClose={() => remove(t.id)} />
      ))}
      <RadixToast.Viewport className="fixed bottom-4 right-4 z-[70] m-0 flex w-[calc(100vw-2rem)] flex-col gap-3 list-none outline-none sm:w-[380px]" />
    </RadixToast.Provider>
  );
}

function ToastItemView({ item, onClose }: { item: ToastItem; onClose: () => void }) {
  useEffect(() => {
    if (item.duration <= 0) return;
    const id = window.setTimeout(onClose, item.duration);
    return () => window.clearTimeout(id);
  }, [item.duration, onClose]);

  return (
    <RadixToast.Root
      open
      onOpenChange={(o) => !o && onClose()}
      role={item.variant === "error" ? "alert" : "status"}
      className={cn(
        "bg-bg-2 border rounded-[var(--radius-3)] p-4 flex items-start gap-3",
        "data-[state=open]:animate-in data-[state=open]:slide-in-from-right-4 data-[state=open]:fade-in-0",
        "data-[state=closed]:animate-out data-[state=closed]:slide-out-to-right-4 data-[state=closed]:fade-out-0",
        VARIANT_BORDER[item.variant],
      )}
    >
      <span className="mt-0.5 shrink-0">{ICONS[item.variant]}</span>
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
        className="-my-3 -mr-3 inline-flex size-11 shrink-0 items-center justify-center rounded-[var(--radius-2)] text-bg-9 transition-colors hover:bg-bg-3 hover:text-bg-11 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
      >
        <X size={14} aria-hidden="true" />
      </RadixToast.Close>
    </RadixToast.Root>
  );
}
