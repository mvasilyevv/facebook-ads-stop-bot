/**
 * HistoryPage — история заливов за период.
 * Переключатель 7/30/90 дней, KPI-плитки, таблицы по офферам и кампаниям.
 */
import { useState } from "react";
import { createFileRoute } from "@tanstack/react-router";
import { MiniHeader } from "@/components/layout/MiniHeader";
import {
  KpiPlate,
  Card,
  Skeleton,
  ErrorState,
  EmptyState,
  Tabs,
} from "@/components/ui";
import {
  useHistorySummary,
  useHistoryOffers,
  useHistoryCampaigns,
} from "@/lib/api";
import { formatSpend, formatInt } from "@fb/shared";

export const Route = createFileRoute("/history/")({
  component: HistoryPage,
});

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

function HistoryPage() {
  const [days, setDays] = useState(7);
  const [section, setSection] = useState("summary");

  const summary = useHistorySummary(days);
  const offersHistory = useHistoryOffers(days);
  const campaignsHistory = useHistoryCampaigns(days);

  const isLoading = summary.isLoading;
  const isError = summary.isError;

  const s = summary.data;

  function handlePeriodChange(key: string) {
    setDays(Number(key));
  }

  return (
    <div className="flex flex-col min-h-full pb-20">
      <MiniHeader eyebrow="Аналитика" title="История" />

      {/* Переключатель периода */}
      <Tabs
        items={PERIOD_TABS}
        active={String(days)}
        onChange={handlePeriodChange}
        className="bg-[var(--color-bg-0)]"
      />

      {/* Переключатель секции */}
      <Tabs
        items={SECTION_TABS}
        active={section}
        onChange={setSection}
        className="bg-[var(--color-bg-0)]"
      />

      <div className="p-4 flex flex-col gap-4">
        {/* Секция: Сводка */}
        {section === "summary" && (
          <>
            {isLoading && (
              <div className="grid grid-cols-2 gap-2">
                {Array.from({ length: 6 }, (_, i) => (
                  <Skeleton key={i} className="h-20" />
                ))}
              </div>
            )}
            {isError && (
              <ErrorState
                message="Не удалось загрузить историю"
                onRetry={() => void summary.refetch()}
              />
            )}
            {!isLoading && !isError && s && (
              <>
                <div className="grid grid-cols-2 gap-2">
                  <KpiPlate
                    eyebrow="Спенд"
                    label="Всего потрачено"
                    value={formatSpend(s.totals.spend)}
                    variant="default"
                  />
                  <KpiPlate
                    eyebrow="Лиды"
                    label="Всего лидов"
                    value={formatInt(s.totals.leads)}
                    variant="ok"
                  />
                  <KpiPlate
                    eyebrow="Регистрации"
                    label="Всего регистраций"
                    value={formatInt(s.totals.registrations)}
                    variant="info"
                  />
                  <KpiPlate
                    eyebrow="Депозиты"
                    label="Всего депозитов"
                    value={formatInt(s.totals.deposits)}
                    variant="ok"
                  />
                  <KpiPlate
                    eyebrow="Предупреждения"
                    label="Warning-алертов"
                    value={s.alerts.warning_count}
                    variant="warn"
                  />
                  <KpiPlate
                    eyebrow="Стопы"
                    label="Stop-алертов"
                    value={s.alerts.stop_count}
                    variant="stop"
                  />
                </div>

                {/* Топ правил */}
                {s.alerts.by_rule && s.alerts.by_rule.length > 0 && (
                  <Card eyebrow="Правила" title="Топ нарушений">
                    <div className="flex flex-col divide-y divide-[var(--color-bg-4)]">
                      {s.alerts.by_rule.map((r) => (
                        <div
                          key={r.rule_code}
                          className="flex items-center justify-between py-2 gap-2"
                        >
                          <span className="text-[12px] font-mono text-[var(--color-bg-9)] truncate">
                            {r.rule_code}
                          </span>
                          <span className="text-[13px] font-semibold text-[var(--color-bg-11)] tabular-nums shrink-0">
                            {r.count}
                          </span>
                        </div>
                      ))}
                    </div>
                  </Card>
                )}
              </>
            )}
            {!isLoading && !isError && !s && (
              <EmptyState
                title="Нет данных"
                description={`История за ${days} дней пуста`}
              />
            )}
          </>
        )}

        {/* Секция: Офферы */}
        {section === "offers" && (
          <>
            {offersHistory.isLoading && (
              <div className="flex flex-col gap-2">
                {Array.from({ length: 4 }, (_, i) => <Skeleton key={i} className="h-16" />)}
              </div>
            )}
            {offersHistory.isError && (
              <ErrorState
                message="Не удалось загрузить офферы"
                onRetry={() => void offersHistory.refetch()}
              />
            )}
            {!offersHistory.isLoading &&
              !offersHistory.isError &&
              (offersHistory.data ?? []).length === 0 && (
                <EmptyState
                  title="Нет данных по офферам"
                  description={`За ${days} дней активности не зафиксировано`}
                />
              )}
            {!offersHistory.isLoading &&
              !offersHistory.isError &&
              (offersHistory.data ?? []).map((o) => (
                <Card key={o.offer_id} padding="sm">
                  <div className="flex items-start justify-between gap-2">
                    <div className="flex-1 min-w-0">
                      <p className="text-[13px] font-semibold text-[var(--color-bg-11)] font-mono">
                        {o.offer_code}
                      </p>
                      <p className="text-[11px] text-[var(--color-bg-9)] mt-0.5">
                        {o.offer_name}
                      </p>
                    </div>
                    <div className="text-right shrink-0">
                      <p className="text-[14px] font-semibold text-[var(--color-bg-11)] tabular-nums font-display">
                        {formatSpend(o.spend)}
                      </p>
                      <p className="text-[11px] text-[var(--color-bg-8)]">
                        {formatInt(o.leads)} л
                        {o.deposits != null && ` · ${formatInt(o.deposits)} д`}
                      </p>
                    </div>
                  </div>
                </Card>
              ))}
          </>
        )}

        {/* Секция: Кампании */}
        {section === "campaigns" && (
          <>
            {campaignsHistory.isLoading && (
              <div className="flex flex-col gap-2">
                {Array.from({ length: 4 }, (_, i) => <Skeleton key={i} className="h-16" />)}
              </div>
            )}
            {campaignsHistory.isError && (
              <ErrorState
                message="Не удалось загрузить кампании"
                onRetry={() => void campaignsHistory.refetch()}
              />
            )}
            {!campaignsHistory.isLoading &&
              !campaignsHistory.isError &&
              (campaignsHistory.data ?? []).length === 0 && (
                <EmptyState
                  title="Нет данных по кампаниям"
                  description={`За ${days} дней активности не зафиксировано`}
                />
              )}
            {!campaignsHistory.isLoading &&
              !campaignsHistory.isError &&
              (campaignsHistory.data ?? []).map((c) => (
                <Card key={c.campaign_id} padding="sm">
                  <div className="flex items-start justify-between gap-2">
                    <div className="flex-1 min-w-0">
                      <p className="text-[12px] font-semibold text-[var(--color-bg-11)] truncate">
                        {c.campaign_name ?? c.campaign_id}
                      </p>
                      {c.offer_code && (
                        <p className="text-[11px] text-[var(--color-bg-9)] font-mono mt-0.5">
                          {c.offer_code}
                        </p>
                      )}
                    </div>
                    <div className="text-right shrink-0">
                      <p className="text-[14px] font-semibold text-[var(--color-bg-11)] tabular-nums font-display">
                        {formatSpend(c.spend)}
                      </p>
                      <p className="text-[11px] text-[var(--color-bg-8)]">
                        {formatInt(c.leads)} л
                      </p>
                    </div>
                  </div>
                </Card>
              ))}
          </>
        )}
      </div>
    </div>
  );
}
