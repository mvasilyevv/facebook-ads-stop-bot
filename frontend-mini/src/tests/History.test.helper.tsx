/**
 * Helper для теста HistoryPage — экспортирует компонент без createFileRoute-обёртки.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MiniHeader } from "@/components/layout/MiniHeader";
import { KpiPlate, Card, Skeleton, ErrorState, EmptyState, Tabs } from "@/components/ui";
import { useHistorySummary, useHistoryOffers, useHistoryCampaigns } from "@/lib/api";
import { formatSpend, formatInt } from "@fb/shared";
import { useState } from "react";
import type { HistorySummary } from "@fb/shared";

const PERIOD_TABS = [
  { key: "7", label: "7 дней" },
  { key: "30", label: "30 дней" },
  { key: "90", label: "90 дней" },
];

const SECTION_TABS = [
  { key: "summary", label: "Сводка" },
  { key: "offers", label: "Офферы" },
  { key: "campaigns", label: "Кампании" },
];

function TestHistoryPage() {
  const [days, setDays] = useState(7);
  const [section, setSection] = useState("summary");

  const summary = useHistorySummary(days);
  const offersHistory = useHistoryOffers(days);
  const campaignsHistory = useHistoryCampaigns(days);

  const s = summary.data as HistorySummary | undefined;

  return (
    <div>
      <MiniHeader eyebrow="Аналитика" title="История" />
      <Tabs items={PERIOD_TABS} active={String(days)} onChange={(key) => setDays(Number(key))} />
      <Tabs items={SECTION_TABS} active={section} onChange={setSection} />

      <div className="p-4 flex flex-col gap-4">
        {section === "summary" && (
          <>
            {summary.isLoading && (
              <div className="grid grid-cols-2 gap-2">
                {Array.from({ length: 6 }, (_, i) => <Skeleton key={i} className="h-20" />)}
              </div>
            )}
            {summary.isError && (
              <ErrorState message="Не удалось загрузить историю" onRetry={() => void summary.refetch()} />
            )}
            {!summary.isLoading && !summary.isError && s && (
              <>
                <div className="grid grid-cols-2 gap-2">
                  <KpiPlate eyebrow="Спенд" label="Всего потрачено" value={formatSpend(s.totals.spend)} />
                  <KpiPlate eyebrow="Лиды" label="Всего лидов" value={formatInt(s.totals.leads)} variant="ok" />
                  <KpiPlate eyebrow="Предупреждения" label="Warning-алертов" value={s.alerts.warning_count} variant="warn" />
                  <KpiPlate eyebrow="Стопы" label="Stop-алертов" value={s.alerts.stop_count} variant="stop" />
                </div>
                {s.alerts.by_rule && s.alerts.by_rule.length > 0 && (
                  <Card eyebrow="Правила" title="Топ нарушений">
                    {s.alerts.by_rule.map((r) => (
                      <div key={r.rule_code} className="flex justify-between py-2">
                        <span>{r.rule_code}</span>
                        <span>{r.count}</span>
                      </div>
                    ))}
                  </Card>
                )}
              </>
            )}
            {!summary.isLoading && !summary.isError && !s && (
              <EmptyState title="Нет данных" description={`История за ${days} дней пуста`} />
            )}
          </>
        )}
        {section === "offers" && (
          <div>
            {offersHistory.isLoading && <Skeleton className="h-16" />}
            {!offersHistory.isLoading && (offersHistory.data ?? []).length === 0 && (
              <EmptyState title="Нет данных по офферам" />
            )}
          </div>
        )}
        {section === "campaigns" && (
          <div>
            {campaignsHistory.isLoading && <Skeleton className="h-16" />}
            {!campaignsHistory.isLoading && (campaignsHistory.data ?? []).length === 0 && (
              <EmptyState title="Нет данных по кампаниям" />
            )}
          </div>
        )}
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
