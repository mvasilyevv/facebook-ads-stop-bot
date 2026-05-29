/**
 * ErrorState — компонент для отрисовки ошибки внутри карточки или page-level.
 * Содержит кнопку Retry.
 */

import { AlertOctagon } from "lucide-react";
import { type ReactNode } from "react";
import { Button } from "./Button";
import { cn } from "@/lib/utils/cn";

interface ErrorStateProps {
  title?: ReactNode;
  /** Сообщение ошибки или объект Error. */
  error?: unknown;
  onRetry?: () => void;
  className?: string;
}

export function ErrorState({
  title = "Что-то пошло не так.",
  error,
  onRetry,
  className,
}: ErrorStateProps) {
  const message =
    error instanceof Error
      ? error.message
      : typeof error === "string"
        ? error
        : error
          ? JSON.stringify(error)
          : null;

  return (
    <div
      role="alert"
      className={cn(
        "border border-[rgba(199,98,92,0.3)] bg-danger-bg/60 p-6 flex items-start gap-4",
        className,
      )}
    >
      <span className="text-danger mt-0.5" aria-hidden="true">
        <AlertOctagon size={20} />
      </span>
      <div className="flex-1">
        <div className="text-bg-11 font-display text-[14px] mb-1">{title}</div>
        {message ? (
          <div className="text-[12px] text-bg-10 font-numeric break-all">
            {String(message).slice(0, 400)}
          </div>
        ) : null}
        {onRetry ? (
          <div className="mt-4">
            <Button size="sm" variant="secondary" onClick={onRetry}>
              Повторить
            </Button>
          </div>
        ) : null}
      </div>
    </div>
  );
}
