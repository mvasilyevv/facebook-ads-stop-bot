/**
 * HealthPage — статусы воркеров и общий вердикт системы.
 * Маршрут: /health/ (TanStack Router, file-based).
 *
 * API: useHealthDetails() из @/lib/api.
 * BackButton включается автоматически (/^\/health$/ в TelegramBackButton.tsx).
 * TabBar — НЕ скрывается (Health открывается как самостоятельная вкладка
 * через Settings → Health или прямую ссылку).
 */
import { createFileRoute } from "@tanstack/react-router";
import { useHealthDetails } from "@/lib/api";
import { Card, EmptyState, ErrorState, Skeleton } from "@/components/ui";
import { WorkerRow, type WorkerStatus } from "@/components/domain/WorkerRow";
import { MiniHeader } from "@/components/layout/MiniHeader";
import { cn } from "@/lib/cn";

export const Route = createFileRoute("/health/")({
  component: HealthPage,
});

// Вердикт → стиль
const VERDICT_STYLES: Record<string, string> = {
  HEALTHY:  "text-[var(--color-success)]",
  DEGRADED: "text-[var(--color-warning)]",
  CRITICAL: "text-[var(--color-danger)]",
};

const VERDICT_BG: Record<string, string> = {
  HEALTHY:  "bg-[var(--color-success-bg)] border-[var(--color-success)]",
  DEGRADED: "bg-[var(--color-warning-bg)] border-[var(--color-warning)]",
  CRITICAL: "bg-[var(--color-danger-bg)]  border-[var(--color-danger)]",
};

const VERDICT_LABELS: Record<string, string> = {
  HEALTHY:  "Всё в норме",
  DEGRADED: "Деградация",
  CRITICAL: "Критично",
};

function HealthPage() {
  const { data, isLoading, isError, error, refetch } = useHealthDetails();

  const overall = data?.overall ?? null;
  const workers = Array.isArray(data?.workers) ? data.workers : [];

  const onlineCount = workers.filter((w: WorkerStatus) => w.status === "ONLINE").length;
  const offlineCount = workers.length - onlineCount;

  return (
    <div>
      <MiniHeader
        eyebrow="Мониторинг"
        title="Статус воркеров"
        right={
          !isLoading ? (
            <button
              type="button"
              onClick={() => void refetch()}
              className="text-[11px] font-mono text-[var(--color-bg-9)] hover:text-[var(--color-bg-11)] transition-colors"
              aria-label="Обновить"
            >
              Обновить
            </button>
          ) : undefined
        }
      />

      <div className="px-4 pt-4 pb-6 flex flex-col gap-4">

        {/* Загрузка */}
        {isLoading && (
          <>
            <Skeleton className="h-16 w-full" />
            <Skeleton className="h-40 w-full" />
          </>
        )}

        {/* Ошибка */}
        {isError && !isLoading && (
          <ErrorState
            message={(error as Error | null)?.message ?? "Не удалось получить данные"}
            onRetry={() => void refetch()}
          />
        )}

        {/* Контент */}
        {!isLoading && !isError && data && (
          <>
            {/* Общий вердикт */}
            {overall && (
              <div
                className={cn(
                  "border px-4 py-3 flex items-center justify-between gap-3",
                  VERDICT_BG[overall] ?? "bg-[var(--color-bg-3)] border-[var(--color-bg-5)]",
                )}
              >
                <div>
                  <p className="text-[10px] uppercase tracking-[0.08em] font-mono text-[var(--color-bg-9)] mb-0.5">
                    Общий статус
                  </p>
                  <p
                    className={cn(
                      "text-[24px] font-mono font-bold leading-none",
                      VERDICT_STYLES[overall] ?? "text-[var(--color-bg-11)]",
                    )}
                  >
                    {VERDICT_LABELS[overall] ?? overall}
                  </p>
                </div>
                <div className="text-right">
                  <p className="text-[11px] font-mono text-[var(--color-bg-9)]">
                    {onlineCount}/{workers.length} online
                  </p>
                  {offlineCount > 0 && (
                    <p className="text-[11px] font-mono text-[var(--color-danger)]">
                      {offlineCount} офлайн
                    </p>
                  )}
                </div>
              </div>
            )}

            {/* Список воркеров */}
            {workers.length > 0 ? (
              <Card eyebrow="Воркеры" padding="sm">
                {workers.map((w) => (
                  <WorkerRow key={w.name} worker={w} />
                ))}
              </Card>
            ) : (
              <EmptyState
                title="Нет данных о воркерах"
                description="Redis heartbeat-ключи не найдены"
              />
            )}
          </>
        )}

        {/* Если нет данных и нет ошибки */}
        {!isLoading && !isError && !data && (
          <EmptyState
            title="Данные недоступны"
            description="Повторите попытку"
            action={{ label: "Обновить", onClick: () => void refetch() }}
          />
        )}
      </div>
    </div>
  );
}
