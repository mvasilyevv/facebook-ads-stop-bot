import { Fragment, useState, type ReactNode } from "react";
import { Link } from "@tanstack/react-router";
import { ChevronDown, ChevronRight, ExternalLink } from "lucide-react";

import {
  ANALYTICS_PRESETS,
  analyticsColumnsForPreset,
  analyticsMetricsForRow,
  type AnalyticsMetricView,
} from "@fb/shared/analytics/presentation";
import type { AnalyticsPreset } from "@fb/shared/analytics/routeState";
import type { AnalyticsPerformanceRow } from "@fb/shared";
import type { DataState } from "@fb/shared/operator/contracts";
import { inheritAnalyticsState } from "@fb/shared/analytics/state";
import { DATA_STATE_DESCRIPTION, DATA_STATE_LABEL } from "@fb/shared/operator/viewModel";
import { DataStateBadge } from "@fb/operator-ui";

import { Skeleton } from "@/components/ui/Skeleton";
import { useAnalyticsPerformance, type AnalyticsPerformanceParams } from "@/lib/api/analytics";

interface PerformanceTableProps {
  rows?: AnalyticsPerformanceRow[];
  loading?: boolean;
  parentState?: DataState;
  currency: string | null;
  params: AnalyticsPerformanceParams;
  preset: AnalyticsPreset;
  onPreset: (preset: AnalyticsPreset) => void;
  onSort: (sort: NonNullable<AnalyticsPerformanceParams["sort"]>) => void;
}

export function PerformanceTable({
  rows,
  loading = false,
  parentState = "ready",
  currency,
  params,
  preset,
  onPreset,
  onSort,
}: PerformanceTableProps) {
  const columns = analyticsColumnsForPreset(preset);

  return (
    <div data-testid="analytics-performance-table">
      <div
        className="flex gap-2 overflow-x-auto border-b border-[var(--color-hairline)] px-4 py-3"
        role="group"
        aria-label="Набор колонок аналитики"
      >
        {ANALYTICS_PRESETS.map((item) => (
          <button
            key={item.value}
            type="button"
            aria-pressed={preset === item.value}
            onClick={() => onPreset(item.value)}
            className={`min-h-11 shrink-0 rounded-full border px-4 text-[14px] font-semibold ${
              preset === item.value
                ? "border-accent bg-accent-bg text-accent"
                : "border-[var(--color-hairline-strong)] text-bg-9"
            }`}
          >
            {item.label}
          </button>
        ))}
      </div>

      <div className="hidden overflow-x-auto md:block">
        <table className="w-full min-w-[940px] border-collapse text-[14px]">
          <caption className="sr-only">Результаты по кампаниям, адсетам и объявлениям</caption>
          <thead>
            <tr className="border-b border-[var(--color-hairline-strong)] text-left font-display uppercase tracking-[0.06em] text-bg-8">
              <Header className="sticky left-0 z-10 min-w-[280px] bg-bg-1">Объект</Header>
              {columns.map((column) =>
                column.sort ? (
                  <Sortable
                    key={column.key}
                    label={column.label}
                    value={column.sort}
                    active={params.sort === column.sort}
                    direction={params.direction}
                    onSort={onSort}
                  />
                ) : (
                  <Header key={column.key}>{column.label}</Header>
                ),
              )}
            </tr>
          </thead>
          <tbody>
            {loading && !rows?.length
              ? Array.from({ length: 6 }, (_, index) => (
                  <tr key={index}>
                    <td colSpan={7} className="p-3">
                      <Skeleton height={36} className="w-full" />
                    </td>
                  </tr>
                ))
              : rows?.map((row) => (
                  <ExpandableRow
                    key={`${row.level}:${row.id}`}
                    row={row}
                    params={params}
                    depth={0}
                    preset={preset}
                    parentState={parentState}
                    currency={currency}
                  />
                ))}
          </tbody>
        </table>
      </div>

      <div className="grid gap-3 p-3 md:hidden">
        {loading && !rows?.length
          ? Array.from({ length: 4 }, (_, index) => (
              <Skeleton key={index} height={180} className="w-full" />
            ))
          : rows?.map((row) => (
              <MobileExpandableRow
                key={`${row.level}:${row.id}`}
                row={row}
                params={params}
                depth={0}
                preset={preset}
                parentState={parentState}
                currency={currency}
              />
            ))}
      </div>

      {!loading && !rows?.length ? (
        <div className="px-5 py-12 text-center text-[14px] text-bg-8">
          <div className="mb-2 flex justify-center">
            <DataStateBadge state={parentState === "ready" ? "unavailable" : parentState} />
          </div>
          {parentState === "empty"
            ? "Сервер подтвердил: в выбранном окне строк нет"
            : `${DATA_STATE_LABEL[parentState === "ready" ? "unavailable" : parentState]}. ${
                DATA_STATE_DESCRIPTION[parentState === "ready" ? "unavailable" : parentState]
              }`}
        </div>
      ) : null}
    </div>
  );
}

function ExpandableRow({
  row,
  params,
  depth,
  preset,
  parentState,
  currency,
}: {
  row: AnalyticsPerformanceRow;
  params: AnalyticsPerformanceParams;
  depth: number;
  preset: AnalyticsPreset;
  parentState: DataState;
  currency: string | null;
}) {
  const effectiveRow = rowWithParentState(row, parentState);
  const tree = useRowTree(effectiveRow, params);
  const childParentState = childStateForQuery(tree.query, effectiveRow.state);
  return (
    <Fragment>
      <tr className="group border-b border-[var(--color-hairline)] hover:bg-bg-2/70">
        <th
          scope="row"
          className="sticky left-0 z-[1] bg-bg-1 px-3 py-2 text-left font-normal group-hover:bg-bg-2"
        >
          <ObjectCell
            row={effectiveRow}
            depth={depth}
            expanded={tree.expanded}
            canExpand={tree.canExpand}
            onToggle={tree.toggle}
          />
        </th>
        {visibleMetricsFor(effectiveRow, preset, currency).map((metric) => (
          <td
            key={metric.key}
            className={`whitespace-nowrap px-3 py-3 text-right font-display tabular-nums ${toneClass(metric.tone)}`}
          >
            {metric.value}
          </td>
        ))}
      </tr>
      {tree.expanded && tree.canExpand ? (
        tree.query.isLoading && !tree.query.data ? (
          <tr>
            <td colSpan={7} className="px-10 py-2">
              <Skeleton height={36} className="w-full" />
            </td>
          </tr>
        ) : (
          tree.query.data?.rows.map((child) => (
            <ExpandableRow
              key={`${child.level}:${child.id}`}
              row={child}
              params={params}
              depth={depth + 1}
              preset={preset}
              parentState={childParentState}
              currency={childCurrency(tree.query.data?.scope, currency)}
            />
          ))
        )
      ) : null}
    </Fragment>
  );
}

function MobileExpandableRow({
  row,
  params,
  depth,
  preset,
  parentState,
  currency,
}: {
  row: AnalyticsPerformanceRow;
  params: AnalyticsPerformanceParams;
  depth: number;
  preset: AnalyticsPreset;
  parentState: DataState;
  currency: string | null;
}) {
  const effectiveRow = rowWithParentState(row, parentState);
  const tree = useRowTree(effectiveRow, params);
  const childParentState = childStateForQuery(tree.query, effectiveRow.state);
  return (
    <div style={{ marginLeft: depth * 12 }}>
      <article className="rounded-[var(--radius-3)] border border-[var(--color-hairline)] bg-bg-1 p-4">
        <ObjectCell
          row={effectiveRow}
          depth={0}
          expanded={tree.expanded}
          canExpand={tree.canExpand}
          onToggle={tree.toggle}
        />
        <dl className="mt-4 grid grid-cols-2 gap-x-4 gap-y-5">
          {visibleMetricsFor(effectiveRow, preset, currency).map((metric) => (
            <div key={metric.key}>
              <dt className="text-[12px] text-bg-8">{metric.label}</dt>
              <dd
                className={`mt-1 font-display text-[16px] tabular-nums ${toneClass(metric.tone)}`}
              >
                {metric.value}
              </dd>
            </div>
          ))}
        </dl>
      </article>
      {tree.expanded && tree.canExpand ? (
        <div className="mt-3 grid gap-3">
          {tree.query.isLoading && !tree.query.data ? (
            <Skeleton height={140} className="w-full" />
          ) : (
            tree.query.data?.rows.map((child) => (
              <MobileExpandableRow
                key={`${child.level}:${child.id}`}
                row={child}
                params={params}
                depth={depth + 1}
                preset={preset}
                parentState={childParentState}
                currency={childCurrency(tree.query.data?.scope, currency)}
              />
            ))
          )}
        </div>
      ) : null}
    </div>
  );
}

function useRowTree(row: AnalyticsPerformanceRow, params: AnalyticsPerformanceParams) {
  const [expanded, setExpanded] = useState(false);
  const childLevel = row.level === "campaign" ? "adset" : row.level === "adset" ? "ad" : null;
  const query = useAnalyticsPerformance(
    { ...params, level: childLevel ?? "ad", parent_id: row.id, page: 1, page_size: 200 },
    expanded && childLevel !== null,
  );
  return {
    expanded,
    canExpand: row.has_children && childLevel !== null,
    toggle: () => setExpanded((value) => !value),
    query,
  };
}

function rowWithParentState(
  row: AnalyticsPerformanceRow,
  parentState: DataState,
): AnalyticsPerformanceRow {
  const state = inheritAnalyticsState(row.state, parentState);
  if (state === row.state) return row;
  return {
    ...row,
    state,
    issues: [DATA_STATE_DESCRIPTION[state], ...row.issues],
  };
}

function childStateForQuery(
  query: ReturnType<typeof useRowTree>["query"],
  parentState: DataState,
): DataState {
  if (query.isError) return "unavailable";
  if (query.isPlaceholderData) {
    return inheritAnalyticsState("stale", parentState);
  }
  if (query.data) {
    return inheritAnalyticsState(query.data.state, parentState);
  }
  return parentState;
}

function ObjectCell({
  row,
  depth,
  expanded,
  canExpand,
  onToggle,
}: {
  row: AnalyticsPerformanceRow;
  depth: number;
  expanded: boolean;
  canExpand: boolean;
  onToggle: () => void;
}) {
  return (
    <div className="flex min-w-0 items-center gap-2" style={{ paddingLeft: depth * 18 }}>
      {canExpand ? (
        <button
          type="button"
          onClick={onToggle}
          aria-expanded={expanded}
          aria-label={expanded ? `Свернуть ${row.name}` : `Раскрыть ${row.name}`}
          className="flex size-11 shrink-0 items-center justify-center rounded-[var(--radius-2)] text-bg-8 outline-none hover:bg-bg-3 focus-visible:ring-2 focus-visible:ring-accent"
        >
          {expanded ? (
            <ChevronDown aria-hidden="true" size={18} />
          ) : (
            <ChevronRight aria-hidden="true" size={18} />
          )}
        </button>
      ) : (
        <span className="size-11 shrink-0" />
      )}
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1">
          <span className="min-w-0 truncate text-[14px] font-medium text-bg-11" title={row.name}>
            {row.name}
          </span>
          {row.level === "ad" && row.fb_id ? (
            <Link
              to="/ads/$fbAdId"
              params={{ fbAdId: row.fb_id }}
              aria-label={`Открыть объявление ${row.name}`}
              className="inline-flex size-11 shrink-0 items-center justify-center rounded-[var(--radius-2)] text-bg-8 outline-none hover:text-accent focus-visible:ring-2 focus-visible:ring-accent"
            >
              <ExternalLink aria-hidden="true" size={16} />
            </Link>
          ) : null}
          {row.state !== "ready" ? (
            <span title={row.issues.join(". ") || undefined}>
              <DataStateBadge state={row.state} />
            </span>
          ) : null}
        </div>
        <div className="mt-1 flex flex-wrap gap-2 font-display text-[12px] uppercase tracking-[0.05em] text-bg-8">
          <span>{row.level}</span>
          {row.offer_code ? <span>{row.offer_code}</span> : null}
          {row.ad_account_id ? <span>{accountLabel(row.ad_account_id)}</span> : null}
        </div>
      </div>
    </div>
  );
}

function visibleMetricsFor(
  row: AnalyticsPerformanceRow,
  preset: AnalyticsPreset,
  currency: string | null,
): AnalyticsMetricView[] {
  const metrics = analyticsMetricsForRow(row, preset, currency);
  if (row.state === "unavailable") {
    return metrics.map((metric) => ({ ...metric, value: "—", tone: undefined }));
  }
  return row.state === "ready"
    ? metrics
    : metrics.map((metric) => ({ ...metric, tone: undefined }));
}

function Header({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <th scope="col" className={`whitespace-nowrap px-3 py-3 text-[12px] font-medium ${className}`}>
      {children}
    </th>
  );
}

function Sortable({
  label,
  value,
  active,
  direction,
  onSort,
}: {
  label: string;
  value: NonNullable<AnalyticsPerformanceParams["sort"]>;
  active: boolean;
  direction?: "asc" | "desc";
  onSort: PerformanceTableProps["onSort"];
}) {
  return (
    <Header>
      <button
        type="button"
        onClick={() => onSort(value)}
        aria-label={`Сортировать: ${label}`}
        className="min-h-11 rounded-sm px-1 outline-none hover:text-bg-11 focus-visible:ring-2 focus-visible:ring-accent"
      >
        <span aria-hidden="true">
          {label}
          {active ? (direction === "asc" ? " ↑" : " ↓") : ""}
        </span>
      </button>
    </Header>
  );
}

function accountLabel(value: string): string {
  return value.startsWith("act_") ? value : `act_${value}`;
}
function childCurrency(
  scope: { currency_state: string; currency?: string | null } | undefined,
  parentCurrency: string | null,
): string | null {
  const candidate = scope?.currency_state === "single" && scope.currency === "USD" ? "USD" : null;
  return candidate === parentCurrency ? candidate : null;
}
function toneClass(tone?: AnalyticsMetricView["tone"]): string {
  return tone === "danger"
    ? "text-danger"
    : tone === "success"
      ? "text-success"
      : tone === "accent"
        ? "text-accent"
        : "text-bg-10";
}
