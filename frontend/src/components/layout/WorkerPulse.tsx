/**
 * WorkerPulse — worker-chip для TopBar (канон design_handoff/dashboard-shared.jsx).
 *
 * Пульс-точка + mono «N/M воркеров», dotted-underline. Цвет:
 *   все живы → bg-10 (нейтрально), 1–2 down → warning (жёлтый), 3+ → danger.
 * При hover/focus — popover со списком воркеров (online/offline).
 *
 * Данные: GET /health/details (useHealthDetails). Worker-статус живёт ТОЛЬКО
 * здесь (в Sidebar не дублируется).
 */

import { useState } from "react";
import { PulseDot } from "@/components/data/PulseDot";
import { useHealthDetails } from "@/lib/api/settings";

export function WorkerPulse() {
  const { data, isError } = useHealthDetails();
  const [open, setOpen] = useState(false);

  const workers = data?.workers ?? [];
  const total = workers.length;
  const online = workers.filter((w) => w.status === "ONLINE").length;
  const down = total - online;

  // Цвет по числу упавших (канон: 0 → success, 1-2 → warning, 3+ → danger).
  const color =
    isError || total === 0
      ? "var(--bg-8)"
      : down === 0
        ? "var(--success)"
        : down <= 2
          ? "var(--warning)"
          : "var(--danger)";

  const label = total > 0 ? `${online}/${total} воркеров` : "воркеры";
  // Текст-цвет чипа: при наличии down — семантический, иначе bg-10.
  const textClass =
    down > 0 && total > 0
      ? down <= 2
        ? "text-warning"
        : "text-danger"
      : "text-bg-10";

  // offline вперёд для popover.
  const sorted = [...workers].sort(
    (a, b) => (a.status === "ONLINE" ? 1 : 0) - (b.status === "ONLINE" ? 1 : 0),
  );

  return (
    <span
      className="relative inline-flex"
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
    >
      <span
        tabIndex={0}
        role="button"
        aria-expanded={open}
        aria-label={`Воркеры: ${label}`}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        className={`inline-flex cursor-default items-center gap-1.5 border-b border-dotted border-bg-7 pb-px font-display text-[12px] tracking-[0.02em] ${textClass}`}
      >
        <PulseDot size={7} color={color} />
        <span className="tabular-nums whitespace-nowrap">{label}</span>
      </span>

      {open && total > 0 && (
        <div
          role="tooltip"
          className="absolute right-0 top-[calc(100%+8px)] z-[80] w-[248px] border border-bg-6 bg-bg-3 p-3"
        >
          <div className="mb-2 flex items-center justify-between">
            <span className="font-display text-[10px] font-semibold uppercase tracking-[0.12em] text-bg-9">
              ВОРКЕРЫ
            </span>
            <span className="font-display text-[11px] tabular-nums" style={{ color }}>
              {online}/{total} online
            </span>
          </div>
          <div className="flex flex-col">
            {sorted.map((w) => (
              <div
                key={w.name}
                className="grid grid-cols-[auto_1fr_auto] items-center gap-2 border-t border-bg-5 py-1"
              >
                <span
                  aria-hidden="true"
                  className="size-1.5 shrink-0 rounded-full"
                  style={{
                    background: w.status === "ONLINE" ? "var(--success)" : "var(--danger)",
                  }}
                />
                <span
                  className={`truncate font-display text-[12px] ${
                    w.status === "ONLINE" ? "text-bg-10" : "text-bg-11"
                  }`}
                >
                  {w.name}
                </span>
                <span
                  className="text-[10px]"
                  style={{
                    color: w.status === "ONLINE" ? "var(--bg-8)" : "var(--danger)",
                  }}
                >
                  {w.status === "ONLINE" ? "up" : "down"}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </span>
  );
}
