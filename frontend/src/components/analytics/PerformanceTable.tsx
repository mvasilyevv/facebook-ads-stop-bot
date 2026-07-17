import { Fragment, useState } from "react";
import { Link } from "@tanstack/react-router";
import { ChevronDown, ChevronRight, ExternalLink } from "lucide-react";
import type { AnalyticsPerformanceRow } from "@fb/shared";

import { useAnalyticsPerformance, type AnalyticsPerformanceParams } from "@/lib/api/analytics";
import { Skeleton } from "@/components/ui/Skeleton";

const money = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });
const count = new Intl.NumberFormat("ru-RU");

interface PerformanceTableProps {
  rows?: AnalyticsPerformanceRow[];
  loading?: boolean;
  params: AnalyticsPerformanceParams;
  onSort: (sort: NonNullable<AnalyticsPerformanceParams["sort"]>) => void;
}

export function PerformanceTable({ rows, loading = false, params, onSort }: PerformanceTableProps) {
  return (
    <div className="overflow-x-auto" data-testid="analytics-performance-table">
      <table className="w-full min-w-[1840px] border-collapse text-[11px]">
        <thead>
          <tr className="border-b border-[var(--hairline-strong)] text-left font-display uppercase tracking-[0.06em] text-bg-8">
            <Header className="sticky left-0 z-10 min-w-[290px] bg-bg-1">Объект</Header>
            <Sortable label="Spend" value="spend" onSort={onSort} />
            <Header>Impr.</Header>
            <Sortable label="Clicks" value="clicks" onSort={onSort} />
            <Header>CPC</Header>
            <Header>CTR</Header>
            <Sortable label="Рег." value="registrations" onSort={onSort} />
            <Sortable label="FTD" value="ftds" onSort={onSort} />
            <Sortable label="Dep." value="confirmed_deposits" onSort={onSort} />
            <Header>Redep.</Header>
            <Sortable label="Revenue" value="revenue" onSort={onSort} />
            <Header>Click→Reg</Header>
            <Header>Reg→FTD</Header>
            <Header>Cost reg</Header>
            <Header>Cost FTD</Header>
            <Header>ROI</Header>
            <Header>ROAS</Header>
            <Header>Base</Header>
            <Sortable label="Δ base" value="base_delta" onSort={onSort} />
          </tr>
        </thead>
        <tbody>
          {loading && !rows?.length
            ? Array.from({ length: 7 }, (_, index) => (
                <tr key={index} className="border-b border-[var(--hairline)]">
                  <td colSpan={19} className="p-2">
                    <Skeleton height={28} className="w-full" />
                  </td>
                </tr>
              ))
            : rows?.map((row) => (
                <ExpandableRow key={`${row.level}:${row.id}`} row={row} params={params} depth={0} />
              ))}
        </tbody>
      </table>
      {!loading && !rows?.length ? (
        <div className="px-5 py-12 text-center text-[12px] text-bg-8">
          В выбранном окне нет Meta-метрик или Tracker-событий
        </div>
      ) : null}
    </div>
  );
}

function ExpandableRow({
  row,
  params,
  depth,
}: {
  row: AnalyticsPerformanceRow;
  params: AnalyticsPerformanceParams;
  depth: number;
}) {
  const [expanded, setExpanded] = useState(false);
  const childLevel = row.level === "campaign" ? "adset" : row.level === "adset" ? "ad" : null;
  const childQuery = useAnalyticsPerformance(
    {
      ...params,
      level: childLevel ?? "ad",
      parent_id: row.id,
      page: 1,
      page_size: 200,
    },
    expanded && childLevel !== null,
  );
  const budget = row.live_budget;
  const delta = budget ? Number(budget.base_delta) : null;

  return (
    <Fragment>
      <tr className="group border-b border-[var(--hairline)] hover:bg-bg-2/70">
        <td className="sticky left-0 z-[1] bg-bg-1 px-3 py-2.5 group-hover:bg-bg-2">
          <div className="flex min-w-0 items-center gap-2" style={{ paddingLeft: depth * 18 }}>
            {row.has_children && childLevel ? (
              <button
                type="button"
                onClick={() => setExpanded((value) => !value)}
                aria-label={expanded ? "Свернуть" : "Раскрыть"}
                className="flex size-6 shrink-0 items-center justify-center rounded-[var(--radius-1)] text-bg-8 hover:bg-bg-3 hover:text-bg-11"
              >
                {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
              </button>
            ) : (
              <span className="size-6 shrink-0" />
            )}
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <span className="truncate text-[12px] font-medium text-bg-11" title={row.name}>
                  {row.name}
                </span>
                {row.level === "ad" && row.fb_id ? (
                  <Link
                    to="/ads/$fbAdId"
                    params={{ fbAdId: row.fb_id }}
                    aria-label="Открыть объявление"
                    className="text-bg-7 hover:text-accent"
                  >
                    <ExternalLink size={12} />
                  </Link>
                ) : null}
              </div>
              <div className="mt-0.5 flex gap-2 font-display text-[9px] uppercase tracking-[0.06em] text-bg-7">
                <span>{row.level}</span>
                {row.offer_code ? <span>{row.offer_code}</span> : null}
                {row.ad_account_id ? <span>act_{row.ad_account_id}</span> : null}
              </div>
            </div>
          </div>
        </td>
        <MoneyCell value={row.spend} />
        <CountCell value={row.impressions} />
        <CountCell value={row.clicks} />
        <MoneyCell value={row.cpc} />
        <PercentCell value={row.ctr_pct} />
        <CountCell value={row.registrations} accent />
        <CountCell value={row.ftds} accent />
        <CountCell value={row.confirmed_deposits} accent />
        <CountCell value={row.redeposits} />
        <MoneyCell value={row.revenue} />
        <PercentCell value={row.click_registration_cr_pct} />
        <PercentCell value={row.registration_ftd_cr_pct} />
        <MoneyCell value={row.cost_per_registration} />
        <MoneyCell value={row.cost_per_ftd} />
        <PercentCell value={row.roi_pct} signed />
        <NumberCell value={row.roas} suffix="×" />
        <MoneyCell value={budget?.base_budget} reason={row.budget_unavailable_reason} />
        <td
          className={`px-3 py-2.5 text-right font-display tabular-nums ${
            delta == null ? "text-bg-7" : delta > 0 ? "text-danger" : "text-success"
          }`}
          title={
            budget
              ? `Δ stop: ${money.format(Number(budget.stop_delta))}`
              : (row.budget_unavailable_reason ?? undefined)
          }
        >
          {delta == null ? "—" : `${delta > 0 ? "+" : ""}${money.format(delta)}`}
        </td>
      </tr>
      {expanded && childLevel ? (
        childQuery.isLoading && !childQuery.data ? (
          <tr>
            <td colSpan={19} className="px-10 py-2">
              <Skeleton height={28} className="w-full" />
            </td>
          </tr>
        ) : (
          childQuery.data?.rows.map((child) => (
            <ExpandableRow
              key={`${child.level}:${child.id}`}
              row={child}
              params={params}
              depth={depth + 1}
            />
          ))
        )
      ) : null}
    </Fragment>
  );
}

function Header({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return <th className={`whitespace-nowrap px-3 py-2.5 font-medium ${className}`}>{children}</th>;
}

function Sortable({
  label,
  value,
  onSort,
}: {
  label: string;
  value: NonNullable<AnalyticsPerformanceParams["sort"]>;
  onSort: PerformanceTableProps["onSort"];
}) {
  return (
    <Header>
      <button type="button" onClick={() => onSort(value)} className="hover:text-bg-11">
        {label}
      </button>
    </Header>
  );
}

function MoneyCell({ value, reason }: { value?: string | null; reason?: string | null }) {
  const numberValue = value == null ? null : Number(value);
  return (
    <td
      className="whitespace-nowrap px-3 py-2.5 text-right font-display tabular-nums text-bg-10"
      title={reason ?? undefined}
    >
      {numberValue == null || Number.isNaN(numberValue) ? "—" : money.format(numberValue)}
    </td>
  );
}

function CountCell({ value, accent = false }: { value: number; accent?: boolean }) {
  return (
    <td
      className={`px-3 py-2.5 text-right font-display tabular-nums ${accent ? "text-bg-11" : "text-bg-9"}`}
    >
      {count.format(value)}
    </td>
  );
}

function PercentCell({ value, signed = false }: { value?: string | null; signed?: boolean }) {
  const parsed = value == null ? null : Number(value);
  return (
    <td className="px-3 py-2.5 text-right font-display tabular-nums text-bg-9">
      {parsed == null || Number.isNaN(parsed)
        ? "—"
        : `${signed && parsed > 0 ? "+" : ""}${parsed.toFixed(2)}%`}
    </td>
  );
}

function NumberCell({ value, suffix }: { value?: string | null; suffix?: string }) {
  const parsed = value == null ? null : Number(value);
  return (
    <td className="px-3 py-2.5 text-right font-display tabular-nums text-bg-9">
      {parsed == null || Number.isNaN(parsed) ? "—" : `${parsed.toFixed(2)}${suffix ?? ""}`}
    </td>
  );
}
