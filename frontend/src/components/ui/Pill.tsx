/**
 * Pill — pill-form badge для фильтров и tag'ов. radius-full.
 */

import { type HTMLAttributes, type ReactNode } from "react";
import { cn } from "@/lib/utils/cn";
import { X } from "lucide-react";

interface PillProps extends HTMLAttributes<HTMLSpanElement> {
  leftIcon?: ReactNode;
  /** true → отображается × и pill становится removable. */
  removable?: boolean;
  onRemove?: () => void;
  /** Активный (accent-обводка). */
  active?: boolean;
}

export function Pill({
  children,
  leftIcon,
  removable,
  onRemove,
  active,
  className,
  ...rest
}: PillProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 h-7 px-3.5",
        "rounded-full border bg-bg-1 text-bg-10",
        "font-display text-[11.5px] uppercase tracking-wider",
        "transition-colors",
        active ? "border-accent text-accent bg-accent-bg" : "border-bg-6 hover:border-bg-7",
        className,
      )}
      {...rest}
    >
      {leftIcon ? <span aria-hidden="true">{leftIcon}</span> : null}
      {children}
      {removable ? (
        <button
          type="button"
          aria-label="Удалить фильтр"
          onClick={(e) => {
            e.stopPropagation();
            onRemove?.();
          }}
          className="ml-0.5 -mr-1 size-4 inline-flex items-center justify-center rounded-full hover:bg-bg-3 transition-colors"
        >
          <X aria-hidden="true" size={11} />
        </button>
      ) : null}
    </span>
  );
}
