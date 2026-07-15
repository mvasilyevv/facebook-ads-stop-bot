/**
 * EmptyState — editorial-empty: тонкая иконка + текст + optional CTA.
 * Без иллюстраций. Всегда центрированный.
 */
import { type ReactNode } from "react";
import { cn } from "@/lib/utils/cn";

interface EmptyStateProps {
  icon?: ReactNode;
  title: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
  className?: string;
}

export function EmptyState({
  icon,
  title,
  description,
  action,
  className,
}: EmptyStateProps) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center text-center py-12 px-4",
        className,
      )}
    >
      {icon ? (
        <div aria-hidden="true" className="text-bg-8 mb-6">
          {icon}
        </div>
      ) : null}
      <div className="text-bg-11 font-display text-[15px] mb-1.5">{title}</div>
      {description ? (
        <div className="text-bg-10 text-[13px] max-w-[360px]">{description}</div>
      ) : null}
      {action ? <div className="mt-6">{action}</div> : null}
    </div>
  );
}
