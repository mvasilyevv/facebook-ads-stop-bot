/**
 * ErrorState — ошибка внутри карточки или page-level.
 * danger icon (mono) + message + Retry.
 */
import { AlertOctagon } from "lucide-react";
import { type ReactNode } from "react";
import { safeApiProblemMessage } from "@fb/operator-api";
import { cn } from "@/lib/utils/cn";
import { Button } from "./Button";

interface ErrorStateProps {
  title?: ReactNode;
  /** Сообщение или объект Error. */
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
  // Строку callers уже подготовили как operator copy. Любой другой объект
  // (Error, traceback, ApiProblem, произвольный payload) проходит через
  // safeApiProblemMessage: наружу выходит только recovery-текст, без сырого
  // exception, JSON-дампа и correlation_id.
  const message =
    typeof error === "string"
      ? error
      : error
        ? safeApiProblemMessage(error, "Подробности недоступны. Повторите попытку.")
        : null;

  return (
    <div
      role="alert"
      className={cn(
        "border border-danger/30 bg-danger-bg/60 rounded-[var(--radius-3)] p-6 flex items-start gap-4",
        className,
      )}
    >
      <span className="text-danger mt-0.5 shrink-0" aria-hidden="true">
        <AlertOctagon size={20} />
      </span>
      <div className="flex-1 min-w-0">
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
