/**
 * ObserverTab — вкладка настроек Observer:
 *   - Тоггл сканирования, интервал, auto_enable_recommendations.
 *   - Кнопка "Scan now".
 *   - Статус observer из Redis (running/paused/unknown).
 *   - Таблица последних scan-runs с filter-selector.
 */

import { useState, type ChangeEvent } from "react";
import { type ColumnDef } from "@tanstack/react-table";
import { RefreshCcw, Play } from "lucide-react";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Skeleton } from "@/components/ui/Skeleton";
import { ErrorState } from "@/components/ui/ErrorState";
import { Select } from "@/components/ui/Select";
import { Table } from "@/components/data/Table";
import { toast } from "@/components/ui/Toast";
import { formatRelativeTime, formatDateTime, formatDuration } from "@/lib/utils/format";
import type { ScanRun } from "@/lib/types/api";

import {
  useObserverSettings,
  useObserverStatus,
  useScanRuns,
  useUpdateObserver,
  useToggleScanning,
  useToggleAutoEnable,
  useTriggerScanNowSettings,
} from "@/lib/api/settings";

// Колонки таблицы scan-runs.
const SCAN_COLUMNS: ColumnDef<ScanRun, unknown>[] = [
  {
    id: "started_at",
    header: "Started",
    accessorKey: "started_at",
    cell: ({ getValue }) => (
      <span className="font-numeric text-[12px]">{formatDateTime(getValue() as string)}</span>
    ),
  },
  {
    id: "outcome",
    header: "Outcome",
    accessorKey: "outcome",
    cell: ({ getValue }) => {
      const v = getValue() as string;
      const variant = v === "ok" ? "success" : v === "error" ? "stop" : "neutral";
      return <Badge variant={variant} size="sm">{v}</Badge>;
    },
  },
  {
    id: "ads_seen",
    header: "Ads",
    accessorKey: "ads_seen",
    cell: ({ getValue }) => (
      <span className="font-numeric text-[13px]">{getValue() as number}</span>
    ),
  },
  {
    id: "alerts_created",
    header: "Alerts",
    accessorKey: "alerts_created",
    cell: ({ getValue }) => {
      const n = getValue() as number;
      return (
        <span className={`font-numeric text-[13px] ${n > 0 ? "text-warning" : ""}`}>{n}</span>
      );
    },
  },
  {
    id: "errors_count",
    header: "Errors",
    accessorKey: "errors_count",
    cell: ({ getValue }) => {
      const n = getValue() as number;
      return (
        <span className={`font-numeric text-[13px] ${n > 0 ? "text-danger" : ""}`}>{n}</span>
      );
    },
  },
  {
    id: "duration_ms",
    header: "Duration",
    accessorKey: "duration_ms",
    cell: ({ getValue }) => {
      const ms = getValue() as number | null;
      return (
        <span className="font-numeric text-[12px] text-bg-10">
          {ms != null ? formatDuration(ms / 1000) : "—"}
        </span>
      );
    },
  },
];

type ScanFilter = "all" | "errors" | "slow" | "with_alerts";

const FILTER_OPTIONS: { value: ScanFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "errors", label: "Errors only" },
  { value: "slow", label: "Slow only" },
  { value: "with_alerts", label: "With alerts" },
];

export function ObserverTab() {
  const [scanFilter, setScanFilter] = useState<ScanFilter>("all");
  // Локальное состояние для редактирования интервала.
  const [intervalDraft, setIntervalDraft] = useState<string>("");
  const [editingInterval, setEditingInterval] = useState(false);

  const settingsQuery = useObserverSettings();
  const statusQuery = useObserverStatus();
  const scanRunsQuery = useScanRuns(50, scanFilter);

  const updateObserver = useUpdateObserver();
  const toggleScanning = useToggleScanning();
  const toggleAutoEnable = useToggleAutoEnable();
  const scanNow = useTriggerScanNowSettings();

  const settings = settingsQuery.data;
  const status = statusQuery.data;

  /** Обработчик тоггла is_scanning. */
  function handleToggleScanning() {
    if (!settings) return;
    toggleScanning.mutate(!settings.is_scanning, {
      onSuccess: () =>
        toast.success(
          settings.is_scanning ? "Сканирование приостановлено" : "Сканирование запущено",
        ),
      onError: (err) =>
        toast.error("Ошибка", err instanceof Error ? err.message : String(err)),
    });
  }

  /** Обработчик тоггла auto_enable_recommendations. */
  function handleToggleAutoEnable() {
    if (!settings) return;
    toggleAutoEnable.mutate(!settings.auto_enable_recommendations_enabled, {
      onSuccess: () => toast.success("Настройка сохранена"),
      onError: (err) =>
        toast.error("Ошибка", err instanceof Error ? err.message : String(err)),
    });
  }

  /** Сохранить новый интервал скана. */
  function handleSaveInterval() {
    const n = parseInt(intervalDraft, 10);
    if (Number.isNaN(n) || n < 5 || n > 3600) {
      toast.error("Некорректный интервал", "Допустимо 5–3600 секунд.");
      return;
    }
    updateObserver.mutate(
      { scan_interval_seconds: n },
      {
        onSuccess: () => {
          setEditingInterval(false);
          toast.success("Интервал сохранён");
        },
        onError: (err) =>
          toast.error("Ошибка", err instanceof Error ? err.message : String(err)),
      },
    );
  }

  function handleStartEditInterval() {
    setIntervalDraft(String(settings?.scan_interval_seconds ?? ""));
    setEditingInterval(true);
  }

  /** Запустить скан немедленно. */
  function handleScanNow() {
    scanNow.mutate(undefined, {
      onSuccess: () => toast.success("Scan triggered", "Observer запустит цикл сканирования."),
      onError: (err) =>
        toast.error("Не удалось запустить scan", err instanceof Error ? err.message : String(err)),
    });
  }

  if (settingsQuery.isError) {
    return (
      <ErrorState
        title="Не удалось загрузить настройки observer."
        error={settingsQuery.error}
        onRetry={() => settingsQuery.refetch()}
      />
    );
  }

  return (
    <div className="grid grid-cols-[1fr_320px] gap-8">
      {/* Левая колонка: форма настроек. */}
      <div className="space-y-6">
        <section>
          <h3 className="font-display text-[10px] uppercase tracking-widest text-bg-9 mb-4">
            Настройки сканирования
          </h3>

          {/* Toggle: сканирование включено/выключено. */}
          <div className="flex items-center justify-between py-3 border-b border-bg-5">
            <div>
              <div className="text-[13px] text-bg-11 font-medium">Сканирование</div>
              <div className="text-[11px] text-bg-9 mt-0.5">
                Включить/выключить автоматический цикл сканирования.
              </div>
            </div>
            {settingsQuery.isLoading ? (
              <Skeleton width={48} height={24} />
            ) : (
              <button
                type="button"
                role="switch"
                aria-checked={settings?.is_scanning ?? false}
                aria-label="Сканирование"
                onClick={handleToggleScanning}
                disabled={toggleScanning.isPending}
                className={[
                  "relative inline-flex w-12 h-6 border transition-colors",
                  "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent",
                  settings?.is_scanning
                    ? "bg-success border-[rgba(126,180,122,0.5)]"
                    : "bg-bg-3 border-bg-6",
                  "disabled:opacity-40 disabled:cursor-not-allowed",
                ].join(" ")}
              >
                <span
                  aria-hidden="true"
                  className={[
                    "absolute top-[3px] size-[18px] bg-bg-11 transition-transform",
                    settings?.is_scanning ? "translate-x-[26px]" : "translate-x-[3px]",
                  ].join(" ")}
                />
              </button>
            )}
          </div>

          {/* Интервал скана. */}
          <div className="flex items-center justify-between py-3 border-b border-bg-5">
            <div>
              <div className="text-[13px] text-bg-11 font-medium">Интервал скана</div>
              <div className="text-[11px] text-bg-9 mt-0.5">Секунды между сканами.</div>
            </div>
            {settingsQuery.isLoading ? (
              <Skeleton width={100} height={28} />
            ) : editingInterval ? (
              <div className="flex items-center gap-2">
                <Input
                  size="sm"
                  type="number"
                  value={intervalDraft}
                  onChange={(e: ChangeEvent<HTMLInputElement>) => setIntervalDraft(e.target.value)}
                  className="w-20"
                  aria-label="Интервал в секундах"
                />
                <Button size="sm" variant="primary" loading={updateObserver.isPending} onClick={handleSaveInterval}>
                  OK
                </Button>
                <Button size="sm" variant="ghost" onClick={() => setEditingInterval(false)}>
                  ×
                </Button>
              </div>
            ) : (
              <button
                type="button"
                onClick={handleStartEditInterval}
                className="font-numeric text-[14px] text-accent hover:text-accent-muted transition-colors"
              >
                {settings?.scan_interval_seconds ?? "—"}s
              </button>
            )}
          </div>

          {/* Toggle: auto-enable recommendations. */}
          <div className="flex items-center justify-between py-3">
            <div>
              <div className="text-[13px] text-bg-11 font-medium">Auto-enable recommendations</div>
              <div className="text-[11px] text-bg-9 mt-0.5">
                Автоматически предлагать включение восстановившихся объявлений.
              </div>
            </div>
            {settingsQuery.isLoading ? (
              <Skeleton width={48} height={24} />
            ) : (
              <button
                type="button"
                role="switch"
                aria-checked={settings?.auto_enable_recommendations_enabled ?? false}
                aria-label="Auto-enable recommendations"
                onClick={handleToggleAutoEnable}
                disabled={toggleAutoEnable.isPending}
                className={[
                  "relative inline-flex w-12 h-6 border transition-colors",
                  "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent",
                  settings?.auto_enable_recommendations_enabled
                    ? "bg-success border-[rgba(126,180,122,0.5)]"
                    : "bg-bg-3 border-bg-6",
                  "disabled:opacity-40 disabled:cursor-not-allowed",
                ].join(" ")}
              >
                <span
                  aria-hidden="true"
                  className={[
                    "absolute top-[3px] size-[18px] bg-bg-11 transition-transform",
                    settings?.auto_enable_recommendations_enabled
                      ? "translate-x-[26px]"
                      : "translate-x-[3px]",
                  ].join(" ")}
                />
              </button>
            )}
          </div>
        </section>

        {/* Таблица scan-runs. */}
        <section>
          <div className="flex items-center justify-between mb-3">
            <h3 className="font-display text-[10px] uppercase tracking-widest text-bg-9">
              Последние сканы
            </h3>
            <Select
              value={scanFilter}
              onChange={(e) => setScanFilter(e.target.value as ScanFilter)}
              options={FILTER_OPTIONS}
              size="sm"
              className="w-36"
            />
          </div>

          {scanRunsQuery.isError ? (
            <ErrorState
              title="Не удалось загрузить scan-runs."
              error={scanRunsQuery.error}
              onRetry={() => scanRunsQuery.refetch()}
            />
          ) : (
            <Table
              data={scanRunsQuery.data ?? []}
              columns={SCAN_COLUMNS}
              virtualized={false}
              loading={scanRunsQuery.isLoading}
              getRowKey={(row) => row.id}
              emptyState={
                <span className="text-bg-9 text-[13px]">
                  Нет данных для выбранного фильтра.
                </span>
              }
            />
          )}
        </section>
      </div>

      {/* Правая колонка: статус + действия. */}
      <div className="space-y-6">
        <section className="border border-bg-5 bg-bg-1 p-5">
          <h3 className="font-display text-[10px] uppercase tracking-widest text-bg-9 mb-4">
            Статус
          </h3>

          {statusQuery.isLoading ? (
            <div className="space-y-3">
              <Skeleton height={18} />
              <Skeleton height={14} width="70%" />
            </div>
          ) : statusQuery.isError ? (
            <ErrorState
              title="Статус недоступен."
              error={statusQuery.error}
              onRetry={() => statusQuery.refetch()}
            />
          ) : (
            <div className="space-y-3">
              <div className="flex items-center gap-2">
                <Badge
                  variant={
                    status?.status === "running"
                      ? "success"
                      : status?.status === "paused"
                        ? "warning"
                        : "neutral"
                  }
                >
                  {status?.status ?? "unknown"}
                </Badge>
              </div>
              <div className="text-[12px] text-bg-9">
                Последний скан:{" "}
                <span className="text-bg-11 font-numeric">
                  {formatRelativeTime(status?.last_cycle_at)}
                </span>
              </div>
              {status?.active_country ? (
                <div className="text-[12px] text-bg-9">
                  Страна:{" "}
                  <span className="text-bg-11 font-numeric">{status.active_country}</span>
                </div>
              ) : null}
              <div className="text-[12px] text-bg-9">
                Сканов сегодня:{" "}
                <span className="text-bg-11 font-numeric">{status?.cycle_count_today ?? "—"}</span>
              </div>
            </div>
          )}
        </section>

        <section className="border border-bg-5 bg-bg-1 p-5 space-y-3">
          <h3 className="font-display text-[10px] uppercase tracking-widest text-bg-9 mb-4">
            Действия
          </h3>
          <Button
            variant="secondary"
            size="sm"
            fullWidth
            leftIcon={<Play size={13} aria-hidden="true" />}
            loading={scanNow.isPending}
            onClick={handleScanNow}
          >
            Scan now
          </Button>
          <Button
            variant="secondary"
            size="sm"
            fullWidth
            leftIcon={<RefreshCcw size={13} aria-hidden="true" />}
            disabled
          >
            Start new cabinet day
          </Button>
        </section>
      </div>
    </div>
  );
}
