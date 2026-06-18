/**
 * HealthPage — экран здоровья воркеров.
 * Шапка (eyebrow СИСТЕМА · ЗДОРОВЬЕ) → вердикт-баннер (HEALTHY/DEGRADED/CRITICAL) →
 * сводка онлайн/total → список WorkerRow → observer_runtime.
 * Маршрут: /health/ (TanStack Router, file-based).
 * BackButton включается автоматически (/^\/health$/ в TelegramBackButton.tsx).
 */
import { createFileRoute } from "@tanstack/react-router";
import { RefreshCw } from "lucide-react";
import { formatRelativeTime } from "@fb/shared";
import { useHealthDetails } from "@/lib/api";
import { haptic } from "@/lib/tg";
import { Eyebrow, PulseDot } from "@/components/data";
import { EmptyState, Skeleton } from "@/components/ui";
import { MiniHeader } from "@/components/layout/MiniHeader";
import { WorkerRow, type WorkerStatus } from "@/components/domain/WorkerRow";
import { cn } from "@/lib/cn";

export const Route = createFileRoute("/health/")({
  component: HealthPage,
});

// ─── Вердикт-маппинги ────────────────────────────────────────────────────────

const VERDICT_COLOR: Record<string, string> = {
  HEALTHY:  "var(--success)",
  DEGRADED: "var(--warning)",
  CRITICAL: "var(--danger)",
};

const VERDICT_BG: Record<string, string> = {
  HEALTHY:  "bg-success-bg border-success",
  DEGRADED: "bg-warning-bg border-warning",
  CRITICAL: "bg-danger-bg border-danger",
};

const VERDICT_LABEL: Record<string, string> = {
  HEALTHY:  "HEALTHY",
  DEGRADED: "DEGRADED",
  CRITICAL: "CRITICAL",
};

// ─── Компонент ───────────────────────────────────────────────────────────────

function HealthPage() {
  const { data, isLoading, isError, error, refetch } = useHealthDetails();

  const overall = data?.overall ?? null;
  const workers: WorkerStatus[] = Array.isArray(data?.workers) ? data.workers : [];
  const runtime = data?.observer_runtime ?? null;

  const onlineCount = workers.filter((w) => w.status === "ONLINE").length;

  const handleRefetch = () => {
    haptic.impact("light");
    void refetch();
  };

  return (
    <div className="flex flex-col">
      {/* ── Шапка ── */}
      <MiniHeader
        eyebrow="СИСТЕМА · ЗДОРОВЬЕ"
        title="Здоровье"
        right={
          <button
            type="button"
            aria-label="Обновить статус"
            onClick={handleRefetch}
            disabled={isLoading}
            className="inline-flex items-center justify-center w-11 h-11 text-bg-9 hover:text-bg-11 active:opacity-70 disabled:opacity-40 transition-opacity"
          >
            <RefreshCw
              size={18}
              strokeWidth={1.8}
              className={isLoading ? "animate-spin" : undefined}
            />
          </button>
        }
      />

      <div className="flex flex-col gap-5 p-4">

        {/* ── Загрузка ── */}
        {isLoading && (
          <>
            <Skeleton className="h-20 w-full rounded-[var(--radius-3)]" />
            <Skeleton className="h-48 w-full rounded-[var(--radius-3)]" />
          </>
        )}

        {/* ── Ошибка ── */}
        {isError && !isLoading && (
          <EmptyState
            title="Ошибка загрузки"
            description={(error as Error | null)?.message ?? "Повторите позже"}
            action={{ label: "Обновить", onClick: handleRefetch }}
          />
        )}

        {/* ── Нет данных ── */}
        {!isLoading && !isError && !data && (
          <EmptyState
            title="Данные недоступны"
            description="Redis heartbeat-ключи не найдены"
            action={{ label: "Обновить", onClick: handleRefetch }}
          />
        )}

        {/* ── Контент ── */}
        {!isLoading && !isError && data && (
          <>
            {/* Вердикт-баннер */}
            {overall && (
              <section
                className={cn(
                  "border px-4 py-4 flex items-center justify-between gap-3 rounded-[var(--radius-3)]",
                  VERDICT_BG[overall] ?? "bg-bg-3 border-[var(--hairline)]",
                )}
              >
                <div className="flex items-center gap-3">
                  <PulseDot
                    size={10}
                    color={VERDICT_COLOR[overall] ?? "var(--bg-9)"}
                  />
                  <p
                    className="font-display font-medium leading-none"
                    style={{
                      fontSize: 26,
                      letterSpacing: "-0.02em",
                      color: VERDICT_COLOR[overall] ?? "var(--bg-11)",
                    }}
                  >
                    {VERDICT_LABEL[overall] ?? overall}
                  </p>
                </div>
                <div className="text-right shrink-0">
                  <p
                    className="font-display tabular-nums text-[22px] font-medium leading-none"
                    style={{ color: VERDICT_COLOR[overall] ?? "var(--bg-11)" }}
                  >
                    {onlineCount}/{workers.length}
                  </p>
                  <p className="font-display text-[10px] uppercase tracking-[0.10em] text-bg-9 mt-1">
                    ONLINE
                  </p>
                </div>
              </section>
            )}

            {/* Список воркеров */}
            <section>
              <Eyebrow className="mb-2.5 flex">ВОРКЕРЫ</Eyebrow>
              <div className="border border-[var(--hairline)] bg-bg-1 px-4 rounded-[var(--radius-3)] overflow-hidden">
                {workers.length === 0 ? (
                  <EmptyState
                    title="Нет данных о воркерах"
                    description="Redis heartbeat-ключи не найдены"
                  />
                ) : (
                  workers.map((w) => <WorkerRow key={w.name} worker={w} />)
                )}
              </div>
            </section>

            {/* Observer runtime */}
            {runtime && (
              <section>
                <Eyebrow className="mb-2.5 flex">OBSERVER RUNTIME</Eyebrow>
                <div className="border border-[var(--hairline)] bg-bg-1 px-4 py-3 flex items-center justify-between gap-3 min-h-[44px] rounded-[var(--radius-3)]">
                  <div>
                    <p className="font-display text-[12px] text-bg-9 uppercase tracking-[0.08em]">
                      Статус
                    </p>
                    <p className="font-display text-[13px] text-bg-11 mt-0.5">
                      {typeof runtime["status"] === "string" ? runtime["status"] : "—"}
                    </p>
                  </div>
                  {typeof runtime["updated_at"] === "string" && (
                    <p className="font-display tabular-nums text-[12px] text-bg-9 shrink-0">
                      {formatRelativeTime(runtime["updated_at"])} назад
                    </p>
                  )}
                </div>
              </section>
            )}

            {/* Кнопка обновить (внизу) */}
            <button
              type="button"
              onClick={handleRefetch}
              disabled={isLoading}
              className="w-full min-h-[44px] border border-[var(--hairline)] rounded-[var(--radius-2)] text-bg-10 text-[13px] font-display hover:border-[var(--hairline-strong)] hover:text-bg-11 active:opacity-70 disabled:opacity-40 transition-opacity"
            >
              Обновить статус
            </button>
          </>
        )}
      </div>
    </div>
  );
}
