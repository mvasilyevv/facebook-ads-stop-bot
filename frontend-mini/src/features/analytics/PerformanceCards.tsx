import type { AnalyticsPerformanceRow } from "@fb/shared";
import {
  formatInt,
  formatPercentValue,
  formatSpend,
} from "@fb/shared/format/number";
import { timezoneEvidenceLabel } from "@fb/shared/format/time";
import type { DataState } from "@fb/shared/operator/contracts";
import { inheritAnalyticsState } from "@fb/shared/analytics/state";
import {
  DATA_STATE_DESCRIPTION,
  DATA_STATE_LABEL,
} from "@fb/shared/operator/viewModel";
import { DataStateBadge } from "@fb/operator-ui";

import { cn } from "@/lib/cn";

import type { AnalyticsPeriod } from "./viewModel";

export function PerformanceCards({
  rows,
  parentState,
  period,
  currency,
  onFocusCampaign,
}: {
  rows: AnalyticsPerformanceRow[];
  parentState: DataState;
  period: AnalyticsPeriod;
  currency: string | null;
  onFocusCampaign: (campaignId: string) => void;
}) {
  if (rows.length === 0) {
    const state = parentState === "ready" ? "unavailable" : parentState;
    return (
      <div
        role={state === "unavailable" ? "alert" : "status"}
        data-state={state}
        className="rounded-[var(--radius-2)] border border-dashed border-[var(--color-hairline-strong)] px-4 py-7 text-center text-[14px] text-bg-9"
      >
        <div className="mb-2 flex justify-center">
          <DataStateBadge state={state} compact />
        </div>
        {state === "empty"
          ? "Сервер подтвердил, что по выбранным фильтрам строк нет."
          : `${DATA_STATE_LABEL[state]}. ${DATA_STATE_DESCRIPTION[state]}`}
      </div>
    );
  }

  return (
    <div className="grid gap-3" data-testid="performance-cards">
      {rows.map((row) => {
        const state = inheritAnalyticsState(row.state, parentState);
        const valuesAvailable = state !== "unavailable";
        const confirmedTone = state === "ready";
        return (
          <article
            key={row.id}
            data-state={state}
            className="overflow-hidden rounded-[var(--radius-3)] border border-[var(--color-hairline)] bg-bg-2"
          >
            <header className="flex items-start justify-between gap-3 border-b border-[var(--color-hairline)] p-4">
              <div className="min-w-0">
                <div className="text-[12px] uppercase tracking-[0.07em] text-bg-8">
                  {row.offer_code || "Без оффера"}
                  {row.ad_account_id ? ` · ${row.ad_account_id}` : ""}
                </div>
                <h3 className="m-0 mt-1 break-words font-display text-[15px] font-semibold leading-5 text-bg-11">
                  {row.name || "Кампания без названия"}
                </h3>
                <div className="mt-1 truncate font-display text-[12px] text-bg-8">
                  {row.fb_id || row.id}
                </div>
              </div>
              <DataStateBadge state={state} compact />
            </header>

            {state !== "ready" ? (
              <div
                role={state === "unavailable" ? "alert" : "status"}
                className={cn(
                  "mx-4 mt-3 rounded-[var(--radius-2)] border-l-[3px] bg-bg-1 px-3 py-2 text-[12px] leading-5 text-bg-9",
                  state === "partial"
                    ? "border border-warning/25 border-l-warning"
                    : state === "unavailable"
                      ? "border border-danger/25 border-l-danger"
                      : "border border-[var(--color-hairline)] border-l-bg-8",
                )}
              >
                <strong className="text-bg-11">
                  {DATA_STATE_LABEL[state]}.
                </strong>{" "}
                {row.issues[0] || DATA_STATE_DESCRIPTION[state]}
              </div>
            ) : null}

            <dl className="grid grid-cols-3 p-2">
              <CardMetric
                label="Расход"
                value={valuesAvailable ? formatSpend(row.spend, currency) : "—"}
              />
              <CardMetric
                label="Клики"
                value={valuesAvailable ? formatInt(row.clicks) : "—"}
              />
              <CardMetric
                label="Рег."
                value={valuesAvailable ? formatInt(row.registrations) : "—"}
                accent={confirmedTone}
              />
              <CardMetric
                label="FTD"
                value={valuesAvailable ? formatInt(row.ftds) : "—"}
                accent={confirmedTone}
              />
              <CardMetric
                label="Цена FTD"
                value={
                  valuesAvailable
                    ? formatSpend(row.cost_per_ftd, currency)
                    : "—"
                }
              />
              <CardMetric
                label="ROI"
                value={valuesAvailable ? formatPercentValue(row.roi_pct) : "—"}
              />
            </dl>

            {period === "today" ? (
              <BudgetEvidence
                row={row}
                valuesAvailable={valuesAvailable}
                confirmedTone={confirmedTone}
                currency={currency}
              />
            ) : null}

            <div className="flex items-center justify-between gap-3 border-t border-[var(--color-hairline)] px-4 py-3">
              <span className="text-[12px] text-bg-8">
                {row.timezone_state === "unknown"
                  ? `${timezoneEvidenceLabel(
                      row.cabinet_timezone,
                      row.timezone_state,
                    )} · оценка`
                  : timezoneEvidenceLabel(
                      row.cabinet_timezone,
                      row.timezone_state,
                    )}
              </span>
              {row.level === "campaign" ? (
                <button
                  type="button"
                  onClick={() => onFocusCampaign(row.id)}
                  className="min-h-11 rounded-[var(--radius-2)] border border-[var(--color-hairline-strong)] bg-bg-1 px-4 text-[13px] font-semibold text-bg-11"
                >
                  Только эта
                </button>
              ) : null}
            </div>
          </article>
        );
      })}
    </div>
  );
}

function CardMetric({
  label,
  value,
  accent = false,
  danger = false,
}: {
  label: string;
  value: string;
  accent?: boolean;
  danger?: boolean;
}) {
  return (
    <div className="min-w-0 rounded-[var(--radius-2)] px-2 py-3">
      <dt className="truncate text-[12px] uppercase tracking-[0.04em] text-bg-8">
        {label}
      </dt>
      <dd
        className={cn(
          "m-0 mt-1 truncate font-display text-[16px] tabular-nums",
          danger ? "text-danger" : accent ? "text-active" : "text-bg-11",
        )}
      >
        {value}
      </dd>
    </div>
  );
}

function BudgetEvidence({
  row,
  valuesAvailable,
  confirmedTone,
  currency,
}: {
  row: AnalyticsPerformanceRow;
  valuesAvailable: boolean;
  confirmedTone: boolean;
  currency: string | null;
}) {
  if (!valuesAvailable) {
    return (
      <div className="mx-4 mb-3 rounded-[var(--radius-2)] bg-bg-1 px-3 py-2 text-[12px] text-bg-9">
        Base / stop: неподтверждённые значения скрыты.
      </div>
    );
  }
  if (!row.live_budget) {
    return (
      <div className="mx-4 mb-3 rounded-[var(--radius-2)] bg-bg-1 px-3 py-2 text-[12px] text-bg-9">
        Base / stop:{" "}
        {row.budget_unavailable_reason || "нет подтверждённого порога"}.
      </div>
    );
  }
  const overStop = Number(row.live_budget.stop_delta) > 0;
  return (
    <dl className="mx-4 mb-3 grid grid-cols-3 rounded-[var(--radius-2)] bg-bg-1 p-2">
      <CardMetric
        label="Base"
        value={formatSpend(row.live_budget.base_budget, currency)}
      />
      <CardMetric
        label="Stop"
        value={formatSpend(row.live_budget.stop_budget, currency)}
      />
      <CardMetric
        label="Δ stop"
        value={signedSpend(row.live_budget.stop_delta, currency)}
        accent={confirmedTone && !overStop}
        danger={confirmedTone && overStop}
      />
    </dl>
  );
}

function signedSpend(
  value: string | null | undefined,
  currency: string | null,
): string {
  if (value == null) return "—";
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return "—";
  const formatted = formatSpend(value, currency);
  return formatted === "—"
    ? formatted
    : `${numeric > 0 ? "+" : ""}${formatted}`;
}
