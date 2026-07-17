/**
 * HistoryEventsDrawer — drill-down по событиям алертов за период.
 * Открывается при клике на alert-строку в таймлайне.
 * Фильтры: по campaign_id / fb_ad_id / stage.
 * Использует useHistoryEvents с текущим периодом.
 */

import { useState, type FC } from "react";
import { Drawer } from "@/components/ui/Drawer";
import { Badge } from "@/components/ui/Badge";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorState } from "@/components/ui/ErrorState";
import { Skeleton } from "@/components/ui/Skeleton";
import { Select, type SelectOption } from "@/components/ui/Select";
import { useHistoryEvents } from "@/lib/api/history";
import { formatDisplayDateTime } from "@/lib/timezone";
import { ruleCodeLabel } from "@fb/shared";
import type { HistoryTimelineItem } from "@fb/shared";
import { AlertCircle } from "lucide-react";

interface HistoryEventsDrawerProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Начальный фильтр — передаётся при клике на event в таймлайне. */
  initialItem?: HistoryTimelineItem | null;
  period: { from_iso: string; to_iso: string };
}

const STAGE_OPTIONS: SelectOption[] = [
  { value: "", label: "Все стадии" },
  { value: "warning", label: "Warning" },
  { value: "stop", label: "Stop" },
];

export const HistoryEventsDrawer: FC<HistoryEventsDrawerProps> = ({
  open,
  onOpenChange,
  initialItem,
  period,
}) => {
  // Фильтры — инициализируем из initialItem
  const [stage, setStage] = useState(initialItem?.stage ?? "");
  const [fbAdId] = useState(initialItem?.fb_ad_id ?? undefined);

  const params = {
    from_iso: period.from_iso,
    to_iso: period.to_iso,
    fb_ad_id: fbAdId || undefined,
    stage: stage || undefined,
  };

  // isFetching при keepPreviousData: прежний список виден, но приглушён —
  // мягкий индикатор смены периода/фильтра вместо скелетон-моргания.
  const {
    data: events,
    isLoading,
    isFetching,
    error,
    refetch,
  } = useHistoryEvents(open ? params : undefined);

  const stageBadge = (s: string) => {
    if (s === "stop") return "stop" as const;
    if (s === "warning") return "warning" as const;
    return "neutral" as const;
  };

  const drawerTitle = initialItem?.ad_name
    ? `События: ${initialItem.ad_name}`
    : fbAdId
      ? `События: ${fbAdId}`
      : "История событий";

  return (
    <Drawer
      open={open}
      onOpenChange={onOpenChange}
      eyebrow="HISTORY · DRILL-DOWN"
      title={drawerTitle}
      description={`${period.from_iso.slice(0, 10)} — ${period.to_iso.slice(0, 10)}`}
      width={640}
    >
      {/* Фильтр стадии */}
      <div className="mb-5">
        <Select
          options={STAGE_OPTIONS}
          value={stage}
          onChange={(e) => setStage(e.target.value)}
          aria-label="Фильтр по стадии"
        />
      </div>

      {/* Контент */}
      {isLoading ? (
        <div className="space-y-3">
          {Array.from({ length: 5 }).map((_, i) => (
            <Skeleton key={i} className="h-14 w-full" />
          ))}
        </div>
      ) : error ? (
        <ErrorState error={error} onRetry={() => void refetch()} />
      ) : !events || events.length === 0 ? (
        <EmptyState
          icon={<AlertCircle size={24} />}
          title="Событий не найдено"
          description="По выбранным фильтрам и периоду событий нет."
        />
      ) : (
        <div
          className={
            isFetching
              ? "space-y-1 opacity-60 transition-opacity duration-200"
              : "space-y-1 transition-opacity duration-200"
          }
          role="list"
          aria-label="Список событий"
          aria-busy={isFetching}
        >
          {events.map((ev) => (
            <div
              key={ev.id}
              role="listitem"
              className="border border-[var(--hairline)] rounded-[var(--radius-2)] bg-bg-1 hover:bg-bg-2 transition-colors p-4"
            >
              {/* Строка 1: время + бейдж стадии */}
              <div className="flex items-center justify-between gap-3 mb-2">
                <span className="font-display text-[10.5px] text-bg-9 tracking-[0.04em]">
                  {formatDisplayDateTime(ev.created_at)}
                </span>
                <Badge variant={stageBadge(ev.stage)} size="sm" withDot>
                  {ev.stage.toUpperCase()}
                </Badge>
              </div>

              {/* Строка 2: название объявления */}
              <div className="font-display text-[13px] text-bg-11 mb-1 truncate">{ev.ad_name}</div>

              {/* Строка 3: кампания + оффер */}
              {(ev.campaign_name || ev.offer_code) && (
                <div className="font-display text-[11px] text-bg-9 mb-2">
                  {ev.campaign_name}
                  {ev.offer_code && <span className="ml-2 text-bg-8">· {ev.offer_code}</span>}
                </div>
              )}

              {/* Правила-пилюли */}
              {ev.matched_rule_codes.length > 0 && (
                <div className="flex flex-wrap gap-1 mt-1">
                  {ev.matched_rule_codes.map((code) => (
                    <span
                      key={code}
                      title={ruleCodeLabel(code, false)}
                      className="inline-block bg-bg-3 border border-[var(--hairline)] rounded-[var(--radius-1)] px-1.5 py-0.5 font-display text-[10.5px] tracking-[0.04em] text-bg-10"
                    >
                      {ruleCodeLabel(code, true)}
                    </span>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </Drawer>
  );
};
