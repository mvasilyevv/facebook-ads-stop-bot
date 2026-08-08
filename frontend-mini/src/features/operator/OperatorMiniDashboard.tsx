import { useState, type ReactNode } from "react";
import { Link, useNavigate } from "@tanstack/react-router";
import {
  AlertTriangle,
  ArrowRight,
  Check,
  CircleHelp,
  Clock3,
  RefreshCw,
  X,
  type LucideIcon,
} from "lucide-react";

import {
  ACTION_STATE_LABEL,
  DATA_STATE_LABEL,
  SEVERITY_LABEL,
  snapshotForRealtimeState,
  snapshotHeadline,
  snapshotOverviewState,
} from "@fb/shared/operator/viewModel";
import { useOperatorRealtimeStatus } from "@fb/operator-api";
import { formatSpend } from "@fb/shared/format/number";
import { formatZonedDateTime } from "@fb/shared/format/time";
import type { components } from "@fb/shared/api/generated";
import { confirmedOperatorCurrency } from "@fb/shared/operator/adsViewModel";
import {
  buildOperatorPortfolioScale,
  operatorPortfolioScalePosition,
} from "@fb/shared/operator/portfolioModel";
import type {
  OperatorActionItem,
  OperatorAttentionItem,
  OperatorCabinetLedgerRow,
  OperatorCurrencyGroup,
  OperatorFunnelData,
  OperatorSection,
  OperatorSeverity,
  OperatorSnapshot,
} from "@fb/shared/operator/contracts";

import { Button } from "@/components/ui/Button";
import { haptic, tgAlert } from "@/lib/tg";
import {
  parseTmaAttentionHref,
  storeResolvedNavigation,
} from "@/lib/transientNavigation";
import {
  operatorProblemMessage,
  useOperatorCabinetSnapshot,
  useOperatorScanNow,
  useOperatorSnapshot,
} from "@/lib/operatorApi";

import "./operator-mini-ledger.css";

const SEVERITY_ICON: Record<OperatorSeverity, LucideIcon> = {
  ok: Check,
  warning: AlertTriangle,
  critical: AlertTriangle,
  unknown: CircleHelp,
};

const ACTION_ICON: Record<OperatorActionItem["state"], LucideIcon> = {
  queued: Clock3,
  running: Clock3,
  confirmed: Check,
  failed: X,
  cancelled: X,
  unknown: CircleHelp,
};

export function OperatorMiniDashboard() {
  const snapshotQuery = useOperatorSnapshot({ window: "today" });
  return <OperatorMiniLedgerScreen snapshotQuery={snapshotQuery} />;
}

export function OperatorMiniCabinetDashboard({
  cabinetId,
}: {
  cabinetId: string;
}) {
  const snapshotQuery = useOperatorCabinetSnapshot(cabinetId, {
    window: "today",
  });
  return (
    <OperatorMiniLedgerScreen
      snapshotQuery={snapshotQuery}
      cabinetId={cabinetId}
    />
  );
}

interface SnapshotQueryLike {
  data?: OperatorSnapshot;
  isLoading: boolean;
  isError: boolean;
  error: unknown;
  refetch: () => unknown;
}

function OperatorMiniLedgerScreen({
  snapshotQuery,
  cabinetId,
}: {
  snapshotQuery: SnapshotQueryLike;
  cabinetId?: string;
}) {
  const navigate = useNavigate();
  const realtimeStatus = useOperatorRealtimeStatus();
  const scan = useOperatorScanNow();
  const [scanReceipt, setScanReceipt] = useState<
    components["schemas"]["ScanNowResponse"] | null
  >(null);

  if (snapshotQuery.isLoading && !snapshotQuery.data) {
    return <MiniLoading />;
  }
  if (snapshotQuery.isError || !snapshotQuery.data) {
    return (
      <div
        role="alert"
        className="m-4 rounded-[var(--radius-3)] border border-danger/40 bg-danger-bg p-4"
      >
        <strong className="text-[16px] text-bg-11">Снимок недоступен</strong>
        <p className="mt-2 text-[14px] leading-5 text-bg-10">
          {operatorProblemMessage(snapshotQuery.error)}
        </p>
        <Button
          className="mt-4 min-h-11"
          onClick={() => void snapshotQuery.refetch()}
        >
          Повторить
        </Button>
      </div>
    );
  }

  const snapshot = snapshotForRealtimeState(
    snapshotQuery.data,
    realtimeStatus === "connected",
  );
  const headline = snapshotHeadline(snapshot);
  const overviewState = snapshotOverviewState(snapshot);
  const StatusIcon = SEVERITY_ICON[headline.severity];
  const pageTitle = cabinetId
    ? (snapshot.meta.account.name ?? `Кабинет ${cabinetId}`)
    : "Сейчас";
  const cabinetCurrencyLabel = confirmedOperatorCurrency(snapshot.meta)
    ? "$"
    : "USD не подтверждён";
  const pageDetail = cabinetId
    ? `${cabinetCurrencyLabel} · ${snapshot.meta.cabinet_timezone ?? "часовой пояс не подтверждён"}`
    : "Деньги, расхождения и команды";

  const runScan = async () => {
    haptic.impact("medium");
    try {
      const receipt = await scan.mutateAsync({});
      setScanReceipt(receipt);
      haptic.notify("warning");
      void snapshotQuery.refetch();
    } catch {
      haptic.notify("error");
    }
  };

  const openAttentionAction = async (href: string) => {
    const destination = parseTmaAttentionHref(href);
    if (!destination) {
      tgAlert("Ссылка действия недоступна.");
      return;
    }
    haptic.selection();
    if (destination.kind === "target") {
      storeResolvedNavigation(destination.target);
      await navigate({ to: "/open" });
      return;
    }
    await navigate({ to: destination.to });
  };

  const openCabinet = async (nextCabinetId: string) => {
    haptic.selection();
    await navigate({
      to: "/cabinets/$cabinetId",
      params: { cabinetId: nextCabinetId },
    });
  };

  return (
    <div className="mini-ledger" aria-labelledby="mini-ledger-title">
      <header className="mini-ledger__header">
        <div>
          <span className="mini-ledger__eyebrow">FB Agent · оператор</span>
          <h1 id="mini-ledger-title">{pageTitle}</h1>
          <p>{pageDetail}</p>
        </div>
        <div className="mini-ledger__tools">
          <button
            type="button"
            className="mini-proof"
            onClick={() => void navigate({ to: "/system/sources" })}
          >
            <StatusIcon size={15} aria-hidden="true" />
            <span>{miniStateLabel(overviewState)}</span>
            <span>{freshnessLabel(snapshot.portfolio.freshness_seconds)}</span>
          </button>
          <button
            type="button"
            className="mini-scan"
            aria-label="Сканировать сейчас"
            disabled={scan.isPending}
            onClick={() => void runScan()}
          >
            <RefreshCw
              size={18}
              className={scan.isPending ? "animate-spin" : ""}
              aria-hidden="true"
            />
            <span>Сканировать</span>
          </button>
        </div>
      </header>

      {scanReceipt ? (
        <div role="status" aria-live="polite" className="mini-ledger__receipt">
          <div>
            <strong>Сканирование поставлено в очередь</strong>
            <span>Задача #{scanReceipt.task_id}</span>
          </div>
          <Link
            to="/actions/$actionId"
            params={{ actionId: String(scanReceipt.task_id) }}
            className="mini-ledger__inline-action"
          >
            Открыть выполнение
          </Link>
        </div>
      ) : null}

      <div
        className="mini-ledger__status"
        data-severity={headline.severity}
        role={headline.severity === "critical" ? "alert" : "status"}
      >
        <StatusIcon size={17} aria-hidden="true" />
        <div>
          <strong>{headline.title}</strong>
          <p>{headline.detail}</p>
        </div>
        <span>
          as_of{" "}
          {formatDateTime(snapshot.meta.generated_at, snapshot.meta.timezone)}
        </span>
      </div>

      <div className="mini-ledger__flow">
        <MiniAttentionLedger
          section={snapshot.attention}
          timezone={snapshot.meta.timezone}
          onAction={openAttentionAction}
        />
        <MiniPortfolioLedger
          section={snapshot.portfolio}
          timezone={snapshot.meta.timezone}
          usdScopeConfirmed={
            snapshot.meta.currency_state === "single" &&
            snapshot.meta.currency === "USD"
          }
          onCabinet={openCabinet}
        />
        <MiniActionJournal section={snapshot.actions} />
        <MiniFunnelLedger
          section={snapshot.funnel}
          currency={snapshot.meta.currency ?? null}
          timezone={snapshot.meta.timezone}
        />
      </div>
    </div>
  );
}

function MiniAttentionLedger({
  section,
  timezone,
  onAction,
}: {
  section: OperatorSnapshot["attention"];
  timezone: string;
  onAction: (href: string) => Promise<void>;
}) {
  const items = section.data?.items.slice(0, 5) ?? [];
  return (
    <MiniLedgerSection
      className="mini-ledger-section--attention"
      id="mini-attention-title"
      title="Требует внимания"
      detail={`${items.length} ${pluralReason(items.length)}`}
      section={section}
      timezone={timezone}
    >
      {!section.data ? (
        <MiniLedgerEmpty text={DATA_STATE_LABEL[section.state]} />
      ) : items.length === 0 ? (
        <MiniLedgerEmpty text="Активных сигналов нет." />
      ) : (
        <ol className="mini-attention-list">
          {items.map((item) => (
            <MiniAttentionItem key={item.id} item={item} onAction={onAction} />
          ))}
        </ol>
      )}
    </MiniLedgerSection>
  );
}

function MiniAttentionItem({
  item,
  onAction,
}: {
  item: OperatorAttentionItem;
  onAction: (href: string) => Promise<void>;
}) {
  const Icon = SEVERITY_ICON[item.severity];
  return (
    <li className="mini-attention-item" data-severity={item.severity}>
      <div className="mini-attention-item__head">
        <span>{item.target.label ?? item.title}</span>
        <span data-severity={item.severity}>
          <Icon size={14} aria-hidden="true" />
          {SEVERITY_LABEL[item.severity]}
        </span>
      </div>
      <h3>{item.title}</h3>
      <p>{item.summary}</p>
      {item.reason ? <p>Причина: {item.reason}</p> : null}
      {item.action ? (
        <button type="button" onClick={() => void onAction(item.action!.href)}>
          {item.action.label}
          <ArrowRight size={14} aria-hidden="true" />
        </button>
      ) : null}
    </li>
  );
}

function MiniPortfolioLedger({
  section,
  timezone,
  usdScopeConfirmed,
  onCabinet,
}: {
  section: OperatorSnapshot["portfolio"];
  timezone: string;
  usdScopeConfirmed: boolean;
  onCabinet: (cabinetId: string) => Promise<void>;
}) {
  const groups = section.data?.currency_groups ?? [];
  return (
    <MiniLedgerSection
      className="mini-ledger-section--portfolio"
      id="mini-portfolio-title"
      title="Портфель"
      detail="сегодня"
      section={section}
      timezone={timezone}
    >
      {!section.data ? (
        <MiniLedgerEmpty text={DATA_STATE_LABEL[section.state]} />
      ) : groups.length === 0 ? (
        <MiniLedgerEmpty text="Кабинеты ещё не добавлены." />
      ) : (
        groups.map((group) => (
          <MiniCurrencyGroup
            key={group.id}
            group={group}
            usdScopeConfirmed={usdScopeConfirmed}
            onCabinet={onCabinet}
          />
        ))
      )}
    </MiniLedgerSection>
  );
}

function MiniCurrencyGroup({
  group,
  usdScopeConfirmed,
  onCabinet,
}: {
  group: OperatorCurrencyGroup;
  usdScopeConfirmed: boolean;
  onCabinet: (cabinetId: string) => Promise<void>;
}) {
  const scale = buildOperatorPortfolioScale(group, usdScopeConfirmed);
  const { usdConfirmed } = scale;
  const totals = usdConfirmed
    ? group.totals
    : { spend: null, base: null, stop: null, base_delta: null };
  return (
    <div className="mini-ledger-group" data-state={group.state}>
      {!usdConfirmed ? (
        <div className="mini-ledger-group__currency-warning">
          <AlertTriangle size={15} aria-hidden="true" />
          Валюта не подтверждена; суммы скрыты
        </div>
      ) : null}
      <dl className="mini-ledger-totals">
        <MiniLedgerTotal label="Расход" value={totals.spend} />
        <MiniLedgerTotal label="База" value={totals.base} />
        <MiniLedgerTotal label="Стоп" value={totals.stop} stop />
      </dl>
      <div className="mini-ledger-axis" aria-hidden="true">
        <span>$0</span>
        <span>{formatScaleTick(scale.maximum / 2)}</span>
        <span>{formatScaleTick(scale.maximum)}</span>
      </div>
      <div role="list" aria-label="Кабинеты">
        {group.cabinets.map((cabinet) => (
          <MiniCabinetRow
            key={cabinet.id}
            cabinet={cabinet}
            scale={scale}
            usdConfirmed={usdConfirmed}
            onOpen={onCabinet}
          />
        ))}
      </div>
    </div>
  );
}

function MiniCabinetRow({
  cabinet,
  scale,
  usdConfirmed,
  onOpen,
}: {
  cabinet: OperatorCabinetLedgerRow;
  scale: ReturnType<typeof buildOperatorPortfolioScale>;
  usdConfirmed: boolean;
  onOpen: (cabinetId: string) => Promise<void>;
}) {
  const Icon = SEVERITY_ICON[cabinet.severity];
  const showScale =
    usdConfirmed &&
    cabinet.state !== "stale" &&
    cabinet.state !== "unavailable" &&
    cabinet.totals.spend !== null;
  const actualPercent = operatorPortfolioScalePosition(
    showScale ? cabinet.totals.spend : null,
    scale,
  );
  const basePercent = operatorPortfolioScalePosition(
    showScale ? cabinet.totals.base : null,
    scale,
  );
  const stopPercent = operatorPortfolioScalePosition(
    showScale ? cabinet.totals.stop : null,
    scale,
  );
  return (
    <div
      className="mini-ledger-cabinet"
      data-severity={cabinet.severity}
      data-state={cabinet.state}
      role="listitem"
    >
      <button type="button" onClick={() => void onOpen(cabinet.id)}>
        <span className="mini-ledger-cabinet__identity">
          <strong>{cabinet.name}</strong>
          <small>
            {usdConfirmed ? "$" : "валюта не подтверждена"} ·{" "}
            {cabinet.timezone ?? "timezone не подтверждён"}
          </small>
        </span>
        <span
          className="mini-ledger-cabinet__state"
          data-severity={cabinet.severity}
        >
          <Icon size={14} aria-hidden="true" />
          {cabinet.risk_label}
        </span>
        <ArrowRight size={15} aria-hidden="true" />
      </button>
      <div className="mini-ledger-scale" data-state={cabinet.state}>
        <span className="mini-ledger-scale__baseline" aria-hidden="true" />
        {actualPercent !== null ? (
          <>
            <span
              className="mini-ledger-scale__actual"
              style={{ width: `${actualPercent}%` }}
              aria-hidden="true"
            />
            <span
              className="mini-ledger-scale__value"
              data-align={actualPercent > 78 ? "end" : "start"}
              style={{ left: `${actualPercent}%` }}
            >
              {formatUsd(cabinet.totals.spend)}
            </span>
          </>
        ) : (
          <span className="mini-ledger-scale__unknown">
            {cabinet.state === "stale"
              ? "снимок устарел"
              : "данные не подтверждены"}
          </span>
        )}
        {basePercent !== null ? (
          <span
            className="mini-ledger-scale__marker"
            style={{ left: `${basePercent}%` }}
            aria-hidden="true"
          />
        ) : null}
        {stopPercent !== null ? (
          <span
            className="mini-ledger-scale__marker mini-ledger-scale__marker--stop"
            style={{ left: `${stopPercent}%` }}
            aria-hidden="true"
          />
        ) : null}
        <span className="sr-only">
          Расход {formatUsd(showScale ? cabinet.totals.spend : null)}, база{" "}
          {formatUsd(showScale ? cabinet.totals.base : null)}, стоп{" "}
          {formatUsd(showScale ? cabinet.totals.stop : null)}.
        </span>
      </div>
    </div>
  );
}

function MiniActionJournal({
  section,
}: {
  section: OperatorSnapshot["actions"];
}) {
  const items = section.data?.items.slice(0, 5) ?? [];
  return (
    <MiniLedgerSection
      className="mini-ledger-section--actions"
      id="mini-actions-title"
      title="Действия"
      detail={`${items.filter(isActiveAction).length} выполняется`}
      section={section}
    >
      {!section.data ? (
        <MiniLedgerEmpty text={DATA_STATE_LABEL[section.state]} />
      ) : items.length === 0 ? (
        <MiniLedgerEmpty text="Активных действий нет." />
      ) : (
        <ol className="mini-action-journal">
          {items.map((item) => {
            const Icon = ACTION_ICON[item.state];
            return (
              <li key={item.id}>
                <div className="mini-action-journal__head">
                  <span>
                    {item.title} · {item.target_label ?? "система"}
                  </span>
                  <span data-state={item.state}>
                    <Icon size={14} aria-hidden="true" />
                    {ACTION_STATE_LABEL[item.state]}
                  </span>
                </div>
                {item.reason ? <p>{item.reason}</p> : null}
                {isActiveAction(item) ? (
                  <div
                    className="mini-action-journal__progress"
                    aria-hidden="true"
                  />
                ) : null}
                <div className="mini-action-journal__meta">
                  <span>Задача {item.public_id}</span>
                  <span>{item.correlation_id.slice(0, 8)}</span>
                </div>
                <Link to="/actions/$actionId" params={{ actionId: item.id }}>
                  Открыть действие
                  <ArrowRight size={14} aria-hidden="true" />
                </Link>
              </li>
            );
          })}
        </ol>
      )}
    </MiniLedgerSection>
  );
}

function MiniFunnelLedger({
  section,
  currency,
  timezone,
}: {
  section: OperatorSnapshot["funnel"];
  currency: string | null;
  timezone: string;
}) {
  return (
    <MiniLedgerSection
      className="mini-ledger-section--funnel"
      id="mini-funnel-title"
      title="Воронка"
      detail={
        section.state === "ready"
          ? "полные данные"
          : DATA_STATE_LABEL[section.state]
      }
      section={section}
      timezone={timezone}
    >
      {!section.data ? (
        <MiniLedgerEmpty text={DATA_STATE_LABEL[section.state]} />
      ) : (
        <MiniFunnelStages
          funnel={section.data}
          currency={currency === "USD" ? "USD" : null}
        />
      )}
    </MiniLedgerSection>
  );
}

function MiniFunnelStages({
  funnel,
  currency,
}: {
  funnel: OperatorFunnelData;
  currency: string | null;
}) {
  return (
    <ol className="mini-ledger-funnel" aria-label="Короткая воронка">
      {funnel.stages.map((stage) => (
        <li key={stage.key}>
          <span>{stage.label}</span>
          <strong>
            {stage.count === null ? "—" : stage.count.toLocaleString("ru-RU")}
          </strong>
          <small>
            <span>
              CR {stage.conversion === null ? "—" : `${stage.conversion}%`}
            </span>
            <span>{formatUsd(currency ? stage.cost : null)}</span>
          </small>
        </li>
      ))}
    </ol>
  );
}

function MiniLedgerSection<T>({
  className,
  id,
  title,
  detail,
  section,
  timezone,
  children,
}: {
  className: string;
  id: string;
  title: string;
  detail: string;
  section: OperatorSection<T>;
  timezone?: string;
  children: ReactNode;
}) {
  // The root shell owns the single reconnect notice. Section rows keep their
  // stale state without repeating the same transport cause four times.
  const issue = section.issues.find(
    (candidate) => candidate.code !== "REALTIME_RECONCILING",
  );
  const IssueIcon = issue ? SEVERITY_ICON[issue.severity] : null;
  return (
    <section
      className={`mini-ledger-section ${className}`}
      data-state={section.state}
      aria-labelledby={id}
    >
      <header>
        <div>
          <h2 id={id}>{title}</h2>
          <span>{detail}</span>
        </div>
        <div className="mini-ledger-section__proof">
          <span>{section.sources.map(sourceLabel).join(" + ")}</span>
          <span>
            as_of{" "}
            {section.as_of && timezone
              ? formatDateTime(section.as_of, timezone)
              : freshnessLabel(section.freshness_seconds)}
          </span>
        </div>
      </header>
      {issue && IssueIcon ? (
        <div
          className="mini-ledger-section__issue"
          data-severity={issue.severity}
          role="status"
        >
          <IssueIcon size={15} aria-hidden="true" />
          <div>
            <strong>{issue.title}</strong>
            <span>{issue.detail}</span>
          </div>
        </div>
      ) : null}
      {children}
    </section>
  );
}

function MiniLedgerTotal({
  label,
  value,
  stop = false,
}: {
  label: string;
  value: string | null;
  stop?: boolean;
}) {
  return (
    <div data-stop={stop || undefined}>
      <dt>{label}</dt>
      <dd data-known={value !== null}>{formatUsd(value)}</dd>
    </div>
  );
}

function MiniLedgerEmpty({ text }: { text: string }) {
  return <div className="mini-ledger-section__empty">{text}</div>;
}

function isActiveAction(item: OperatorActionItem): boolean {
  return item.state === "queued" || item.state === "running";
}

function formatUsd(value: string | number | null): string {
  return formatSpend(value, "USD").replace(/^USD\s*/, "$");
}

function formatScaleTick(value: number): string {
  return `$${Math.round(value)}`;
}

function formatDateTime(value: string, timezone: string): string {
  const formatted = formatZonedDateTime(value, timezone);
  return formatted === "—" ? "не подтверждено" : formatted;
}

function freshnessLabel(seconds: number | null): string {
  if (seconds === null) return "не подтверждено";
  if (seconds < 60) return `${seconds} сек`;
  return `${Math.max(1, Math.round(seconds / 60))} мин`;
}

function miniStateLabel(state: OperatorSnapshot["portfolio"]["state"]): string {
  const labels = {
    ready: "Актуально",
    empty: "Пусто",
    partial: "Неполные",
    stale: "Устарели",
    unavailable: "Недоступны",
  } as const;
  return labels[state];
}

function pluralReason(value: number): string {
  const remainder100 = value % 100;
  const remainder10 = value % 10;
  if (remainder100 >= 11 && remainder100 <= 14) return "причин";
  if (remainder10 === 1) return "причина";
  if (remainder10 >= 2 && remainder10 <= 4) return "причины";
  return "причин";
}

function sourceLabel(source: string): string {
  const labels: Record<string, string> = {
    meta: "Meta",
    offer_rules: "правила",
    meta_account_snapshot: "кабинеты",
    tracker: "Tracker",
    adsetpro: "Tracker",
    observer: "Observer",
    incidents: "инциденты",
    task_queue: "CommandService",
    worker_telemetry: "воркеры",
    postgresql: "PostgreSQL",
  };
  return labels[source] ?? source.replaceAll("_", " ");
}

export function MiniActions({ items }: { items: OperatorActionItem[] }) {
  if (!items.length) return <MiniEmpty text="Активных действий нет." />;
  return (
    <ol className="mt-3 divide-y divide-[var(--color-hairline)]">
      {items.map((item) => (
        <li key={item.id} className="py-3">
          <div className="flex items-baseline justify-between gap-3">
            <Link
              to="/actions/$actionId"
              params={{ actionId: item.id }}
              className="inline-flex min-h-11 min-w-0 items-center truncate rounded-[var(--radius-2)] px-1 text-[14px] font-semibold text-bg-11 underline-offset-4 focus-visible:outline-2 focus-visible:outline-accent"
            >
              {item.title}
            </Link>
            <span className="shrink-0 font-display text-[12px] text-bg-9">
              {item.public_id}
            </span>
          </div>
          <p className="mt-1 text-[14px] text-bg-9">
            {ACTION_STATE_LABEL[item.state]} · {item.target_label ?? "система"}
          </p>
          {item.state === "unknown" ? (
            <p className="mt-1 text-[12px] text-warning">
              Результат проверяется, успех не подтверждён.
            </p>
          ) : null}
        </li>
      ))}
    </ol>
  );
}

function MiniEmpty({ text }: { text: string }) {
  return (
    <div className="mt-3 rounded-[var(--radius-2)] border border-dashed border-[var(--color-hairline-strong)] p-4 text-center text-[14px] text-bg-9">
      {text}
    </div>
  );
}

function MiniLoading() {
  return (
    <div
      role="status"
      aria-label="Загрузка операторского снимка"
      className="space-y-4 p-4"
    >
      {[90, 180, 320, 240].map((height, index) => (
        <div
          key={index}
          className="animate-pulse rounded-[var(--radius-3)] bg-bg-2 motion-reduce:animate-none"
          style={{ height }}
        />
      ))}
    </div>
  );
}
