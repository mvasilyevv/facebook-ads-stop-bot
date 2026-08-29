/**
 * WorkerPulse — компактный статус источников и воркеров для TopBar.
 *
 * Пульс-точка + mono «N/M воркеров», dotted-underline. Цвет:
 *   все живы → bg-10 (нейтрально), 1–2 down → warning (жёлтый), 3+ → danger.
 * При hover/focus — popover со списком воркеров (online/offline).
 *
 * Данные: канонический operator snapshot. Cached/reconnecting состояние никогда
 * не отображается зелёным.
 */

import { useState } from "react";
import { useOperatorRealtimeStatus } from "@fb/operator-api";
import { severityForDataState, snapshotForRealtimeState, workerStatusLabel } from "@fb/shared/operator/viewModel";
import { PulseDot } from "@/components/data/PulseDot";
import { useOperatorSnapshot } from "@/lib/api/operator";

export function WorkerPulse() {
  const { data, isError } = useOperatorSnapshot({ window: "today" });
  const realtimeStatus = useOperatorRealtimeStatus();
  const [open, setOpen] = useState(false);

  const snapshot = data ? snapshotForRealtimeState(data, realtimeStatus === "connected") : null;
  const systemState = snapshot?.system.state ?? "unavailable";
  // Кабинеты-сканеры (cabinet_runtime) и одиннадцать фоновых воркеров
  // (issue #176) — один общий пульс. Раньше здесь были только сканеры:
  // остановившийся campaign_creator/reconciler/... не поднимал бы этот чип
  // вообще, а это единственный индикатор, который оператор видит всегда.
  const workers = [
    ...(snapshot?.system.data?.workers ?? []),
    ...(snapshot?.system.data?.background_workers ?? []),
  ];
  const confirmed = systemState === "ready";
  const total = workers.length;
  const online = confirmed ? workers.filter((worker) => worker.severity === "ok").length : 0;
  const down = total - online;

  // Цвет по числу упавших (канон: 0 → success, 1-2 → warning, 3+ → danger).
  const color =
    isError || !confirmed || total === 0
      ? "var(--color-bg-8)"
      : down === 0
        ? "var(--color-success)"
        : down <= 2
          ? "var(--color-warning)"
          : "var(--color-danger)";

  const label = confirmed
    ? `${online}/${total} воркеров`
    : total > 0
      ? `—/${total} воркеров`
      : "—/— воркеров";
  const ariaLabel = confirmed ? `Воркеры: ${label}` : "Воркеры: статус не подтверждён";
  // Текст-цвет чипа: при наличии down — семантический, иначе bg-10.
  const textClass =
    confirmed && down > 0 && total > 0
      ? down <= 2
        ? "text-warning"
        : "text-danger"
      : "text-bg-10";

  // offline вперёд для popover.
  const sorted = [...workers].sort(
    (a, b) =>
      (a.status.trim().toLowerCase() === "online" ? 1 : 0) -
      (b.status.trim().toLowerCase() === "online" ? 1 : 0),
  );

  return (
    <div
      className="relative inline-flex"
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
    >
      <button
        type="button"
        aria-expanded={open}
        aria-controls="worker-status-popover"
        aria-label={ariaLabel}
        onClick={() => setOpen((value) => !value)}
        onBlur={() => setOpen(false)}
        onKeyDown={(event) => {
          if (event.key === "Escape") setOpen(false);
        }}
        className={`inline-flex min-h-11 cursor-pointer items-center gap-1.5 border-b border-dotted border-bg-7 px-2 font-display text-[12px] tracking-[0.02em] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent ${textClass}`}
      >
        <PulseDot size={7} color={color} />
        <span className="tabular-nums whitespace-nowrap">{label}</span>
      </button>

      {open && total > 0 && (
        <div
          id="worker-status-popover"
          role="tooltip"
          className="absolute right-0 top-[calc(100%+8px)] z-[80] w-[248px] rounded-[var(--radius-3)] border border-[var(--color-hairline-strong)] bg-bg-3 p-3"
        >
          <div className="mb-2 flex items-center justify-between">
            <span className="font-display text-[12px] font-semibold uppercase tracking-[0.08em] text-bg-9">
              ВОРКЕРЫ
            </span>
            <span className="font-display text-[12px] tabular-nums" style={{ color }}>
              {confirmed ? `${online}/${total} в работе` : "Не подтверждено"}
            </span>
          </div>
          <div className="flex flex-col">
            {sorted.map((w) => {
              const severity = severityForDataState(w.severity, systemState);
              return (
                <div
                  key={w.id}
                  className="grid grid-cols-[auto_1fr_auto] items-center gap-2 border-t border-[var(--color-hairline)] py-1"
                >
                  <span
                    aria-hidden="true"
                    className="size-1.5 shrink-0 rounded-full"
                    style={{
                      background:
                        severity === "ok"
                          ? "var(--color-success)"
                          : severity === "warning"
                            ? "var(--color-warning)"
                            : severity === "critical"
                              ? "var(--color-danger)"
                              : "var(--color-bg-8)",
                    }}
                  />
                  <span
                    className={`min-w-0 truncate font-display text-[12px] ${
                      severity === "ok" ? "text-bg-10" : "text-bg-11"
                    }`}
                  >
                    {w.label}
                  </span>
                  <span
                    className="text-[12px]"
                    style={{
                      color: severity === "critical" ? "var(--color-danger)" : "var(--color-bg-8)",
                    }}
                  >
                    {confirmed ? workerStatusLabel(w.status) : "не подтверждено"}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
