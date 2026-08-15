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

export function OperatorUnavailableState({
  title,
  resource,
  details,
  onRetry,
}: {
  title: string;
  resource: string;
  details?: string;
  onRetry?: () => void;
}) {
  const detail = details?.trim();
  const guidance =
    detail && detail !== title
      ? `${detail} Повторите запрос.`
      : `Не удалось загрузить ${resource}. Повторите запрос.`;

  return <ErrorState title={title} error={guidance} onRetry={onRetry} />;
}
