/**
 * HistoryEventsTable — drill-down таблица AlertEvent с фильтрами.
 * Колонки: time / stage / ad_name / offer / matched_rules.
 */

import { useState, type ChangeEvent } from "react";
import { Badge } from "@/components/ui/Badge";
import { Skeleton } from "@/components/ui/Skeleton";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorState } from "@/components/ui/ErrorState";
import { RuleBadge } from "@/components/domain/RuleBadge";
import { formatDateTime } from "@/lib/utils/format";
import { ALERT_STAGE_LABELS } from "@/lib/constants/states";
import { cn } from "@/lib/utils/cn";
import { Filter, Clock } from "lucide-react";
import type { AlertEvent } from "@/lib/types/api";

interface HistoryEventsTableProps {
  events: AlertEvent[] | undefined;
  isLoading: boolean;
  isError: boolean;
  error?: unknown;
  onRetry?: () => void;
  /** Колбэк при выборе фильтра stage (для связки с родителем). */
  onStageFilter?: (stage: string | null) => void;
  /** Колбэк при вводе campaign_id. */
  onCampaignFilter?: (id: string | null) => void;
}

export function HistoryEventsTable({
  events,
  isLoading,
  isError,
  error,
  onRetry,
  onStageFilter,
  onCampaignFilter,
}: HistoryEventsTableProps) {
  const [stageFilter, setStageFilter] = useState<"all" | "warning" | "stop">("all");
  const [campaignInput, setCampaignInput] = useState("");

  if (isError) {
    return (
      <ErrorState
        title="Не удалось загрузить события."
        error={error}
        onRetry={onRetry}
      />
    );
  }

  const handleStageChange = (s: "all" | "warning" | "stop") => {
    setStageFilter(s);
    onStageFilter?.(s === "all" ? null : s);
  };

  const handleCampaignChange = (e: ChangeEvent<HTMLInputElement>) => {
    const v = e.target.value;
    setCampaignInput(v);
    onCampaignFilter?.(v.trim() || null);
  };

  // Фильтр по stage делает сервер (onStageFilter → рефетч). Локально — только по кампании.
  const filtered = (events ?? []).filter((ev) => {
    if (
      campaignInput.trim() &&
      !ev.campaign_name?.toLowerCase().includes(campaignInput.toLowerCase())
    ) {
      return false;
    }
    return true;
  });

  return (
    <div>
      {/* Фильтры */}
      <div className="flex items-center gap-3 mb-5">
        <Filter size={14} className="text-bg-7 shrink-0" aria-hidden="true" />
        {/* Stage filter */}
        <div className="flex items-center gap-1 border border-bg-5 p-0.5">
          {(["all", "warning", "stop"] as const).map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => handleStageChange(s)}
              className={cn(
                "h-7 px-3 font-display text-[11px] uppercase tracking-wider transition-colors",
                stageFilter === s
                  ? "bg-bg-4 text-accent"
                  : "text-bg-9 hover:text-bg-11 hover:bg-bg-2",
              )}
            >
              {s === "all" ? "Все" : ALERT_STAGE_LABELS[s]}
            </button>
          ))}
        </div>
        {/* Campaign filter */}
        <input
          type="text"
          value={campaignInput}
          onChange={handleCampaignChange}
          placeholder="Фильтр по кампании..."
          className={cn(
            "h-7 px-3 bg-bg-2 border border-bg-5 font-display text-[12px] text-bg-11",
            "placeholder:text-bg-9 tracking-tight w-48",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent",
          )}
          aria-label="Фильтр по названию кампании"
        />
        <span className="font-display text-[11px] text-bg-9 tracking-tight tabular-nums ml-auto">
          {filtered.length} событий
        </span>
      </div>

      {isLoading ? (
        <TableSkeleton />
      ) : filtered.length === 0 ? (
        <EmptyState
          icon={<Clock size={40} strokeWidth={1.25} aria-hidden="true" />}
          title="Событий не найдено."
          description="Измените фильтры или диапазон дат."
          className="py-12 border border-bg-5"
        />
      ) : (
        <div className="border border-bg-5">
          <table className="w-full border-collapse" aria-label="История событий">
            <thead>
              <tr className="border-b border-bg-5">
                {["Время", "Стадия", "Объявление", "Оффер", "Правила"].map((h) => (
                  <th
                    key={h}
                    scope="col"
                    className="px-4 py-2.5 text-left font-display text-[10px] uppercase tracking-[0.14em] text-bg-8 bg-bg-2"
                  >
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {filtered.map((ev) => (
                <tr
                  key={ev.id}
                  className="border-b border-bg-3 last:border-b-0 hover:bg-bg-2 transition-colors"
                >
                  <td className="px-4 py-3 font-display text-[11px] text-bg-9 whitespace-nowrap tabular-nums">
                    {formatDateTime(ev.created_at)}
                  </td>
                  <td className="px-4 py-3">
                    <Badge variant={ev.stage === "stop" ? "stop" : "warning"} size="sm">
                      {ALERT_STAGE_LABELS[ev.stage === "stop" ? "stop" : "warning"]}
                    </Badge>
                  </td>
                  <td className="px-4 py-3 font-display text-[12px] text-bg-11 max-w-[220px] truncate">
                    {ev.ad_name ?? ev.fb_ad_id ?? "—"}
                    {ev.campaign_name && (
                      <div className="text-[11px] text-bg-9 truncate">{ev.campaign_name}</div>
                    )}
                  </td>
                  <td className="px-4 py-3 font-display text-[12px] text-bg-10">
                    {ev.offer_code ?? "—"}
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex flex-wrap gap-1">
                      {ev.matched_rule_codes.slice(0, 3).map((code) => (
                        <RuleBadge key={code} code={code} />
                      ))}
                      {ev.matched_rule_codes.length > 3 && (
                        <span className="font-display text-[10px] text-bg-9">
                          +{ev.matched_rule_codes.length - 3}
                        </span>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

/** Skeleton-строки таблицы. */
function TableSkeleton() {
  return (
    <div className="border border-bg-5">
      <div className="grid grid-cols-5 gap-4 px-4 py-2.5 bg-bg-2 border-b border-bg-5">
        {[0, 1, 2, 3, 4].map((i) => <Skeleton key={i} height={10} />)}
      </div>
      {[0, 1, 2, 3, 4].map((i) => (
        <div key={i} className="grid grid-cols-5 gap-4 px-4 py-3 border-b border-bg-3 last:border-b-0">
          {[0, 1, 2, 3, 4].map((j) => <Skeleton key={j} height={14} />)}
        </div>
      ))}
    </div>
  );
}
