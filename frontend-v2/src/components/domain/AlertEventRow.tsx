/**
 * AlertEventRow — строка в feed'е алертов (Dashboard + History).
 * Composite: time + stage badge + ad name + rule pills + chevron.
 */

import { ChevronRight } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { formatTimeOfDay } from "@/lib/utils/format";
import type { AlertEvent } from "@/lib/types/api";
import { cn } from "@/lib/utils/cn";

interface AlertEventRowProps {
  event: AlertEvent;
  onClick?: () => void;
}

export function AlertEventRow({ event, onClick }: AlertEventRowProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "w-full grid grid-cols-[80px_100px_1fr_auto_24px] gap-4 items-center",
        "py-3.5 px-4 -mx-4",
        "border-b border-bg-3 last:border-b-0",
        "text-left transition-colors",
        "hover:bg-bg-2",
        "focus-visible:bg-bg-2",
      )}
    >
      <span className="font-display text-[11px] text-bg-9 tracking-tight tabular-nums">
        {formatTimeOfDay(event.created_at)}
      </span>
      <Badge variant={event.stage === "stop" ? "stop" : "warning"}>
        {event.stage === "stop" ? "stop" : "warn"}
      </Badge>
      <span className="font-display text-[13px] text-bg-11 truncate tracking-tight">
        {event.ad_name ?? event.fb_ad_id ?? "—"}
      </span>
      <div className="flex gap-1.5">
        {event.matched_rule_codes.slice(0, 3).map((code) => (
          <span
            key={code}
            className="font-display text-[10px] tracking-wider bg-bg-3 text-bg-10 border border-bg-6 px-1.5 py-0.5"
          >
            {code}
          </span>
        ))}
      </div>
      <ChevronRight size={14} className="text-bg-7" aria-hidden="true" />
    </button>
  );
}
