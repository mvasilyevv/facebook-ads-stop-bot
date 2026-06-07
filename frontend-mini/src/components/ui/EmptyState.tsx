/**
 * EmptyState — пустое состояние для списков.
 * EmptyState + ErrorState в одном файле (компактно).
 */
import type { ReactNode } from "react";
import { Button } from "./Button";

interface EmptyStateProps {
  icon?: ReactNode;
  title: string;
  description?: string;
  action?: {
    label: string;
    onClick: () => void;
  };
}

export function EmptyState({ icon, title, description, action }: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 py-10 text-center">
      {icon && (
        <div className="text-[32px] text-[var(--color-bg-8)] select-none" aria-hidden>
          {icon}
        </div>
      )}
      <div>
        <p className="text-[14px] font-medium text-[var(--color-bg-10)]">{title}</p>
        {description && (
          <p className="text-[13px] text-[var(--color-bg-9)] mt-1">{description}</p>
        )}
      </div>
      {action && (
        <Button variant="secondary" size="sm" onClick={action.onClick}>
          {action.label}
        </Button>
      )}
    </div>
  );
}

interface ErrorStateProps {
  message?: string;
  onRetry?: () => void;
}

export function ErrorState({ message, onRetry }: ErrorStateProps) {
  return (
    <div className="flex flex-col items-center gap-3 py-8 text-center">
      <p className="text-[13px] text-[var(--color-danger)]">
        {message ?? "Произошла ошибка"}
      </p>
      {onRetry && (
        <Button variant="secondary" size="sm" onClick={onRetry}>
          Повторить
        </Button>
      )}
    </div>
  );
}
