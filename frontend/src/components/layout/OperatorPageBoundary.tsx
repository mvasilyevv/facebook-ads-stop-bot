import { type ReactNode } from "react";

import { PageHeader } from "@/components/layout/PageHeader";
import { ErrorState } from "@/components/ui/ErrorState";
import { Skeleton } from "@/components/ui/Skeleton";
import { cn } from "@/lib/utils/cn";

interface OperatorPageBoundaryProps {
  /** Уточнение к разделу; сам раздел и его номер задаёт маршрут. */
  detail?: string;
  title: string;
  subtitle?: ReactNode;
  navigation?: ReactNode;
  children: ReactNode;
  className?: string;
}

/** Единая шапка для состояний, в которых данных ещё нет или они недоступны. */
export function OperatorPageBoundary({
  detail,
  title,
  subtitle,
  navigation,
  children,
  className,
}: OperatorPageBoundaryProps) {
  return (
    <div className={cn("min-w-0", className)}>
      {navigation ? <div className="mb-5">{navigation}</div> : null}
      <PageHeader detail={detail} title={title} subtitle={subtitle} />
      {children}
    </div>
  );
}

export function OperatorListSkeleton({ label }: { label: string }) {
  return (
    <div role="status" aria-label={label} aria-busy="true" className="grid gap-3">
      {Array.from({ length: 6 }, (_, index) => (
        <Skeleton key={index} variant="row" className="min-h-16 w-full" />
      ))}
    </div>
  );
}

export function OperatorCardSkeleton({ label }: { label: string }) {
  return (
    <div role="status" aria-label={label} aria-busy="true" className="grid gap-4">
      <Skeleton className="h-8 w-2/3" />
      <Skeleton className="h-16 w-1/2" />
      <Skeleton className="h-56 w-full" />
    </div>
  );
}

/**
 * Строка похожа на сырой дамп исключения или служебных данных (JSON, react-query
 * queryKey, stack trace), а не на заранее написанный операторский текст.
 * `details` обязан быть готовой копией (см. safeApiProblemMessage) — эта проверка
 * защищает от регресса, если кто-то по ошибке прокинет error.message напрямую.
 */
function looksLikeRawDiagnostic(text: string): boolean {
  return /[[\]{}]/.test(text) || /\.(?:ts|tsx|js|jsx|mjs|cjs):\d+(?::\d+)?/.test(text);
}

export function OperatorUnavailableState({
  title,
  resource,
  details,
  onRetry,
}: {
  title: string;
  resource: string;
  /** Заранее написанный операторский текст (см. safeApiProblemMessage). Никогда не error.message. */
  details?: string;
  onRetry?: () => void;
}) {
  const detail = details?.trim();
  const safeDetail = detail && !looksLikeRawDiagnostic(detail) ? detail : undefined;
  const guidance =
    safeDetail && safeDetail !== title
      ? `${safeDetail} Повторите запрос.`
      : `Не удалось загрузить ${resource}. Повторите запрос.`;

  return <ErrorState title={title} error={guidance} onRetry={onRetry} />;
}
