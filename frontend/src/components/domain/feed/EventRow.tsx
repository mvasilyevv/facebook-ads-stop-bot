/**
 * EventRow — строка alert-event в feed'е (Dashboard alerts + History).
 *
 * Макет (dashboard.html):
 *   grid: time / stage-badge / ad-name (ellipsis) / rule-pills / chevron-arrow
 *   hover: bg-bg-2, accent-chevron.
 */

import { ChevronRight } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { Pill } from "@/components/ui/Pill";
import {
  ruleCodeLabel,
  ALERT_STAGE_LABELS,
  formatTimeOfDay,
  formatDateTime,
} from "@fb/shared";
import type { AlertEvent } from "@fb/shared";
import { cn } from "@/lib/utils/cn";

interface EventRowProps {
  event: AlertEvent;
  onClick?: () => void;
}

export function EventRow({ event, onClick }: EventRowProps) {
  const isStop = event.stage === "stop";
  const codes = event.matched_rule_codes ?? [];
  // Показываем до 3 пилюль, остальные — счётчик
  const visibleCodes = codes.slice(0, 3);
  const extraCount = codes.length - 3;

  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "w-full text-left",
        // grid: time 64px / badge 116px / name 1fr / pills auto / chevron 24px
        "grid items-center gap-4",
        "py-3.5 px-4 -mx-4",
        "border-b border-bg-3 last:border-b-0",
        "transition-colors duration-[120ms] hover:bg-bg-2",
        // F3 (WCAG 2.4.7): НЕ глушим outline — оставляем видимый фокус-индикатор.
        "focus-visible:bg-bg-2",
        // Accent chevron при hover через group
        "group",
      )}
      style={{ gridTemplateColumns: "64px 116px 1fr auto 24px" }}
    >
      {/* Время (UTC) */}
      <span
        className="font-display text-[11px] text-bg-9 tracking-tight tabular-nums"
        title={`${formatDateTime(event.created_at)} UTC`}
      >
        {formatTimeOfDay(event.created_at)}
      </span>

      {/* Stage badge */}
      <Badge variant={isStop ? "stop" : "warning"} size="sm">
        {isStop ? ALERT_STAGE_LABELS.stop : ALERT_STAGE_LABELS.warning}
      </Badge>

      {/* Ad name */}
      <span className="font-display text-[13px] text-bg-11 truncate tracking-tight">
        {event.ad_name ?? event.fb_ad_id ?? "—"}
      </span>

      {/* Rule pills */}
      <div className="flex items-center gap-1.5 shrink-0">
        {visibleCodes.map((code) => (
          <Pill key={code} className="text-[10.5px]">
            {ruleCodeLabel(code, true)}
          </Pill>
        ))}
        {extraCount > 0 ? (
          <span
            className="font-display text-[10px] text-bg-9 tracking-wider"
            title={codes.slice(3).join(", ")}
          >
            +{extraCount}
          </span>
        ) : null}
      </div>

      {/* Chevron — accent при hover (через group) */}
      <ChevronRight
        size={14}
        aria-hidden="true"
        className="text-bg-8 group-hover:text-accent transition-colors duration-[120ms]"
      />
    </button>
  );
}
