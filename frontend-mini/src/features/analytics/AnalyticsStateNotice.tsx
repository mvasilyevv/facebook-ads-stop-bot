import type { DataState } from "@fb/shared/operator/contracts";
import {
  DATA_STATE_DESCRIPTION,
  DATA_STATE_LABEL,
} from "@fb/shared/operator/viewModel";
import { DataStateBadge } from "@fb/operator-ui";

import { cn } from "@/lib/cn";

/**
 * Живёт в отдельном файле от `DaypartDayChart`, чтобы страница аналитики
 * могла статически импортировать этот лёгкий баннер, а сам тяжёлый график
 * (SVG + интерактив) оставался отдельным ленивым чанком. Общий файл делал
 * ленивую загрузку графика бесполезной — сборщик всё равно тянул оба
 * экспорта в один чанк по статическому импорту баннера.
 */
export function AnalyticsStateNotice({
  state,
  issue,
  testId,
}: {
  state: Exclude<DataState, "ready">;
  issue?: string;
  testId?: string;
}) {
  return (
    <div
      role={state === "unavailable" ? "alert" : "status"}
      data-state={state}
      data-testid={testId}
      className={cn(
        "rounded-[var(--radius-2)] border bg-bg-2 px-4 py-3 text-[14px]",
        state === "partial"
          ? "border-warning/30"
          : state === "unavailable"
            ? "border-danger/30"
            : "border-[var(--color-hairline-strong)]",
      )}
    >
      <div className="flex items-center justify-between gap-3">
        <strong className="text-bg-11">{DATA_STATE_LABEL[state]}</strong>
        <DataStateBadge state={state} compact />
      </div>
      <p className="m-0 mt-1 leading-5 text-bg-9">
        {issue || DATA_STATE_DESCRIPTION[state]}
      </p>
    </div>
  );
}
