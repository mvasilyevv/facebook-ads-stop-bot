/**
 * Helper для теста HistoryPage — экспортирует компонент без createFileRoute-обёртки.
 * Повторяет структуру routes/history/index.tsx: pill-периоды + KPI-сетка + stage + правила.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MiniHeader } from "@/components/layout/MiniHeader";
import { KpiPlate, Skeleton, EmptyState } from "@/components/ui";
import type { KpiVariant } from "@/components/ui";
import { useHistorySummary, useHistoryOffers, useHistoryCampaigns } from "@/lib/api";
import { formatSpend, formatInt, ruleCodeLabel } from "@fb/shared";
import type { HistorySummary, HistoryCampaign, HistoryOffer } from "@fb/shared";
import { useState } from "react";
import { cn } from "@/lib/cn";

const PERIODS: { days: number; label: string }[] = [
  { days: 7,  label: "7 дней" },
  { days: 30, label: "30 дней" },
  { days: 90, label: "90 дней" },
];

function MetaRow({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="flex items-center justify-between px-3 py-2 gap-2">
      <span className="text-[13px]">{label}</span>
      <span className="tabular-nums text-[15px]">{value}</span>
    </div>
  );
}

function TestHistoryPage() {
  const [days, setDays] = useState(7);

  const summary = useHistorySummary(days);
  const offersHistory = useHistoryOffers(days);
  const campaignsHistory = useHistoryCampaigns(days);

  const s = summary.data as HistorySummary | undefined;

  const kpiItems: { eyebrow: string; label: string; value: string | number; variant: KpiVariant }[] = s
    ? [
        { eyebrow: "СПЕНД",       label: "потрачено",     value: formatSpend(s.totals.spend),       variant: "default" },
        { eyebrow: "ПОКАЗЫ",      label: "impressions",   value: formatInt(s.totals.impressions),   variant: "default" },
        { eyebrow: "КЛИКИ",       label: "переходов",     value: formatInt(s.totals.clicks),        variant: "info" },
        { eyebrow: "ЛИДЫ",        label: "всего",         value: formatInt(s.totals.leads),         variant: "ok" },
        { eyebrow: "РЕГИСТРАЦИИ", label: "всего",         value: formatInt(s.totals.registrations), variant: "info" },
        { eyebrow: "ДЕПОЗИТЫ",    label: "всего",         value: formatInt(s.totals.deposits),      variant: "ok" },
      ]
    : [];

  return (
    <div>
      <MiniHeader eyebrowNum="03" eyebrow="HISTORY · АРХИВ" title="История" />

      {/* pill-переключатель периода */}
      <div className="flex gap-2 px-4 py-3">
        {PERIODS.map((p) => (
          <button
            key={p.days}
            type="button"
            onClick={() => setDays(p.days)}
            className={cn(
              "min-h-[36px] px-4 text-[12px] border",
              p.days === days ? "bg-accent text-bg-0 border-accent" : "text-bg-9 border-bg-5",
            )}
          >
            {p.label}
          </button>
        ))}
      </div>

      <div className="p-4 flex flex-col gap-5">

        {/* KPI-сетка */}
        {summary.isLoading && (
          <div className="grid grid-cols-2 gap-px">
            {Array.from({ length: 6 }, (_, i) => <Skeleton key={i} className="h-20" />)}
          </div>
        )}
        {!summary.isLoading && !summary.isError && s && (
          <div className="grid grid-cols-2 gap-px bg-bg-5">
            {kpiItems.map((item) => (
              <KpiPlate
                key={item.eyebrow}
                eyebrow={item.eyebrow}
                label={item.label}
                value={item.value}
                variant={item.variant}
              />
            ))}
          </div>
        )}
        {!summary.isLoading && !summary.isError && !s && (
          <EmptyState title="Событий нет" description={`За ${days} дней активности не зафиксировано`} />
        )}

        {/* ПО STAGE */}
        {s && (
          <section>
            <div className="divide-y">
              <MetaRow label="Warning-алертов" value={s.alerts.warning_count} />
              <MetaRow label="Stop-алертов"    value={s.alerts.stop_count} />
              <MetaRow label="Disable завершено" value={s.tasks.disable_completed} />
              <MetaRow label="Disable ошибок"    value={s.tasks.disable_failed} />
              <MetaRow label="Enable завершено"  value={s.tasks.enable_completed} />
            </div>
          </section>
        )}

        {/* ПО ПРАВИЛУ */}
        {s && s.alerts.by_rule.length > 0 && (
          <section>
            <div className="divide-y">
              {s.alerts.by_rule.map((r) => (
                <MetaRow key={r.rule_code} label={ruleCodeLabel(r.rule_code, true)} value={r.count} />
              ))}
            </div>
          </section>
        )}

        {/* Офферы */}
        {offersHistory.isLoading && <Skeleton className="h-16" />}
        {!offersHistory.isLoading && (offersHistory.data ?? []).length === 0 && (
          <EmptyState title="Событий нет" description={`За ${days} дней данных нет`} />
        )}
        {!offersHistory.isLoading &&
          (offersHistory.data ?? []).map((o: HistoryOffer) => (
            <div key={o.offer_id} className="flex justify-between">
              <span>{o.offer_code}</span>
              <span>{formatSpend(o.spend)}</span>
            </div>
          ))}

        {/* Кампании */}
        {campaignsHistory.isLoading && <Skeleton className="h-16" />}
        {!campaignsHistory.isLoading && (campaignsHistory.data ?? []).length === 0 && (
          <EmptyState title="Событий нет" description={`За ${days} дней данных нет`} />
        )}
        {!campaignsHistory.isLoading &&
          (campaignsHistory.data ?? []).map((c: HistoryCampaign) => (
            <div key={c.campaign_id} className="flex justify-between">
              <span>{c.campaign_name ?? c.campaign_id}</span>
              <span>{formatSpend(c.spend)}</span>
            </div>
          ))}

      </div>
    </div>
  );
}

const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });

export default function HistoryTestWrapper() {
  return (
    <QueryClientProvider client={qc}>
      <TestHistoryPage />
    </QueryClientProvider>
  );
}
