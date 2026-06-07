/**
 * HistoryPage — история/архив за выбранный период.
 * Шапка MiniHeader → pill-переключатель периода → KPI-сетка (summary) →
 * блок ПО STAGE → блок ПО ПРАВИЛУ → секции кампаний / офферов.
 */
import { useState } from "react";
import { createFileRoute } from "@tanstack/react-router";
import { MiniHeader } from "@/components/layout/MiniHeader";
import { Eyebrow } from "@/components/data/Eyebrow";
import { KpiPlate, Skeleton, EmptyState } from "@/components/ui";
import type { KpiVariant } from "@/components/ui";
import {
  useHistorySummary,
  useHistoryOffers,
  useHistoryCampaigns,
} from "@/lib/api";
import { formatSpend, formatInt, ruleCodeLabel } from "@fb/shared";
import type { HistoryCampaign, HistoryOffer } from "@fb/shared";
import { haptic } from "@/lib/tg";
import { cn } from "@/lib/cn";

export const Route = createFileRoute("/history/")({
  component: HistoryPage,
});

// ─── Периоды ─────────────────────────────────────────────────────────────────

const PERIODS: { days: number; label: string }[] = [
  { days: 7,  label: "7д" },
  { days: 30, label: "30д" },
  { days: 90, label: "90д" },
];

// ─── Утилиты ────────────────────────────────────────────────────────────────

/** Секция-обёртка с eyebrow и gap-px-сеткой детей. */
function Section({
  num,
  title,
  children,
}: {
  num?: string;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section>
      <Eyebrow num={num} className="mb-2.5 flex">
        {title}
      </Eyebrow>
      {children}
    </section>
  );
}

/** Строка в блоке «по правилу» / «по stage». */
function MetaRow({
  label,
  value,
  color,
}: {
  label: string;
  value: string | number;
  color?: string;
}) {
  return (
    <div className="flex items-center justify-between px-3.5 py-2.5 min-h-[44px] gap-2">
      <span className="text-[13px] font-display text-bg-10 truncate">{label}</span>
      <span
        className="font-display tabular-nums text-[15px] text-bg-11 shrink-0"
        style={color ? { color } : undefined}
      >
        {value}
      </span>
    </div>
  );
}

// ─── Pill-переключатель периода ───────────────────────────────────────────────

function PeriodPills({
  days,
  onChange,
}: {
  days: number;
  onChange: (d: number) => void;
}) {
  return (
    <div className="flex items-center gap-2 px-4 py-3 border-b border-bg-5">
      {PERIODS.map((p) => {
        const active = p.days === days;
        return (
          <button
            key={p.days}
            type="button"
            onClick={() => {
              haptic.selection();
              onChange(p.days);
            }}
            className={cn(
              "min-h-[36px] px-4 text-[12px] font-display font-semibold uppercase tracking-[0.08em] border transition-colors",
              active
                ? "bg-accent text-bg-0 border-accent"
                : "bg-bg-1 text-bg-9 border-bg-5 hover:text-bg-11",
            )}
          >
            {p.label}
          </button>
        );
      })}
    </div>
  );
}

// ─── KPI-сетка summary ────────────────────────────────────────────────────────

interface SummaryKpiProps {
  spend: string;
  impressions: number;
  clicks: number;
  leads: number;
  registrations: number;
  deposits: number;
}

function SummaryKpiGrid(props: SummaryKpiProps) {
  const items: { eyebrow: string; label: string; value: string | number; variant: KpiVariant }[] = [
    { eyebrow: "СПЕНД",        label: "потрачено",     value: formatSpend(props.spend),       variant: "default" },
    { eyebrow: "ПОКАЗЫ",       label: "impressions",   value: formatInt(props.impressions),   variant: "default" },
    { eyebrow: "КЛИКИ",        label: "переходов",     value: formatInt(props.clicks),        variant: "info" },
    { eyebrow: "ЛИДЫ",         label: "всего",         value: formatInt(props.leads),         variant: "ok" },
    { eyebrow: "РЕГИСТРАЦИИ",  label: "всего",         value: formatInt(props.registrations), variant: "info" },
    { eyebrow: "ДЕПОЗИТЫ",     label: "всего",         value: formatInt(props.deposits),      variant: "ok" },
  ];

  return (
    <div className="grid grid-cols-2 gap-px bg-bg-5">
      {items.map((item) => (
        <KpiPlate
          key={item.eyebrow}
          eyebrow={item.eyebrow}
          label={item.label}
          value={item.value}
          variant={item.variant}
        />
      ))}
    </div>
  );
}

// ─── Строка кампании ──────────────────────────────────────────────────────────

function CampaignRow({ c }: { c: HistoryCampaign }) {
  return (
    <div className="flex items-start justify-between px-3.5 py-3 min-h-[44px] gap-2 border-b border-bg-5 last:border-0">
      <div className="flex-1 min-w-0">
        <p className="font-display text-[13px] text-bg-11 truncate leading-snug">
          {c.campaign_name ?? c.campaign_id}
        </p>
        <div className="flex items-center gap-2 mt-0.5">
          {c.offer_code != null && (
            <span className="font-display text-[11px] text-bg-9 tabular-nums">{c.offer_code}</span>
          )}
          <span className="font-display text-[11px] text-bg-8 tabular-nums">
            {formatInt(c.leads)} л · {formatInt(c.registrations)} р · {formatInt(c.deposits)} д
          </span>
        </div>
      </div>
      <div className="shrink-0 text-right">
        <p className="font-display tabular-nums text-[15px] text-bg-11 leading-snug">
          {formatSpend(c.spend)}
        </p>
        {c.cost_per_lead != null && (
          <p className="font-display tabular-nums text-[11px] text-bg-8">
            CPL {formatSpend(c.cost_per_lead)}
          </p>
        )}
      </div>
    </div>
  );
}

// ─── Строка оффера ────────────────────────────────────────────────────────────

function OfferRow({ o }: { o: HistoryOffer }) {
  return (
    <div className="flex items-start justify-between px-3.5 py-3 min-h-[44px] gap-2 border-b border-bg-5 last:border-0">
      <div className="flex-1 min-w-0">
        <p className="font-display text-[13px] text-bg-11 tabular-nums leading-snug">{o.offer_code}</p>
        <p className="text-[11px] text-bg-9 mt-0.5 truncate">{o.offer_name}</p>
        <span className="font-display text-[11px] text-bg-8 tabular-nums">
          {formatInt(o.leads)} л · {formatInt(o.registrations)} р · {formatInt(o.deposits)} д
        </span>
      </div>
      <div className="shrink-0 text-right">
        <p className="font-display tabular-nums text-[15px] text-bg-11 leading-snug">
          {formatSpend(o.spend)}
        </p>
        {o.cost_per_lead != null && (
          <p className="font-display tabular-nums text-[11px] text-bg-8">
            CPL {formatSpend(o.cost_per_lead)}
          </p>
        )}
      </div>
    </div>
  );
}

// ─── Скелетон-строки ─────────────────────────────────────────────────────────

function RowSkeletons({ count = 3 }: { count?: number }) {
  return (
    <div className="flex flex-col gap-px bg-bg-5">
      {Array.from({ length: count }, (_, i) => (
        <div key={i} className="bg-bg-1 px-3.5 py-3 flex justify-between gap-2">
          <div className="flex-1 space-y-1.5">
            <Skeleton className="h-4 w-3/4" />
            <Skeleton className="h-3 w-1/2" />
          </div>
          <Skeleton className="h-5 w-16 shrink-0" />
        </div>
      ))}
    </div>
  );
}

// ─── Главный компонент ────────────────────────────────────────────────────────

function HistoryPage() {
  const [days, setDays] = useState(7);

  const summary = useHistorySummary(days);
  const offersHistory = useHistoryOffers(days);
  const campaignsHistory = useHistoryCampaigns(days);

  const s = summary.data;

  return (
    <div className="flex flex-col min-h-full pb-20">
      {/* ── шапка ── */}
      <MiniHeader eyebrowNum="03" eyebrow="HISTORY · АРХИВ" title="История" />

      {/* ── переключатель периода ── */}
      <PeriodPills days={days} onChange={setDays} />

      <div className="flex flex-col gap-5 p-4">

        {/* ── KPI-сводка ── */}
        <Section num="01" title="ВСЕГО ЗА ПЕРИОД">
          {summary.isLoading ? (
            <div className="grid grid-cols-2 gap-px bg-bg-5">
              {Array.from({ length: 6 }, (_, i) => (
                <div key={i} className="bg-bg-1 p-3 space-y-2">
                  <Skeleton className="h-3 w-14" />
                  <Skeleton className="h-7 w-20" />
                  <Skeleton className="h-3 w-10" />
                </div>
              ))}
            </div>
          ) : summary.isError ? (
            <EmptyState title="Ошибка загрузки" description="Повторите позже" />
          ) : s ? (
            <SummaryKpiGrid
              spend={s.totals.spend}
              impressions={s.totals.impressions}
              clicks={s.totals.clicks}
              leads={s.totals.leads}
              registrations={s.totals.registrations}
              deposits={s.totals.deposits}
            />
          ) : (
            <EmptyState
              title="Событий нет"
              description={`За ${days} дней активности не зафиксировано`}
            />
          )}
        </Section>

        {/* ── ПО STAGE ── */}
        {s && (
          <Section num="02" title="ПО STAGE">
            <div className="border border-bg-5 bg-bg-1 divide-y divide-bg-5">
              <MetaRow
                label="Warning-алертов"
                value={s.alerts.warning_count}
                color={s.alerts.warning_count > 0 ? "var(--warning)" : undefined}
              />
              <MetaRow
                label="Stop-алертов"
                value={s.alerts.stop_count}
                color={s.alerts.stop_count > 0 ? "var(--danger)" : undefined}
              />
              <MetaRow label="Disable завершено" value={s.tasks.disable_completed} />
              <MetaRow label="Disable ошибок"    value={s.tasks.disable_failed} />
              <MetaRow label="Enable завершено"  value={s.tasks.enable_completed} />
            </div>
          </Section>
        )}

        {/* ── ПО ПРАВИЛУ ── */}
        {s && s.alerts.by_rule.length > 0 && (
          <Section title="ПО ПРАВИЛУ">
            <div className="border border-bg-5 bg-bg-1 divide-y divide-bg-5">
              {s.alerts.by_rule.map((r) => (
                <MetaRow
                  key={r.rule_code}
                  label={ruleCodeLabel(r.rule_code, true)}
                  value={r.count}
                />
              ))}
            </div>
          </Section>
        )}

        {/* ── Офферы ── */}
        <Section num="03" title="ПО ОФФЕРУ">
          {offersHistory.isLoading ? (
            <RowSkeletons count={3} />
          ) : offersHistory.isError ? (
            <EmptyState title="Ошибка загрузки" description="Повторите позже" />
          ) : (offersHistory.data ?? []).length === 0 ? (
            <EmptyState
              title="Событий нет"
              description={`За ${days} дней данных нет`}
            />
          ) : (
            <div className="border border-bg-5 bg-bg-1">
              {(offersHistory.data ?? []).map((o) => (
                <OfferRow key={o.offer_id} o={o} />
              ))}
            </div>
          )}
        </Section>

        {/* ── Кампании ── */}
        <Section num="04" title="ПО КАМПАНИИ">
          {campaignsHistory.isLoading ? (
            <RowSkeletons count={3} />
          ) : campaignsHistory.isError ? (
            <EmptyState title="Ошибка загрузки" description="Повторите позже" />
          ) : (campaignsHistory.data ?? []).length === 0 ? (
            <EmptyState
              title="Событий нет"
              description={`За ${days} дней данных нет`}
            />
          ) : (
            <div className="border border-bg-5 bg-bg-1">
              {(campaignsHistory.data ?? []).map((c) => (
                <CampaignRow key={c.campaign_id} c={c} />
              ))}
            </div>
          )}
        </Section>

      </div>
    </div>
  );
}
