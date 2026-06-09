/**
 * IncidentRow — строка активного инцидента в feed'е Dashboard.
 *
 * Макет (dashboard.html):
 *   [stage badge] [ad name ellipsis] [rule-pill] [age]
 *   Кликабельная, hover: bg-bg-2.
 *
 * stage badge: stop_sent → "stop" красный, claimed → "claimed" синий,
 *   warning_sent → "warning" оранжевый.
 */

import { Badge } from "@/components/ui/Badge";
import { Pill } from "@/components/ui/Pill";
import { ruleCodeLabel, formatRelativeTime, ALERT_STAGE_LABELS } from "@fb/shared";
import type { Incident } from "@fb/shared";
import { cn } from "@/lib/utils/cn";

interface IncidentRowProps {
  incident: Incident;
  onClick?: () => void;
}

export function IncidentRow({ incident, onClick }: IncidentRowProps) {
  // Определяем variant по alert_state
  const isStop = incident.alert_state === "stop_sent";
  const isClaimed = incident.alert_state === "claimed";

  let stageBadgeVariant: "stop" | "claimed" | "warning" = "warning";
  let stageLabel = ALERT_STAGE_LABELS.warning;
  if (isStop) {
    stageBadgeVariant = "stop";
    stageLabel = ALERT_STAGE_LABELS.stop;
  } else if (isClaimed) {
    stageBadgeVariant = "claimed";
    stageLabel = "В работе";
  }

  // Объединяем stop + warning rule codes
  const allCodes = [
    ...(incident.stop_rule_codes ?? []),
    ...(incident.warning_rule_codes ?? []),
  ];
  const firstCode = allCodes[0];
  const extraCount = allCodes.length - 1;

  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "w-full flex items-center gap-3 py-3.5 px-2 -mx-2",
        "border-b border-bg-3 last:border-b-0 text-left",
        "transition-colors duration-[120ms] hover:bg-bg-2",
        // F3 (WCAG 2.4.7): НЕ глушим outline — оставляем видимый фокус-индикатор.
        "focus-visible:bg-bg-2",
      )}
    >
      {/* Stage badge */}
      <Badge variant={stageBadgeVariant} size="sm" className="shrink-0">
        {stageLabel}
      </Badge>

      {/* Ad name (ellipsis) */}
      <span className="flex-1 min-w-0 font-display text-[13.5px] text-bg-11 truncate tracking-tight">
        {incident.ad_name ?? incident.fb_ad_id}
      </span>

      {/* Rule pill + overflow counter */}
      {firstCode ? (
        <span className="flex items-center gap-1 shrink-0">
          <Pill className="text-[10.5px]">
            {ruleCodeLabel(firstCode, true)}
          </Pill>
          {extraCount > 0 ? (
            <span
              className="font-display text-[10px] text-bg-9 tracking-wider"
              title={allCodes.slice(1).join(", ")}
            >
              +{extraCount}
            </span>
          ) : null}
        </span>
      ) : null}

      {/* Age */}
      <span className="font-display text-[11px] text-bg-9 tracking-wider w-14 text-right tabular-nums shrink-0">
        {formatRelativeTime(incident.incident_open_since)}
      </span>
    </button>
  );
}
