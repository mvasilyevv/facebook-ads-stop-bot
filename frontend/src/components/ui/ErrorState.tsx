/**
 * ErrorState — ошибка внутри карточки или page-level.
 * danger icon (mono) + message + Retry.
 */
import { AlertOctagon } from "lucide-react";
import { type ReactNode } from "react";
import { cn } from "@/lib/utils/cn";
import { Button } from "./Button";

interface ErrorStateProps {
  title?: ReactNode;
  /**
   * Готовая операторская копия. Санитайзит вызывающий (safeApiProblemMessage):
   * презентационный компонент не должен зависеть от API-слоя и не умеет
   * разбирать ApiProblem.
   */
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
  // Печатаем ТОЛЬКО строку. Любой объект (Error, traceback, ApiProblem,
  // произвольный payload) сюда попасть не должен, а если попал — показываем
  // нейтральный текст вместо дампа: заголовок уже сказал, что сломалось.
  const message =
    typeof error === "string" && error.trim()
      ? error
      : error
        ? "Подробности недоступны. Повторите попытку."
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
        {/* Обычный текст, а не моноширинный дамп: сюда попадает операторская
            копия, сырые сообщения отфильтрованы выше. break-words вместо
            break-all — иначе строка рвётся посреди слова на узком экране. */}
        {message ? (
          <div className="text-[13px] text-bg-10 font-body break-words">
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
