/**
 * IncidentRow — строка в Active Incidents list на DashboardPage.
 *   [stage badge] [ad name] [rule] [age]
 */

import { Badge } from "@/components/ui/Badge";
import { RuleBadge } from "@/components/domain/RuleBadge";
import { formatRelativeTime } from "@/lib/utils/format";
import { ALERT_STAGE_LABELS, ALERT_STATE_LABELS } from "@/lib/constants/states";
import type { Incident } from "@/lib/types/api";
import { cn } from "@/lib/utils/cn";

interface IncidentRowProps {
  incident: Incident;
  onClick?: () => void;
}

export function IncidentRow({ incident, onClick }: IncidentRowProps) {
  const isStop = incident.alert_state === "stop_sent";
  const isClaimed = incident.alert_state === "claimed";
  const variant = isStop ? "stop" : isClaimed ? "claimed" : "warning";
  const label = isStop
    ? ALERT_STAGE_LABELS.stop
    : isClaimed
      ? ALERT_STATE_LABELS.claimed
      : ALERT_STAGE_LABELS.warning;

  const codes = [...incident.stop_rule_codes, ...incident.warning_rule_codes];
  const extra = codes.length - 1;

  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "w-full flex items-center gap-3 py-3.5 px-2 -mx-2",
        "border-b border-bg-3 last:border-b-0 text-left",
        "transition-colors hover:bg-bg-2",
      )}
    >
      <Badge variant={variant} size="sm" className="shrink-0">
        {label}
      </Badge>
      <span className="flex-1 min-w-0 font-display text-[13.5px] text-bg-11 truncate tracking-tight">
        {incident.ad_name ?? incident.fb_ad_id}
      </span>
      {codes[0] ? (
        <span className="flex items-center gap-1 shrink-0">
          <RuleBadge code={codes[0]} />
          {extra > 0 ? (
            <span
              className="font-display text-[10px] text-bg-9 tracking-wider"
              title={codes.slice(1).join(", ")}
            >
              +{extra}
            </span>
          ) : null}
        </span>
      ) : null}
      <span className="font-display text-[11px] text-bg-9 tracking-wider w-12 text-right tabular-nums shrink-0">
        {formatRelativeTime(incident.incident_open_since)}
      </span>
    </button>
  );
}
