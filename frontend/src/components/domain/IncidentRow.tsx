/**
 * IncidentRow — строка в Active Incidents list на DashboardPage.
 *   [stage badge] [ad name] [rule] [age]
 */

import { Badge } from "@/components/ui/Badge";
import { formatRelativeTime } from "@/lib/utils/format";
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
  const label = isStop ? "stop" : isClaimed ? "claim" : "warn";

  const primaryRule = incident.stop_rule_codes[0] ?? incident.warning_rule_codes[0] ?? null;

  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "w-full flex items-center gap-3.5 py-3.5 px-2 -mx-2",
        "border-b border-bg-3 last:border-b-0 text-left",
        "transition-colors hover:bg-bg-2",
      )}
    >
      <div className="w-16 shrink-0">
        <Badge variant={variant}>{label}</Badge>
      </div>
      <span className="flex-1 font-display text-[13.5px] text-bg-11 truncate tracking-tight">
        {incident.ad_name ?? incident.fb_ad_id}
      </span>
      {primaryRule ? (
        <span className="font-display text-[11px] tracking-wider text-bg-10 bg-bg-3 border border-bg-6 px-1.5 py-0.5">
          {primaryRule}
        </span>
      ) : null}
      <span className="font-display text-[11px] text-bg-9 tracking-wider w-14 text-right tabular-nums">
        {formatRelativeTime(incident.incident_open_since)}
      </span>
    </button>
  );
}
