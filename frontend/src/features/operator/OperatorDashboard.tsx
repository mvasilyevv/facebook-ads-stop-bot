import { lazy, Suspense } from "react";
import { Link } from "@tanstack/react-router";
import { Activity, AlertTriangle, ArrowRight, RefreshCw } from "lucide-react";

import {
  ACTION_STATE_LABEL,
  SEVERITY_LABEL,
  decimalToNumber,
  severityForDataState,
  snapshotForRealtimeState,
  snapshotHeadline,
  snapshotOverviewState,
  workerStatusLabel,
} from "@fb/shared/operator/viewModel";
import { safeOperatorAttentionHref } from "@fb/shared/operator/attentionNavigation";
import { formatSpend } from "@fb/shared/format/number";
import { formatZonedDateTime } from "@fb/shared/format/time";
import type {
  DataState,
  OperatorActionItem,
  OperatorAttentionItem,
  OperatorEconomyData,
  OperatorFunnelData,
  OperatorSeverity,
  OperatorSnapshot,
  OperatorSpendPoint,
  OperatorSystemData,
} from "@fb/shared/operator/contracts";
import { AccessibleChartFrame, DataStateBadge, OperatorSectionFrame } from "@fb/operator-ui";
import { useOperatorRealtimeStatus } from "@fb/operator-api";

import { Button } from "@/components/ui/Button";
import { ErrorState } from "@/components/ui/ErrorState";
import { Skeleton } from "@/components/ui/Skeleton";
import {
  operatorProblemMessage,
  useOperatorScanNow,
  useOperatorSnapshot,
} from "@/lib/api/operator";
import { toast } from "@/components/ui/toastStore";

const SEVERITY_VIEW: Record<OperatorSeverity, { glyph: string; color: string; surface: string }> = {
  ok: { glyph: "✓", color: "var(--color-success)", surface: "var(--color-success-bg)" },
  warning: { glyph: "△", color: "var(--color-warning)", surface: "var(--color-warning-bg)" },
  critical: { glyph: "!", color: "var(--color-danger)", surface: "var(--color-danger-bg)" },
  unknown: { glyph: "?", color: "var(--color-bg-9)", surface: "var(--color-bg-2)" },
};

const LazyOperatorSpendChartPlot = lazy(() =>
  import("./OperatorSpendChartPlot").then(({ OperatorSpendChartPlot }) => ({
    default: OperatorSpendChartPlot,
  })),
);

export function OperatorDashboard() {
  const snapshotQuery = useOperatorSnapshot({ window: "today" });
  const triggerScan = useOperatorScanNow();
  const realtimeStatus = useOperatorRealtimeStatus();

  if (snapshotQuery.isLoading && !snapshotQuery.data) return <OperatorDashboardSkeleton />;
  if (snapshotQuery.isError || !snapshotQuery.data) {
    return (
      <ErrorState
        title="Операторский снимок недоступен"
        error={operatorProblemMessage(snapshotQuery.error)}
        onRetry={() => void snapshotQuery.refetch()}
      />
    );
  }

  const snapshot = snapshotForRealtimeState(snapshotQuery.data, realtimeStatus === "connected");
  const headline = snapshotHeadline(snapshot);
  const displayTimeZone = snapshot.meta.timezone;
  const timezoneDegraded = snapshot.meta.cabinet_timezone_state !== "single";
  const currencyUnknown = snapshot.meta.currency_state !== "single";
  const systemDisplayState = snapshotOverviewState(snapshot);
  const view = SEVERITY_VIEW[headline.severity];

  const scanNow = () => {
    triggerScan.mutate(undefined, {
      onSuccess: () => {
        toast.success("Сканирование поставлено в очередь");
        void snapshotQuery.refetch();
      },
    });
  };

  return (
    <div className="operator-dashboard" aria-labelledby="operator-dashboard-title">
      <header className="mb-5 flex flex-col gap-5 lg:flex-row lg:items-end lg:justify-between">
        <div className="max-w-3xl">
          <div className="font-display text-[12px] uppercase tracking-[0.08em] text-bg-8">
            Сейчас · кабинет {snapshot.meta.account.name ?? snapshot.meta.account.id ?? "не выбран"}
          </div>
          <h1
            id="operator-dashboard-title"
            className="m-0 mt-2 font-display text-[clamp(28px,4vw,44px)] font-medium leading-[1.05] tracking-[-0.035em] text-bg-11"
          >
            {headline.title}
          </h1>
          <p className="mt-3 max-w-2xl text-[16px] leading-6 text-bg-9">{headline.detail}</p>
        </div>
        <div className="flex w-full flex-wrap items-center gap-2 lg:w-auto lg:justify-end">
          <DataStateBadge state={systemDisplayState} />
          <span className="inline-flex min-h-11 items-center gap-2 rounded-[var(--radius-2)] border border-[var(--color-hairline)] px-3 font-display text-[12px] text-bg-9">
            <span className="text-[16px]" aria-hidden="true">
              ◷
            </span>
            Снимок {formatDateTime(snapshot.meta.generated_at, displayTimeZone)}
          </span>
          <Button
            variant="primary"
            size="lg"
            leftIcon={<RefreshCw size={16} aria-hidden="true" />}
            loading={triggerScan.isPending}
            onClick={scanNow}
            className="min-h-11"
          >
            Сканировать
          </Button>
        </div>
      </header>

      {timezoneDegraded ? (
        <div
          role="status"
          className="mb-5 flex min-h-11 items-center gap-3 rounded-[var(--radius-2)] border border-warning/40 bg-warning-bg px-4 py-3 text-[14px] text-bg-11"
        >
          <AlertTriangle className="shrink-0 text-warning" size={18} aria-hidden="true" />
          <span>
            {snapshot.meta.cabinet_timezone_state === "mixed"
              ? "В выборке несколько часовых поясов; границы суток рассчитаны отдельно по кабинетам."
              : `Часовой пояс кабинета неизвестен; границы суток оценочные${
                  snapshot.meta.missing_timezone_account_ids.length
                    ? ` (${snapshot.meta.missing_timezone_account_ids.length} кабинетов)`
                    : ""
                }.`}
          </span>
        </div>
      ) : null}
      {currencyUnknown ? (
        <div
          role="status"
          className="mb-5 flex min-h-11 items-center gap-3 rounded-[var(--radius-2)] border border-warning/40 bg-warning-bg px-4 py-3 text-[14px] text-bg-11"
        >
          <AlertTriangle className="shrink-0 text-warning" size={18} aria-hidden="true" />
          <span>
            {snapshot.meta.currency_state === "mixed"
              ? "В выборке несколько валют; денежные итоги и сравнения скрыты."
              : "Валюта кабинета не подтверждена; денежные значения скрыты."}
          </span>
        </div>
      ) : null}

      <section
        aria-label="Сводное состояние"
        className="mb-5 overflow-hidden rounded-[var(--radius-3)] border border-[var(--color-hairline-strong)]"
        style={{ background: view.surface }}
      >
        <div className="flex flex-col gap-4 p-4 sm:flex-row sm:items-center">
          <span
            className="flex size-11 shrink-0 items-center justify-center rounded-[var(--radius-2)] bg-bg-0/40"
            style={{ color: view.color }}
          >
            <span className="font-display text-[22px] font-semibold" aria-hidden="true">
              {view.glyph}
            </span>
          </span>
          <div className="min-w-0 flex-1">
            <strong className="text-[16px] text-bg-11">{SEVERITY_LABEL[headline.severity]}</strong>
            <p className="mt-1 text-[14px] text-bg-10">
              {systemSummary(snapshot.system.data, displayTimeZone, snapshot.system.state)}
            </p>
          </div>
          <Link
            to="/system/sources"
            className="inline-flex min-h-11 items-center justify-center gap-2 rounded-[var(--radius-2)] border border-[var(--color-hairline-strong)] bg-bg-1 px-4 text-[14px] font-semibold text-bg-11 hover:bg-bg-2 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
          >
            Источники и воркеры
            <ArrowRight size={15} aria-hidden="true" />
          </Link>
        </div>
        {snapshot.system.data ? (
          <WorkerRail system={snapshot.system.data} state={snapshot.system.state} />
        ) : null}
      </section>

      <div className="grid min-w-0 gap-5 2xl:grid-cols-[minmax(0,1.45fr)_minmax(360px,.7fr)]">
        <OperatorSectionFrame
          section={snapshot.economy}
          title="Расход и границы"
          description="Факт, базовый бюджет и stop-граница за сутки кабинета."
          className="min-w-0"
          empty={<OperatorEmpty text="В выбранном периоде расхода нет." />}
        >
          {(economy) => <EconomyPanel economy={economy} snapshot={snapshot} />}
        </OperatorSectionFrame>

        <OperatorSectionFrame
          section={snapshot.attention}
          title="Требует внимания"
          description="Сначала критичные риски, затем действия и рекомендации."
          action={
            <Link
              to="/actions"
              className="inline-flex min-h-11 items-center gap-2 rounded-[var(--radius-2)] px-2 text-[14px] font-semibold text-bg-10 hover:bg-bg-2 hover:text-bg-11 focus-visible:outline-2 focus-visible:outline-accent"
            >
              Все действия <ArrowRight size={14} aria-hidden="true" />
            </Link>
          }
          empty={<OperatorEmpty text="Активных сигналов нет." />}
        >
          {(attention) => (
            <AttentionFeed items={attention.items.slice(0, 6)} timezone={displayTimeZone} />
          )}
        </OperatorSectionFrame>

        <OperatorSectionFrame
          section={snapshot.funnel}
          title="Воронка Meta → Tracker"
          description="Количество, конверсия и стоимость каждого подтверждённого этапа."
          className="min-w-0"
          empty={<OperatorEmpty text="Для расчёта воронки пока нет событий." />}
        >
          {(funnel) => <FunnelPanel funnel={funnel} currency={snapshot.meta.currency ?? null} />}
        </OperatorSectionFrame>

        <OperatorSectionFrame
          section={snapshot.actions}
          title="Действия"
          description="Очередь, выполняемые операции и последние ошибки."
          empty={<OperatorEmpty text="Активных действий нет." />}
        >
          {(actions) => <ActionList items={actions.items.slice(0, 6)} />}
        </OperatorSectionFrame>
      </div>
    </div>
  );
}

export function ActionList({ items }: { items: OperatorActionItem[] }) {
  if (!items.length) return <OperatorEmpty text="Активных действий нет." />;
  return (
    <ol className="mt-4 divide-y divide-[var(--color-hairline)]" aria-label="Последние действия">
      {items.map((item) => (
        <li key={item.id} className="flex min-h-16 items-start gap-3 py-3">
          <ActionStateMark state={item.state} />
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <Link
                to="/actions/$actionId"
                params={{ actionId: item.id }}
                className="inline-flex min-h-11 items-center rounded-[var(--radius-2)] px-1 text-[14px] font-semibold text-bg-11 underline-offset-4 hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
              >
                {item.title}
              </Link>
              <span className="font-display text-[12px] text-bg-9">{item.public_id}</span>
            </div>
            <p className="mt-1 text-[14px] text-bg-9">
              {item.target_label ?? "Системная операция"} · {ACTION_STATE_LABEL[item.state]}
            </p>
            {item.reason ? <p className="mt-1 text-[12px] text-bg-8">{item.reason}</p> : null}
          </div>
        </li>
      ))}
    </ol>
  );
}

function EconomyPanel({
  economy,
  snapshot,
}: {
  economy: OperatorEconomyData;
  snapshot: OperatorSnapshot;
}) {
  const { totals } = economy;
  return (
    <>
      <dl className="mt-5 grid grid-cols-2 gap-px overflow-hidden rounded-[var(--radius-2)] border border-[var(--color-hairline)] bg-[var(--color-hairline)] lg:grid-cols-4">
        <MoneyMetric label="Факт" value={totals.spend} currency={snapshot.meta.currency ?? null} />
        <MoneyMetric label="База" value={totals.base} currency={snapshot.meta.currency ?? null} />
        <MoneyMetric label="Stop" value={totals.stop} currency={snapshot.meta.currency ?? null} />
        <MoneyMetric
          label="Отклонение от базы"
          value={totals.base_delta}
          currency={snapshot.meta.currency ?? null}
          signed
        />
      </dl>
      <SpendChart economy={economy} snapshot={snapshot} />
    </>
  );
}

function SpendChart({
  economy,
  snapshot,
}: {
  economy: OperatorEconomyData;
  snapshot: OperatorSnapshot;
}) {
  const actual = decimalToNumber(economy.totals.spend);
  const stop = decimalToNumber(economy.totals.stop);
  const summary =
    snapshot.meta.currency_state !== "single" || !snapshot.meta.currency
      ? "Валюта кабинета не подтверждена; денежные значения скрыты."
      : actual === null
        ? "Фактический расход не подтверждён."
        : stop === null
          ? `Факт ${formatMoney(actual, snapshot.meta.currency)}; stop-граница не подтверждена.`
          : `Факт ${formatMoney(actual, snapshot.meta.currency)} из stop-границы ${formatMoney(stop, snapshot.meta.currency)}.`;

  return (
    <AccessibleChartFrame
      title="Накопительный расход"
      summary={summary}
      timezone={snapshot.meta.timezone}
      asOf={snapshot.economy.as_of}
      sources={snapshot.economy.sources}
      completeness={snapshot.economy.state}
      chart={
        economy.series.length ? (
          <Suspense fallback={<SpendChartFallback />}>
            <LazyOperatorSpendChartPlot
              points={economy.series}
              currency={snapshot.meta.currency ?? null}
              timezone={snapshot.meta.timezone}
              generatedAt={snapshot.meta.generated_at}
            />
          </Suspense>
        ) : (
          <OperatorEmpty text="Точки графика пока не получены." />
        )
      }
      table={
        <SpendDataTable
          rows={economy.series}
          currency={snapshot.meta.currency ?? null}
          timezone={snapshot.meta.timezone}
        />
      }
    />
  );
}

function SpendChartFallback() {
  return (
    <div
      role="status"
      aria-label="Загрузка графика расходов"
      className="h-[240px] w-full sm:h-[280px]"
    >
      <div aria-hidden="true" className="h-full animate-pulse rounded-[var(--radius-2)] bg-bg-3" />
    </div>
  );
}

function SpendDataTable({
  rows,
  currency,
  timezone,
}: {
  rows: OperatorSpendPoint[];
  currency: string | null;
  timezone: string;
}) {
  return (
    <table>
      <caption className="sr-only">Накопительный расход по времени</caption>
      <thead>
        <tr>
          <th scope="col">Время</th>
          <th scope="col">Факт</th>
          <th scope="col">База</th>
          <th scope="col">Stop</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.at}>
            <th scope="row">{formatDateTime(row.at, timezone)}</th>
            <td>{formatMoneyValue(row.actual, currency)}</td>
            <td>{formatMoneyValue(row.base, currency)}</td>
            <td>{formatMoneyValue(row.stop, currency)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function FunnelPanel({
  funnel,
  currency,
}: {
  funnel: OperatorFunnelData;
  currency: string | null;
}) {
  const firstKnown = funnel.stages.find((stage) => stage.count !== null)?.count ?? null;
  return (
    <ol className="mt-5 space-y-3" aria-label="Этапы воронки">
      {funnel.stages.map((stage, index) => {
        const width =
          stage.count === null || firstKnown === null || firstKnown <= 0
            ? 0
            : Math.max(5, Math.min(100, (stage.count / firstKnown) * 100));
        return (
          <li key={stage.key} className="grid grid-cols-[32px_minmax(0,1fr)] gap-3">
            <span className="flex size-8 items-center justify-center rounded-full border border-[var(--color-hairline-strong)] font-display text-[12px] text-bg-9">
              {index + 1}
            </span>
            <div className="min-w-0">
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <strong className="text-[14px] text-bg-11">{stage.label}</strong>
                <span className="font-display text-[16px] tabular-nums text-bg-11">
                  {stage.count === null ? "—" : stage.count.toLocaleString("ru-RU")}
                </span>
              </div>
              <div className="mt-2 h-2 overflow-hidden rounded-full bg-bg-3" aria-hidden="true">
                <div
                  className="h-full rounded-full bg-accent transition-[width] duration-300"
                  style={{ width: `${width}%` }}
                />
              </div>
              <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[12px] text-bg-9">
                <span>CR {stage.conversion === null ? "—" : `${stage.conversion}%`}</span>
                <span>Стоимость {formatMoneyValue(stage.cost, currency)}</span>
              </div>
            </div>
          </li>
        );
      })}
    </ol>
  );
}

function AttentionFeed({ items, timezone }: { items: OperatorAttentionItem[]; timezone: string }) {
  if (!items.length) return <OperatorEmpty text="Активных сигналов нет." />;
  return (
    <ol className="mt-4 divide-y divide-[var(--color-hairline)]" aria-label="Сигналы внимания">
      {items.map((item) => {
        const view = SEVERITY_VIEW[item.severity];
        const actionHref = item.action ? safeOperatorAttentionHref(item.action.href) : null;
        return (
          <li key={item.id} className="py-4 first:pt-2">
            <div className="flex items-start gap-3">
              <span
                className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-[var(--radius-2)] font-display text-[14px] font-semibold"
                style={{ background: view.surface, color: view.color }}
              >
                <span aria-hidden="true">{view.glyph}</span>
              </span>
              <div className="min-w-0 flex-1">
                <span className="font-display text-[12px] uppercase tracking-[.06em] text-bg-8">
                  {SEVERITY_LABEL[item.severity]}
                </span>
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <strong className="text-[14px] text-bg-11">{item.title}</strong>
                  <time className="font-display text-[12px] text-bg-8" dateTime={item.occurred_at}>
                    {formatDateTime(item.occurred_at, timezone)}
                  </time>
                </div>
                <p className="mt-1 text-[14px] leading-5 text-bg-10">{item.summary}</p>
                {item.reason ? (
                  <p className="mt-1 text-[12px] text-bg-8">Причина: {item.reason}</p>
                ) : null}
                {item.action && actionHref ? (
                  <a
                    href={actionHref}
                    className="mt-3 inline-flex min-h-11 items-center gap-2 rounded-[var(--radius-2)] border border-[var(--color-hairline-strong)] px-3 text-[14px] font-semibold text-bg-11 hover:bg-bg-2 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
                  >
                    {item.action.label} <ArrowRight size={14} aria-hidden="true" />
                  </a>
                ) : null}
              </div>
            </div>
          </li>
        );
      })}
    </ol>
  );
}

function WorkerRail({ system, state }: { system: OperatorSystemData; state: DataState }) {
  return (
    <ul
      className="grid border-t border-[var(--color-hairline)] bg-bg-0/30 sm:grid-cols-2 xl:grid-cols-4"
      aria-label="Источники данных"
    >
      {system.workers.map((worker) => {
        const severity = severityForDataState(worker.severity, state);
        return (
          <li
            key={worker.id}
            className="flex min-h-14 items-center gap-3 border-b border-[var(--color-hairline)] px-4 py-3 sm:border-r xl:border-b-0"
          >
            <span
              className="size-2.5 shrink-0 rounded-full"
              style={{ background: SEVERITY_VIEW[severity].color }}
              aria-hidden="true"
              data-severity={severity}
            />
            <div className="min-w-0">
              <div className="truncate text-[14px] font-semibold text-bg-11">{worker.label}</div>
              <div className="truncate text-[12px] text-bg-9">
                {state === "stale" || state === "unavailable"
                  ? "Состояние не подтверждено"
                  : workerStatusLabel(worker.status)}
              </div>
            </div>
          </li>
        );
      })}
    </ul>
  );
}

function ActionStateMark({ state }: { state: OperatorActionItem["state"] }) {
  const color =
    state === "confirmed"
      ? "var(--color-success)"
      : state === "failed"
        ? "var(--color-danger)"
        : state === "queued" || state === "running"
          ? "var(--color-warning)"
          : "var(--color-bg-8)";
  return (
    <span
      className="mt-1 flex size-8 shrink-0 items-center justify-center rounded-full border border-[var(--color-hairline-strong)]"
      style={{ color }}
      title={ACTION_STATE_LABEL[state]}
    >
      <Activity size={15} aria-hidden="true" />
      <span className="sr-only">{ACTION_STATE_LABEL[state]}</span>
    </span>
  );
}

function MoneyMetric({
  label,
  value,
  currency,
  signed = false,
}: {
  label: string;
  value: string | null;
  currency: string | null;
  signed?: boolean;
}) {
  const parsed = decimalToNumber(value);
  const formatted = parsed === null ? "—" : formatMoney(parsed, currency);
  const confirmed = parsed !== null && formatted !== "—";
  return (
    <div className="bg-bg-2 p-4">
      <dt className="text-[12px] font-semibold uppercase tracking-[0.05em] text-bg-9">{label}</dt>
      <dd
        className="m-0 mt-2 font-display text-[clamp(18px,2.2vw,24px)] tabular-nums text-bg-11"
        aria-label={!confirmed ? `${label}: не подтверждено` : undefined}
      >
        {!confirmed ? "—" : `${signed && parsed! > 0 ? "+" : ""}${formatted}`}
      </dd>
    </div>
  );
}

function OperatorEmpty({ text }: { text: string }) {
  return (
    <div className="mt-4 flex min-h-24 items-center justify-center rounded-[var(--radius-2)] border border-dashed border-[var(--color-hairline-strong)] px-4 text-center text-[14px] text-bg-9">
      {text}
    </div>
  );
}

function OperatorDashboardSkeleton() {
  return (
    <div role="status" aria-label="Загрузка операторского снимка" className="space-y-5">
      <div className="space-y-3 py-3">
        <Skeleton height={12} width={180} className="rounded" />
        <Skeleton height={42} width="min(640px, 90%)" className="rounded" />
        <Skeleton height={20} width="min(460px, 75%)" className="rounded" />
      </div>
      <Skeleton height={112} className="rounded-[var(--radius-3)]" />
      <div className="grid gap-5 lg:grid-cols-2">
        <Skeleton height={420} className="rounded-[var(--radius-3)]" />
        <Skeleton height={420} className="rounded-[var(--radius-3)]" />
      </div>
    </div>
  );
}

function systemSummary(
  system: OperatorSystemData | null,
  timezone: string,
  state: DataState,
): string {
  if (!system) return "Нет подтверждённых данных о мониторинге и воркерах.";
  if (state === "partial") {
    return "Снимок источников неполный; текущее состояние подтверждено не полностью.";
  }
  if (state === "stale") {
    return "Последний подтверждённый снимок устарел; текущее состояние неизвестно.";
  }
  if (state !== "ready") {
    return "Текущее состояние мониторинга и воркеров не подтверждено.";
  }
  const online = system.workers.filter((worker) => worker.severity === "ok").length;
  const scan = system.last_scan_at
    ? `Последний скан ${formatDateTime(system.last_scan_at, timezone)}.`
    : "Последний скан не подтверждён.";
  const monitoring =
    system.monitoring_enabled === true
      ? "Мониторинг включён."
      : system.monitoring_enabled === false
        ? "Мониторинг остановлен."
        : "Состояние мониторинга неизвестно.";
  return `${monitoring} ${online}/${system.workers.length} источников в норме. ${scan}`;
}

function formatMoneyValue(value: string | null, currency: string | null): string {
  return formatSpend(value, currency);
}

function formatMoney(value: number, currency: string | null): string {
  return formatSpend(value, currency);
}

function formatDateTime(value: string, timezone: string): string {
  const formatted = formatZonedDateTime(value, timezone);
  return formatted === "—" ? "не подтверждено" : formatted;
}
