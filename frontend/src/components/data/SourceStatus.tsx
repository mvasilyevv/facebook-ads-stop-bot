import { Radio, TriangleAlert } from "lucide-react";

import { Card } from "@/components/ui/Card";
import { cn } from "@/lib/utils/cn";
import {
  formatTrackerCount,
  formatTrackerLag,
  formatTrackerTimestamp,
  readTrackerRealtime,
} from "@/lib/types/trackerRealtime";

export function MetaDelayedNote({ className }: { className?: string }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 font-display text-[10px] uppercase tracking-[0.1em] text-bg-8",
        className,
      )}
      title="Meta Insights обновляет расходы и клики не мгновенно"
    >
      <span className="size-1.5 rounded-full bg-info" aria-hidden="true" />
      Meta · возможна задержка
    </span>
  );
}

interface TrackerLiveStripProps {
  data?: unknown;
  compact?: boolean;
  className?: string;
}

interface QualityView {
  label: string;
  tone: "success" | "warning" | "neutral";
}

function qualityView(value: string | null, available: boolean | null): QualityView {
  if (available === false) return { label: "Источник недоступен", tone: "warning" };
  const normalized = value?.trim().toLowerCase();
  if (["live", "good", "healthy", "ok", "exact"].includes(normalized ?? "")) {
    return { label: "Данные согласованы", tone: "success" };
  }
  if (["degraded", "partial", "stale", "drift", "incomplete"].includes(normalized ?? "")) {
    return { label: "Требует внимания", tone: "warning" };
  }
  return { label: "Качество не подтверждено", tone: "neutral" };
}

export function TrackerLiveStrip({ data, compact = false, className }: TrackerLiveStripProps) {
  const tracker = readTrackerRealtime(data);
  const quality = qualityView(tracker?.dataQuality ?? null, tracker?.available ?? null);
  const valuesTrusted = tracker?.available !== false;
  const unmatched = valuesTrusted ? (tracker?.unmatchedEvents ?? null) : null;
  const metrics = [
    ["Регистрации", valuesTrusted ? (tracker?.registrations ?? null) : null],
    ["FTD", valuesTrusted ? (tracker?.ftds ?? null) : null],
    ["Подтверждены", valuesTrusted ? (tracker?.confirmedDeposits ?? null) : null],
    ["Редепозиты", valuesTrusted ? (tracker?.redeposits ?? null) : null],
    ["Не сопоставлено", unmatched],
  ] as const;

  return (
    <Card
      className={cn("overflow-hidden border-[rgba(56,211,159,0.2)]", className)}
      eyebrow={
        <span className="inline-flex items-center gap-2 text-success">
          <Radio size={12} aria-hidden="true" />
          AdSet.pro · Live
        </span>
      }
      action={
        <span
          className={cn(
            "inline-flex items-center gap-1.5 rounded-full border px-2 py-1 font-display text-[9px] uppercase tracking-[0.08em]",
            quality.tone === "success" && "border-success/30 bg-success-bg text-success",
            quality.tone === "warning" && "border-warning/30 bg-warning-bg text-warning",
            quality.tone === "neutral" && "border-[var(--hairline)] bg-bg-2 text-bg-9",
          )}
        >
          {quality.tone === "warning" ? <TriangleAlert size={10} aria-hidden="true" /> : null}
          {quality.label}
        </span>
      }
      aria-label="Оперативные данные AdSet.pro"
    >
      <div
        className={cn(
          "grid gap-px overflow-hidden rounded-[var(--radius-2)] border border-[var(--hairline)] bg-[var(--hairline)]",
          compact ? "grid-cols-2" : "grid-cols-2 md:grid-cols-5",
        )}
        role="list"
      >
        {metrics.map(([label, value]) => (
          <div key={label} className="min-w-0 bg-bg-0 px-3 py-2.5">
            <div className="truncate text-[10px] text-bg-8">{label}</div>
            <div
              className={cn(
                "mt-1 font-display text-[15px] tabular-nums text-bg-11",
                label === "Не сопоставлено" && value != null && value > 0 && "text-warning",
              )}
            >
              {formatTrackerCount(value)}
            </div>
          </div>
        ))}
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-[10px] text-bg-8">
        <span>
          Последний postback: <strong className="font-normal text-bg-10">{formatTrackerTimestamp(tracker?.lastEventAt ?? null)}</strong>
        </span>
        <span>
          Обработка: <strong className="font-normal text-bg-10">{formatTrackerLag(tracker?.processingLagMs ?? null)}</strong>
        </span>
        <span>
          Очередь: <strong className="font-normal tabular-nums text-bg-10">{formatTrackerCount(valuesTrusted ? (tracker?.backlog ?? null) : null)}</strong>
        </span>
        {tracker?.duplicateEvents != null ? (
          <span>
            Дубли:{" "}
            <strong className="font-normal tabular-nums text-bg-10">
              {formatTrackerCount(tracker.duplicateEvents)}
            </strong>
          </span>
        ) : null}
        {tracker?.unsupportedEvents != null ? (
          <span>
            Неподдерживаемые:{" "}
            <strong className="font-normal tabular-nums text-bg-10">
              {formatTrackerCount(tracker.unsupportedEvents)}
            </strong>
          </span>
        ) : null}
        {tracker?.reconciliationDrift != null ? (
          <span>
            Расхождение сверки:{" "}
            <strong
              className={cn(
                "font-normal tabular-nums",
                tracker.reconciliationDrift === 0 ? "text-success" : "text-warning",
              )}
            >
              {formatTrackerCount(tracker.reconciliationDrift)}
            </strong>
          </span>
        ) : null}
      </div>
    </Card>
  );
}
