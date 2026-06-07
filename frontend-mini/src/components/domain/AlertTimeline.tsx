/**
 * AlertTimeline — компактная лента алертов для AdDetail.
 * Локальный компонент: не трогает ui/.
 */
import type { TmaRecentAlert } from "@/lib/api";
import { Badge } from "@/components/ui";
import { ruleCodeLabel } from "@fb/shared";
import { formatRelativeTime } from "@fb/shared";

interface AlertTimelineProps {
  alerts: TmaRecentAlert[];
}

export function AlertTimeline({ alerts }: AlertTimelineProps) {
  if (alerts.length === 0) return null;

  return (
    <div className="flex flex-col divide-y divide-[var(--color-bg-5)]">
      {alerts.slice(0, 10).map((al, i) => {
        const isStop = al.stage?.toLowerCase() === "stop";
        return (
          <div key={i} className="flex items-start gap-2 py-2.5">
            <Badge
              variant={isStop ? "stop" : "warning"}
              className="mt-0.5 shrink-0 min-w-[48px] justify-center"
            >
              {isStop ? "СТОП" : "WARN"}
            </Badge>
            <div className="flex-1 min-w-0">
              <p className="text-[11px] text-[var(--color-bg-9)] font-mono leading-none mb-1">
                {formatRelativeTime(al.created_at)}
              </p>
              {al.reason_title && (
                <p className="text-[13px] text-[var(--color-bg-11)] leading-snug">
                  {al.reason_title}
                </p>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

/** Pill-лента кодов правил для алерта. */
export function RulePills({ codes }: { codes: string[] | null | undefined }) {
  if (!codes?.length) return null;
  return (
    <div className="flex flex-wrap gap-1">
      {codes.map((code) => (
        <span
          key={code}
          className="font-mono text-[10px] px-[5px] py-[2px] bg-[var(--color-bg-3)] text-[var(--color-bg-10)] leading-none"
        >
          {ruleCodeLabel(code, true)}
        </span>
      ))}
    </div>
  );
}
