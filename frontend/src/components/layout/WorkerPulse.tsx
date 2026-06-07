/**
 * WorkerPulse — health-индикатор для TopBar / Sidebar.
 * Пульсирующая точка + "N/M воркеров", цвет по overall health.
 * Данные из GET /health/details (TanStack Query).
 *
 * Переиспользуемый компонент: встречается в TopBar и может быть в Sidebar footer.
 */

import { type CSSProperties } from "react";
import { useHealthDetails } from "@/lib/api/settings";

type Variant = "success" | "warning" | "danger" | "muted";

const DOT_BG: Record<Variant, string> = {
  success: "bg-success",
  warning: "bg-warning",
  danger: "bg-danger",
  muted: "bg-bg-8",
};

const DOT_GLOW: Record<Variant, string> = {
  success: "rgba(126, 180, 122, 0.4)",
  warning: "rgba(212, 168, 88, 0.4)",
  danger: "rgba(199, 98, 92, 0.4)",
  muted: "rgba(124, 124, 134, 0.3)",
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

  return (
    <div
      className="flex items-center gap-2 text-[12px] text-bg-10 font-display tracking-[.02em] cursor-default"
      title={
        data
          ? data.workers
              .map((w) => `${w.status === "ONLINE" ? "●" : "○"} ${w.name}`)
              .join("\n")
          : "Здоровье воркеров недоступно"
      }
      aria-label={`Воркеры: ${label}`}
    >
      <span
        aria-hidden="true"
        style={
          {
            "--pulse-glow": DOT_GLOW[variant],
            "--pulse-color": DOT_GLOW[variant],
          } as CSSProperties
        }
        className={`size-2 rounded-full ${DOT_BG[variant]} pulse-dot`}
      />
      <span>{label}</span>
    </div>
  );
}
