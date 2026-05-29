/**
 * WorkerPulse — health-индикатор для TopBar / Sidebar.
 * Дышит (pulse-dot), цвет по health.
 */

import { useHealthDetails } from "@/lib/api/settings";
import { Tooltip } from "../ui/Tooltip";

type Variant = "success" | "warning" | "danger" | "muted";

const DOT_BG: Record<Variant, string> = {
  success: "bg-success",
  warning: "bg-warning",
  danger: "bg-danger",
  muted: "bg-bg-8",
};

export function WorkerPulse() {
  const { data, isError } = useHealthDetails();

  const variant: Variant = isError
    ? "muted"
    : data?.overall === "HEALTHY"
      ? "success"
      : data?.overall === "DEGRADED"
        ? "warning"
        : data?.overall === "CRITICAL"
          ? "danger"
          : "muted";

  const total = data?.workers.length ?? 0;
  const online = data?.workers.filter((w) => w.status === "ONLINE").length ?? 0;

  const label = data ? `${online}/${total} воркеров` : "воркеры";
  const tooltipContent = data ? (
    <div className="space-y-1">
      {data.workers.map((w) => (
        <div key={w.name} className="flex items-center gap-2">
          <span
            aria-hidden="true"
            className={`size-1.5 rounded-full inline-block ${
              w.status === "ONLINE" ? "bg-success" : "bg-danger"
            }`}
          />
          <span className="text-[11px]">{w.name}</span>
        </div>
      ))}
    </div>
  ) : (
    <span>Здоровье воркеров недоступно</span>
  );

  return (
    <Tooltip content={tooltipContent} side="bottom">
      <div className="flex items-center gap-2 text-[12px] text-bg-10 font-display tracking-wider cursor-default">
        <span aria-hidden="true" className={`size-2 rounded-full pulse-dot ${DOT_BG[variant]}`} />
        <span>{label}</span>
      </div>
    </Tooltip>
  );
}
