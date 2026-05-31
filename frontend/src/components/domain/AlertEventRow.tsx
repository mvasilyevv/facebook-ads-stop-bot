/**
 * AlertEventRow — строка в feed'е алертов (Dashboard + History).
 * Composite: time + stage badge + ad name + rule pills + chevron.
 */

import { ChevronRight } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { RuleBadge } from "@/components/domain/RuleBadge";
import { formatTimeOfDay, formatDateTime } from "@/lib/utils/format";
import { ALERT_STAGE_LABELS } from "@/lib/constants/states";
import type { AlertEvent } from "@/lib/types/api";
import { cn } from "@/lib/utils/cn";

interface AlertEventRowProps {
  event: AlertEvent;
  onClick?: () => void;
}

export function AlertEventRow({ event, onClick }: AlertEventRowProps) {
  const isStop = event.stage === "stop";
  const codes = event.matched_rule_codes ?? [];
  const extra = codes.length - 3;

  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "w-full grid grid-cols-[64px_116px_1fr_auto_24px] gap-4 items-center",
        "py-3.5 px-4 -mx-4",
        "border-b border-bg-3 last:border-b-0",
        "text-left transition-colors",
        "hover:bg-bg-2",
        "focus-visible:bg-bg-2",
      )}
    >
      <span
        className="font-display text-[11px] text-bg-9 tracking-tight tabular-nums"
        title={`${formatDateTime(event.created_at)} UTC`}
      >
        {formatTimeOfDay(event.created_at)}
      </span>
      <Badge variant={isStop ? "stop" : "warning"} size="sm">
        {ALERT_STAGE_LABELS[isStop ? "stop" : "warning"]}
      </Badge>
      <span className="font-display text-[13px] text-bg-11 truncate tracking-tight">
        {event.ad_name ?? event.fb_ad_id ?? "—"}
      </span>
      <div className="flex gap-1.5 items-center">
        {codes.slice(0, 3).map((code) => (
          <RuleBadge key={code} code={code} />
        ))}
        {extra > 0 ? (
          <span
            className="font-display text-[10px] text-bg-9 tracking-wider"
            title={codes.slice(3).join(", ")}
          >
            +{extra}
          </span>
        ) : null}
      </div>
      <ChevronRight size={14} className="text-bg-7" aria-hidden="true" />
    </button>
  );
}
