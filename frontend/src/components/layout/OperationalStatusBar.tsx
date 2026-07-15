import { Link } from "@tanstack/react-router";
import { AlertTriangle, CircleHelp, Play, ServerOff } from "lucide-react";

import type { MonitoringState } from "@/components/dashboard/monitoringState";
import { Button } from "@/components/ui/Button";
import { toast } from "@/components/ui/Toast";
import { ApiError } from "@/lib/api/client";
import { useToggleScanning } from "@/lib/api/settings";
import { useMonitoringSnapshot } from "@/lib/hooks/useMonitoringSnapshot";
import { formatRelativeTime } from "@fb/shared";

const VIEW: Record<
  Exclude<MonitoringState, "healthy">,
  { title: string; Icon: typeof AlertTriangle; color: string }
> = {
  paused: {
    title: "Мониторинг на паузе",
    Icon: Play,
    color: "var(--warning)",
  },
  degraded: {
    title: "Контур работает частично",
    Icon: AlertTriangle,
    color: "var(--warning)",
  },
  offline: {
    title: "Мониторинг недоступен",
    Icon: ServerOff,
    color: "var(--danger)",
  },
  unknown: {
    title: "Ждём данные мониторинга",
    Icon: CircleHelp,
    color: "var(--info)",
  },
};

export function OperationalStatusBar() {
  const snapshot = useMonitoringSnapshot();
  const toggleScanning = useToggleScanning();

  if (snapshot.state === "healthy") return null;

  const view = VIEW[snapshot.state];
  const offlineCount = Math.max(snapshot.workersExpected - snapshot.workersOnline, 0);
  const allWorkersOnline = snapshot.workersExpected > 0 && offlineCount === 0;
  const metaChannelDegraded = snapshot.health?.meta_api_channel?.status === "DEGRADED";
  const title =
    snapshot.state === "degraded" && allWorkersOnline && metaChannelDegraded
      ? "Воркеры запущены, канал Meta API недоступен"
      : view.title;
  const offlineWorkersText =
    offlineCount === 1
      ? "1 воркер не передаёт heartbeat"
      : offlineCount > 1
        ? `${offlineCount} ${workerWord(offlineCount)} не передают heartbeat`
        : "Критические компоненты не передают heartbeat";
  const lastScan = snapshot.lastScanAt
    ? `Последний подтверждённый скан ${formatRelativeTime(snapshot.lastScanAt)}.`
    : "Подтверждённых сканов пока нет.";

  const description =
    snapshot.state === "paused"
      ? `Сканирование выключено. История доступна, live-метрики не обновляются. ${lastScan}`
      : snapshot.state === "offline"
        ? `${offlineWorkersText}. Live-метрики и автоотключение нельзя считать активными. ${lastScan}`
        : snapshot.state === "degraded"
          ? allWorkersOnline && metaChannelDegraded
            ? `Все процессы передают heartbeat, но сквозная проверка канала Meta API не проходит. ${lastScan}`
            : `${offlineWorkersText}. Часть live-контура недоступна. ${lastScan}`
          : allWorkersOnline
            ? `Все процессы запущены, observer ещё не опубликовал runtime-статус. Обычно это занимает один цикл сканирования. ${lastScan}`
            : `Runtime-статус observer пока не получен. ${lastScan}`;

  const handleEnable = () => {
    toggleScanning.mutate(true, {
      onSuccess: () => toast.success("Мониторинг включён"),
      onError: (error) => {
        const reason =
          error instanceof ApiError && typeof error.detail === "string"
            ? error.detail
            : error instanceof Error
              ? error.message
              : "Не удалось включить мониторинг";
        toast.error("Мониторинг не включён", reason);
      },
    });
  };

  return (
    <section
      aria-label="Состояние мониторинга"
      role={snapshot.state === "offline" ? "alert" : "status"}
      className="mb-5 flex flex-col gap-3 rounded-[var(--radius-3)] border border-[var(--hairline-strong)] bg-bg-1 px-4 py-3 sm:flex-row sm:items-center"
      style={{ borderLeftWidth: 2, borderLeftColor: view.color }}
    >
      <span
        className="flex size-9 shrink-0 items-center justify-center rounded-[var(--radius-2)] bg-bg-3"
        style={{ color: view.color }}
      >
        <view.Icon size={18} strokeWidth={1.7} aria-hidden="true" />
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
          <strong className="font-display text-[13px] font-medium text-bg-11">
            {title}
          </strong>
          {snapshot.workersExpected > 0 ? (
            <span className="font-display text-[11px] tabular-nums" style={{ color: view.color }}>
              {snapshot.workersOnline}/{snapshot.workersExpected} процессов онлайн
            </span>
          ) : null}
        </div>
        <p className="mt-0.5 max-w-4xl text-[12px] leading-[1.5] text-bg-10">
          {description}
        </p>
      </div>
      <div className="flex shrink-0 flex-wrap items-center gap-2">
        {snapshot.state === "paused" ? (
          <Button
            variant="primary"
            size="sm"
            leftIcon={<Play size={14} aria-hidden="true" />}
            loading={toggleScanning.isPending}
            onClick={handleEnable}
          >
            Включить мониторинг
          </Button>
        ) : null}
        <Link
          to="/settings"
          search={{ tab: "health" }}
          className="inline-flex h-7 items-center justify-center rounded-[var(--radius-2)] border border-[var(--hairline-strong)] bg-bg-2 px-3 text-[12.5px] font-medium text-bg-11 transition-colors hover:border-bg-7 hover:bg-bg-3 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
        >
          Диагностика
        </Link>
      </div>
    </section>
  );
}

function workerWord(value: number): string {
  const mod10 = value % 10;
  const mod100 = value % 100;
  return mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)
    ? "воркера"
    : "воркеров";
}
