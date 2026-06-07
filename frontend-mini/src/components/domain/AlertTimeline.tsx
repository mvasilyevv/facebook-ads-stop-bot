/**
 * AlertTimeline — лента recent_alerts для AdDetail.
 * Канон: mono, eyebrow, stage-badge (warning/stop), время (formatRelativeTime),
 * reason_title. Без зависимостей вне @/components/ui + @fb/shared.
 */
import type { TmaRecentAlert } from "@/lib/api";
import { Badge } from "@/components/ui";
import { formatRelativeTime } from "@fb/shared";

interface AlertTimelineProps {
  alerts: TmaRecentAlert[];
}

export function AlertTimeline({ alerts }: AlertTimelineProps) {
  if (alerts.length === 0) return null;

  return (
    <div className="flex flex-col divide-y divide-bg-5">
      {alerts.slice(0, 10).map((al, i) => {
        const isStop = al.stage?.toLowerCase() === "stop";
        return (
          <div key={i} className="flex items-start gap-3 py-2.5">
            {/* stage-badge */}
            <Badge
              variant={isStop ? "stop" : "warning"}
              size="sm"
              className="shrink-0 mt-px"
            >
              {isStop ? "СТОП" : "WARN"}
            </Badge>
            {/* время + причина */}
            <div className="flex-1 min-w-0">
              <span
                className="block font-display tabular-nums text-bg-9"
                style={{ fontSize: 10, letterSpacing: "0.04em", lineHeight: 1 }}
              >
                {al.created_at ? formatRelativeTime(al.created_at) : "—"}
              </span>
              {al.reason_title && (
                <p
                  className="font-display text-bg-11 mt-1 leading-snug"
                  style={{ fontSize: 13 }}
                >
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
