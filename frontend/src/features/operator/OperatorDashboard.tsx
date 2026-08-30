import {
  AlertTriangle,
  ArrowLeft,
  ArrowRight,
  Check,
  CircleHelp,
  Clock3,
  OctagonAlert,
  RefreshCw,
  X,
  type LucideIcon,
} from "lucide-react";
import { Link } from "@tanstack/react-router";
import { useState, type ReactNode } from "react";

import { useOperatorRealtimeStatus } from "@fb/operator-api";
import { DataStateBadge, StopProximityReadout } from "@fb/operator-ui";
import { confirmedOperatorCurrency } from "@fb/shared/operator/adsViewModel";
import { operatorActionReason } from "@fb/shared/operator/actionLabels";
import { safeOperatorAttentionHref } from "@fb/shared/operator/attentionNavigation";
import {
  completeOperatorCommandIntent,
  getOrCreateOperatorCommandIntent,
} from "@fb/shared/operator/commandIntent";
import {
  formatOperatorDateTime as formatDateTime,
  formatOperatorFreshness as freshnessLabel,
  formatOperatorScaleTick as formatScaleTick,
  formatOperatorUsd as formatUsd,
  isActiveOperatorAction as isActiveAction,
  collapseConsecutiveOperatorActions,
  collapseOperatorAttentionItems,
  operatorAttentionCopy,
  operatorCabinetDisplayName,
  operatorCabinetTimezone,
  operatorLedgerTimezone,
  operatorReasonNoun as pluralReason,
  operatorSourceLabel as sourceLabel,
} from "@fb/shared/operator/ledgerSemantics";
import {
  buildOperatorPortfolioScale,
  findOperatorCabinetLedgerRow,
  operatorPortfolioScalePosition,
} from "@fb/shared/operator/portfolioModel";
import { describeStopProximity } from "@fb/shared/operator/stopProximity";
import { OPERATOR_ADS_STOP_PROXIMITY_SORT } from "@fb/shared/operator/routeFilters";
import {
  operatorReloginRecovery,
  RELOGIN_RECOVERY_BUTTON_LABEL,
  RELOGIN_RECOVERY_BUTTON_TONE,
  reloginRecoveryButtonState,
} from "@fb/shared/operator/reloginRecovery";
import type {
  DataState,
  OperatorActionItem,
  OperatorAttentionItem,
  OperatorCabinetLedgerRow,
  OperatorCommandResponse,
  OperatorCurrencyGroup,
  OperatorFunnelData,
  OperatorSection,
  OperatorSeverity,
  OperatorSnapshot,
} from "@fb/shared/operator/contracts";
import {
  ACTION_STATE_LABEL,
  DATA_STATE_LABEL,
  SEVERITY_LABEL,
  snapshotForRealtimeState,
  snapshotHeadline,
  snapshotOverviewState,
} from "@fb/shared/operator/viewModel";
import { russianCountForm } from "@fb/shared";

import { Button } from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/Skeleton";
import { OperatorUnavailableState } from "@/components/layout/OperatorPageBoundary";
import { toast } from "@/components/ui/toastStore";
import {
  operatorProblemMessage,
  useOperatorCabinetSnapshot,
  useOperatorRetryScan,
  useOperatorSnapshot,
} from "@/lib/api/operator";

import { OperatorCabinetAdsSection } from "./OperatorAds";
import { ScanningControl } from "./ScanningControl";

import "./operator-ledger.css";

// critical и warning не должны различаться одним лишь цветом:
// восьмиугольник читается как «стоп» и при цветовой слепоте.
const SEVERITY_ICON: Record<OperatorSeverity, LucideIcon> = {
  ok: Check,
  warning: AlertTriangle,
  critical: OctagonAlert,
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

export function OperatorDashboard() {
  const snapshotQuery = useOperatorSnapshot({ window: "today" });
  return <OperatorLedgerScreen snapshotQuery={snapshotQuery} />;
}

interface SnapshotQueryLike {
  data?: OperatorSnapshot;
  isLoading: boolean;
  isError: boolean;
  error: unknown;
  refetch: () => unknown;
}

function OperatorLedgerScreen({ snapshotQuery }: { snapshotQuery: SnapshotQueryLike }) {
  const retryScan = useOperatorRetryScan();
  const realtimeStatus = useOperatorRealtimeStatus();
  const [scanReceipt, setScanReceipt] = useState<{
    incidentId: string;
    receipt: OperatorCommandResponse;
  } | null>(null);
  const [failedIncidentId, setFailedIncidentId] = useState<string | null>(null);

  if (snapshotQuery.isLoading && !snapshotQuery.data) {
    return (
      <OperatorLedgerStateFrame>
        <OperatorDashboardSkeleton />
      </OperatorLedgerStateFrame>
    );
  }
  if (snapshotQuery.isError || !snapshotQuery.data) {
    return (
      <OperatorLedgerStateFrame>
        <OperatorUnavailableState
          title="Операторский снимок недоступен"
          resource="операторский снимок"
          details={operatorProblemMessage(snapshotQuery.error)}
          onRetry={() => void snapshotQuery.refetch()}
        />
      </OperatorLedgerStateFrame>
    );
  }

  const snapshot = snapshotForRealtimeState(snapshotQuery.data, realtimeStatus === "connected");
  const overviewState = snapshotOverviewState(snapshot);
  const headline = snapshotHeadline(snapshot);
  const StatusIcon = SEVERITY_ICON[headline.severity];
  const displayTimezone = operatorLedgerTimezone(snapshot);
  const usdScopeConfirmed = confirmedOperatorCurrency(snapshot.meta) === "USD";
  const recovery = operatorReloginRecovery(snapshot);
  const currentReceipt =
    recovery && scanReceipt?.incidentId === recovery.incident.id ? scanReceipt.receipt : null;
  const receiptAction = currentReceipt
    ? snapshot.actions.data?.items.find((item) => item.id === String(currentReceipt.task_id))
    : null;
  const recoveryState = recovery
    ? reloginRecoveryButtonState({
        actionState: currentReceipt ? receiptAction?.state : recovery.scanAction?.state,
        receiptState: currentReceipt?.state,
        requestPending: retryScan.isPending,
        requestFailed: failedIncidentId === recovery.incident.id,
      })
    : null;

  const retryInterruptedScan = async () => {
    if (!recovery) return;
    setFailedIncidentId(null);
    const incidentId = recovery.incident.id;
    try {
      const idempotencyKey = getOrCreateOperatorCommandIntent("retry_scan", incidentId);
      const receipt = await retryScan.mutateAsync({
        params: { header: { "Idempotency-Key": idempotencyKey } },
      });
      completeOperatorCommandIntent("retry_scan", incidentId, idempotencyKey);
      setScanReceipt({ incidentId, receipt });
      if (receipt.state === "queued") {
        toast.info(
          "Сканирование поставлено в очередь",
          "Завершение ещё не подтверждено. Дождитесь обновления снимка.",
        );
      } else if (receipt.state === "running") {
        toast.info("Сканирование уже выполняется", `Задача ${receipt.public_id}`);
      }
    } catch (error) {
      setFailedIncidentId(incidentId);
      toast.error("Не удалось отправить повторный скан", operatorProblemMessage(error));
    }
  };

  return (
    <div className="operator-ledger" aria-labelledby="operator-ledger-title">
      <header className="operator-ledger__header">
        <div>
          <h1 id="operator-ledger-title">Сейчас</h1>
          <p>Деньги, расхождения и выполняемые команды — одна проверяемая картина.</p>
        </div>
        <div className="operator-ledger__header-tools">
          <ScanningControl system={snapshot.system} />
          <Link className="ledger-proof-stamp" to="/system/sources">
            <StatusIcon size={16} aria-hidden="true" />
            <span>{DATA_STATE_LABEL[overviewState]}</span>
            <span className="ledger-proof-stamp__time">
              {freshnessLabel(snapshot.portfolio.freshness_seconds)}
            </span>
          </Link>
          {recovery && recoveryState ? (
            <Button
              variant={
                RELOGIN_RECOVERY_BUTTON_TONE[recoveryState] === "warning"
                  ? "warning"
                  : "secondary"
              }
              size="lg"
              leftIcon={<RefreshCw size={16} aria-hidden="true" />}
              loading={retryScan.isPending || recoveryState === "running"}
              disabled={recoveryState === "sent"}
              onClick={() => void retryInterruptedScan()}
              className="min-h-11"
              data-state={recoveryState}
              aria-live="polite"
            >
              {RELOGIN_RECOVERY_BUTTON_LABEL[recoveryState]}
            </Button>
          ) : null}
        </div>
      </header>

      <div
        className="operator-ledger__status"
        data-severity={headline.severity}
        role={headline.severity === "critical" ? "alert" : "status"}
      >
        <StatusIcon size={18} aria-hidden="true" />
        <div className="operator-ledger__status-copy">
          <strong>{headline.title}</strong>
          <p>{headline.detail}</p>
        </div>
        <span className="ledger-proof-stamp__time">
          Снимок на {formatDateTime(snapshot.meta.generated_at, displayTimezone)}
        </span>
      </div>

      <div className="operator-ledger__grid">
        <PortfolioLedger
          section={snapshot.portfolio}
          snapshot={snapshot}
          timezone={displayTimezone}
        />
        <AttentionLedger
          section={snapshot.attention}
          timezone={displayTimezone}
          usdScopeConfirmed={usdScopeConfirmed}
        />
        <ApproachingStopLedger
          section={snapshot.approaching_stop}
          currency={usdScopeConfirmed ? "USD" : null}
          timezone={displayTimezone}
        />
        <FunnelLedger
          section={snapshot.funnel}
          currency={snapshot.meta.currency ?? null}
          timezone={displayTimezone}
        />
        <ActionJournal section={snapshot.actions} />
      </div>
    </div>
  );
}

function OperatorLedgerStateFrame({ children }: { children: ReactNode }) {
  return (
    <div className="operator-ledger">
      <header className="operator-ledger__header">
        <div>
          <h1 id="operator-ledger-title">Сейчас</h1>
          <p>Состояние будет показано после подтверждения операторского снимка.</p>
        </div>
      </header>
      {children}
    </div>
  );
}

/**
 * Экран кабинета (issue #344): сводка и объявления одного кабинета, а не
 * уменьшенная копия портфельного дашборда. Портфель (список всех кабинетов) и
 * воронка сюда не идут — они отвечают за весь аккаунт, а не за этот кабинет;
 * список объявлений — главный элемент экрана, а не последняя секция.
 */
export function OperatorCabinetDashboard({ cabinetId }: { cabinetId: string }) {
  const snapshotQuery = useOperatorCabinetSnapshot(cabinetId, { window: "today" });
  return <OperatorCabinetScreen cabinetId={cabinetId} snapshotQuery={snapshotQuery} />;
}

function OperatorCabinetScreen({
  cabinetId,
  snapshotQuery,
}: {
  cabinetId: string;
  snapshotQuery: SnapshotQueryLike;
}) {
  const retryScan = useOperatorRetryScan();
  const realtimeStatus = useOperatorRealtimeStatus();
  const [scanReceipt, setScanReceipt] = useState<{
    incidentId: string;
    receipt: OperatorCommandResponse;
  } | null>(null);
  const [failedIncidentId, setFailedIncidentId] = useState<string | null>(null);

  if (snapshotQuery.isLoading && !snapshotQuery.data) {
    return (
      <OperatorCabinetStateFrame cabinetId={cabinetId}>
        <OperatorDashboardSkeleton />
      </OperatorCabinetStateFrame>
    );
  }
  if (snapshotQuery.isError || !snapshotQuery.data) {
    return (
      <OperatorCabinetStateFrame cabinetId={cabinetId}>
        <OperatorUnavailableState
          title="Снимок кабинета недоступен"
          resource="снимок кабинета"
          details={operatorProblemMessage(snapshotQuery.error)}
          onRetry={() => void snapshotQuery.refetch()}
        />
      </OperatorCabinetStateFrame>
    );
  }

  const snapshot = snapshotForRealtimeState(snapshotQuery.data, realtimeStatus === "connected");
  const overviewState = snapshotOverviewState(snapshot);
  const cabinetTimezone = operatorCabinetTimezone(snapshot, cabinetId);
  const displayTimezone = operatorLedgerTimezone(snapshot, cabinetId);
  const usdScopeConfirmed = confirmedOperatorCurrency(snapshot.meta) === "USD";
  const pageTitle = snapshot.meta.account.name ?? `Кабинет ${operatorCabinetDisplayName(cabinetId)}`;
  const cabinetCurrencyLabel = usdScopeConfirmed ? "USD" : "USD не подтверждён";
  const pageDescription = `${cabinetCurrencyLabel} · ${cabinetTimezone ?? "часовой пояс не подтверждён"} · контроль кабинета`;
  const OverviewIcon =
    SEVERITY_ICON[
      overviewState === "ready" || overviewState === "empty"
        ? "ok"
        : overviewState === "partial"
          ? "warning"
          : "unknown"
    ];

  // Снимок уже сужен до этого кабинета (account_id=cabinet_id на бэкенде), а
  // значит инцидент разлогина в attention принадлежит именно ему — баннер
  // больше не глушится безусловным `cabinetId ? null : ...`.
  const recovery = operatorReloginRecovery(snapshot);
  const currentReceipt =
    recovery && scanReceipt?.incidentId === recovery.incident.id ? scanReceipt.receipt : null;
  const receiptAction = currentReceipt
    ? snapshot.actions.data?.items.find((item) => item.id === String(currentReceipt.task_id))
    : null;
  const recoveryState = recovery
    ? reloginRecoveryButtonState({
        actionState: currentReceipt ? receiptAction?.state : recovery.scanAction?.state,
        receiptState: currentReceipt?.state,
        requestPending: retryScan.isPending,
        requestFailed: failedIncidentId === recovery.incident.id,
      })
    : null;

  const retryInterruptedScan = async () => {
    if (!recovery) return;
    setFailedIncidentId(null);
    const incidentId = recovery.incident.id;
    try {
      const idempotencyKey = getOrCreateOperatorCommandIntent("retry_scan", incidentId);
      const receipt = await retryScan.mutateAsync({
        params: { header: { "Idempotency-Key": idempotencyKey } },
      });
      completeOperatorCommandIntent("retry_scan", incidentId, idempotencyKey);
      setScanReceipt({ incidentId, receipt });
      if (receipt.state === "queued") {
        toast.info(
          "Сканирование поставлено в очередь",
          "Завершение ещё не подтверждено. Дождитесь обновления снимка.",
        );
      } else if (receipt.state === "running") {
        toast.info("Сканирование уже выполняется", `Задача ${receipt.public_id}`);
      }
    } catch (error) {
      setFailedIncidentId(incidentId);
      toast.error("Не удалось отправить повторный скан", operatorProblemMessage(error));
    }
  };

  return (
    <div className="operator-ledger" aria-labelledby="operator-cabinet-title">
      <header className="operator-ledger__header">
        <div>
          <Link className="operator-ledger__back" to="/">
            <ArrowLeft size={14} aria-hidden="true" /> Портфель
          </Link>
          <h1 id="operator-cabinet-title">{pageTitle}</h1>
          <p>{pageDescription}</p>
        </div>
        <div className="operator-ledger__header-tools">
          <Link className="ledger-proof-stamp" to="/system/sources">
            <OverviewIcon size={16} aria-hidden="true" />
            <span>{DATA_STATE_LABEL[overviewState]}</span>
            <span className="ledger-proof-stamp__time">
              {freshnessLabel(snapshot.portfolio.freshness_seconds)}
            </span>
          </Link>
          {recovery && recoveryState ? (
            <Button
              variant={
                RELOGIN_RECOVERY_BUTTON_TONE[recoveryState] === "warning"
                  ? "warning"
                  : "secondary"
              }
              size="lg"
              leftIcon={<RefreshCw size={16} aria-hidden="true" />}
              loading={retryScan.isPending || recoveryState === "running"}
              disabled={recoveryState === "sent"}
              onClick={() => void retryInterruptedScan()}
              className="min-h-11"
              data-state={recoveryState}
              aria-live="polite"
            >
              {RELOGIN_RECOVERY_BUTTON_LABEL[recoveryState]}
            </Button>
          ) : null}
        </div>
      </header>

      <CabinetMoneyStrip
        section={snapshot.portfolio}
        cabinetId={cabinetId}
        usdScopeConfirmed={usdScopeConfirmed}
      />

      <div className="operator-cabinet__stack">
        <AttentionLedger
          section={snapshot.attention}
          timezone={displayTimezone}
          usdScopeConfirmed={usdScopeConfirmed}
        />
        <OperatorCabinetAdsSection
          cabinetId={cabinetId}
          currency={usdScopeConfirmed ? "USD" : null}
        />
        <ActionJournal section={snapshot.actions} />
      </div>
    </div>
  );
}

function OperatorCabinetStateFrame({
  cabinetId,
  children,
}: {
  cabinetId: string;
  children: ReactNode;
}) {
  return (
    <div className="operator-ledger">
      <header className="operator-ledger__header">
        <div>
          <Link className="operator-ledger__back" to="/">
            <ArrowLeft size={14} aria-hidden="true" /> Портфель
          </Link>
          <h1 id="operator-cabinet-title">{`Кабинет ${cabinetId}`}</h1>
          <p>Состояние кабинета будет показано после подтверждения снимка.</p>
        </div>
      </header>
      {children}
    </div>
  );
}

/**
 * Компактная шапка кабинета: расход/база/стоп одной строкой и превышение,
 * если оно подтверждено сервером (`risk_label`/`risk_reason`). Отдельная
 * секция «Портфель» со шкалой по всем кабинетам сюда не переносится — этот
 * блок отвечает только за один кабинет.
 */
function CabinetMoneyStrip({
  section,
  cabinetId,
  usdScopeConfirmed,
}: {
  section: OperatorSnapshot["portfolio"];
  cabinetId: string;
  usdScopeConfirmed: boolean;
}) {
  const row = findOperatorCabinetLedgerRow(section.data, cabinetId);
  const usdConfirmed = usdScopeConfirmed && row?.currency === "USD";
  const totals =
    row && usdConfirmed
      ? row.totals
      : { spend: null, base: null, stop: null, base_delta: null };
  const Icon = row ? SEVERITY_ICON[row.severity] : SEVERITY_ICON.unknown;

  return (
    <section
      className="ledger-section ledger-section--cabinet-money"
      data-state={section.state}
      aria-labelledby="cabinet-money-title"
    >
      <div className="ledger-section__header">
        <div className="ledger-section__title">
          <h2 id="cabinet-money-title">Бюджет кабинета</h2>
        </div>
        {row ? (
          <span className="ledger-cabinet__state" data-severity={row.severity}>
            <Icon size={14} aria-hidden="true" />
            {row.risk_label}
          </span>
        ) : (
          <span>{DATA_STATE_LABEL[section.state]}</span>
        )}
      </div>
      <LedgerSectionIssue section={section} />
      {row?.risk_reason ? (
        <div className="ledger-section__issue" data-severity={row.severity} role="status">
          <Icon size={15} aria-hidden="true" />
          <div>
            <strong>{row.risk_label}</strong>
            <span>{row.risk_reason}</span>
          </div>
        </div>
      ) : null}
      {!row ? (
        <LedgerEmpty text={DATA_STATE_LABEL[section.state]} />
      ) : (
        <dl className="ledger-group__totals">
          <LedgerTotal label="Расход" value={totals.spend} />
          <LedgerTotal label="База" value={totals.base} />
          <LedgerTotal label="Стоп" value={totals.stop} stop />
        </dl>
      )}
    </section>
  );
}

function PortfolioLedger({
  section,
  snapshot,
  timezone,
}: {
  section: OperatorSnapshot["portfolio"];
  snapshot: OperatorSnapshot;
  timezone: string | null;
}) {
  const groups = section.data?.currency_groups ?? [];
  return (
    <section
      className="ledger-section ledger-section--portfolio"
      data-state={section.state}
      aria-labelledby="portfolio-ledger-title"
    >
      <LedgerSectionHeader
        id="portfolio-ledger-title"
        title="Портфель"
        detail={snapshot.meta.window === "today" ? "сегодня" : snapshot.meta.window}
        section={section}
        timezone={timezone}
      />
      <LedgerSectionIssue section={section} />
      {!section.data ? (
        <LedgerEmpty text={DATA_STATE_LABEL[section.state]} />
      ) : groups.length === 0 ? (
        <LedgerEmpty text="Кабинеты ещё не добавлены." />
      ) : (
        groups.map((group) => (
          <CurrencyLedgerGroup
            key={group.id}
            group={group}
            showLabel={groups.length > 1}
            usdScopeConfirmed={
              snapshot.meta.currency_state === "single" && snapshot.meta.currency === "USD"
            }
          />
        ))
      )}
    </section>
  );
}

function CurrencyLedgerGroup({
  group,
  showLabel,
  usdScopeConfirmed,
}: {
  group: OperatorCurrencyGroup;
  showLabel: boolean;
  usdScopeConfirmed: boolean;
}) {
  const scale = buildOperatorPortfolioScale(group, usdScopeConfirmed);
  const { usdConfirmed } = scale;
  const totals = usdConfirmed
    ? group.totals
    : { spend: null, base: null, stop: null, base_delta: null };

  return (
    <div className="ledger-group" data-state={group.state}>
      {showLabel ? (
        <div className="ledger-section__header">
          <div className="ledger-section__title">
            <h3>{usdConfirmed ? "Бюджет · USD" : "Валюта не подтверждена"}</h3>
          </div>
          <StateLabel state={usdConfirmed ? group.state : "partial"} />
        </div>
      ) : null}
      {!usdConfirmed ? (
        <div className="ledger-section__issue" data-severity="warning" role="status">
          <AlertTriangle size={15} aria-hidden="true" />
          <div>
            <strong>Валюта не подтверждена</strong>
            <span>FB Agent показывает бюджеты только в долларах; суммы скрыты.</span>
          </div>
        </div>
      ) : null}
      <dl className="ledger-group__totals">
        <LedgerTotal label="Расход" value={totals.spend} />
        <LedgerTotal label="База" value={totals.base} />
        <LedgerTotal label="Стоп" value={totals.stop} stop />
      </dl>
      <div className="ledger-axis" aria-hidden="true">
        <span>Кабинет</span>
        <span className="ledger-axis__ticks">
          {scale.usdConfirmed
            ? scale.ticks.map((tick) => <span key={tick}>{formatScaleTick(tick)}</span>)
            : null}
        </span>
        <span style={{ textAlign: "right" }}>Состояние</span>
      </div>
      <div
        role="list"
        aria-label={
          usdConfirmed
            ? "Кабинеты с подтверждённым долларовым контекстом"
            : "Кабинеты без подтверждённой валюты"
        }
      >
        {group.cabinets.map((cabinet) => (
          <CabinetLedgerRow
            key={cabinet.id}
            cabinet={cabinet}
            scale={scale}
            usdConfirmed={usdConfirmed}
          />
        ))}
      </div>
    </div>
  );
}

function CabinetLedgerRow({
  cabinet,
  scale,
  usdConfirmed,
}: {
  cabinet: OperatorCabinetLedgerRow;
  scale: ReturnType<typeof buildOperatorPortfolioScale>;
  usdConfirmed: boolean;
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
  const basePercent = operatorPortfolioScalePosition(showScale ? cabinet.totals.base : null, scale);
  const stopPercent = operatorPortfolioScalePosition(showScale ? cabinet.totals.stop : null, scale);

  return (
    <div
      className="ledger-cabinet"
      data-severity={cabinet.severity}
      data-state={cabinet.state}
      role="listitem"
    >
      <div className="ledger-cabinet__identity">
        <Link
          to="/cabinets/$cabinetId"
          params={{ cabinetId: cabinet.id }}
          aria-label={`${cabinet.action.label}: ${operatorCabinetDisplayName(cabinet.name)}`}
        >
          <strong>{operatorCabinetDisplayName(cabinet.name)}</strong>
          <span>
            {usdConfirmed ? "USD" : "валюта не подтверждена"} ·{" "}
            {cabinet.timezone ?? "часовой пояс не подтверждён"}
          </span>
        </Link>
      </div>
      <div className="ledger-scale" data-state={cabinet.state}>
        <span className="ledger-scale__baseline" aria-hidden="true" />
        {actualPercent !== null ? (
          <>
            <span
              className="ledger-scale__actual"
              style={{ width: `${actualPercent}%` }}
              aria-hidden="true"
            />
            <span
              className="ledger-scale__value"
              data-align={actualPercent > 82 ? "end" : "start"}
              style={{ left: `${actualPercent}%` }}
            >
              {formatUsd(cabinet.totals.spend)}
            </span>
          </>
        ) : (
          <span className="ledger-scale__unknown">
            {cabinet.state === "stale" ? "снимок устарел" : "данные не подтверждены"}
          </span>
        )}
        {basePercent !== null ? (
          <span
            className="ledger-scale__marker"
            style={{ left: `${basePercent}%` }}
            title={`База ${formatUsd(cabinet.totals.base)}`}
            aria-hidden="true"
          />
        ) : null}
        {stopPercent !== null ? (
          <span
            className="ledger-scale__marker ledger-scale__marker--stop"
            style={{ left: `${stopPercent}%` }}
            title={`Стоп ${formatUsd(cabinet.totals.stop)}`}
            aria-hidden="true"
          />
        ) : null}
        <span className="sr-only">
          Расход {formatUsd(showScale ? cabinet.totals.spend : null)}, база{" "}
          {formatUsd(showScale ? cabinet.totals.base : null)}, стоп{" "}
          {formatUsd(showScale ? cabinet.totals.stop : null)}.
        </span>
      </div>
      <div className="ledger-cabinet__state" data-severity={cabinet.severity}>
        <Icon size={14} aria-hidden="true" />
        <span>{cabinet.risk_label}</span>
      </div>
    </div>
  );
}

function AttentionLedger({
  section,
  timezone,
  usdScopeConfirmed,
}: {
  section: OperatorSnapshot["attention"];
  timezone: string | null;
  usdScopeConfirmed: boolean;
}) {
  // Счётчик считается по полному списку, а не по первым пяти карточкам.
  // Без подтверждённых данных выводим «—»: ноль означал бы «причин нет».
  const total = section.data ? section.data.items.length : null;
  const items = collapseOperatorAttentionItems(section.data?.items ?? [], usdScopeConfirmed).slice(
    0,
    5,
  );
  return (
    <section
      className="ledger-section ledger-section--attention"
      data-state={section.state}
      aria-labelledby="attention-ledger-title"
    >
      <LedgerSectionHeader
        id="attention-ledger-title"
        title="Требует внимания"
        detail={total === null ? "—" : `${total} ${pluralReason(total)}`}
        section={section}
        timezone={timezone}
      />
      <LedgerSectionIssue section={section} />
      {!section.data ? (
        <LedgerEmpty text={DATA_STATE_LABEL[section.state]} />
      ) : items.length === 0 ? (
        <LedgerEmpty text="Активных сигналов нет." />
      ) : (
        <ol className="ledger-attention-list">
          {items.map(({ item, count }) => (
            <AttentionLedgerItem
              key={item.id}
              item={item}
              count={count}
              timezone={timezone}
              usdScopeConfirmed={usdScopeConfirmed}
            />
          ))}
        </ol>
      )}
      <Link className="ledger-attention-item__action" to="/incidents">
        Все инциденты
        <ArrowRight size={14} aria-hidden="true" />
      </Link>
    </section>
  );
}

function AttentionLedgerItem({
  item,
  count,
  timezone,
  usdScopeConfirmed,
}: {
  item: OperatorAttentionItem;
  count: number;
  timezone: string | null;
  usdScopeConfirmed: boolean;
}) {
  const Icon = SEVERITY_ICON[item.severity];
  const href = item.action ? safeOperatorAttentionHref(item.action.href) : null;
  const copy = operatorAttentionCopy(item, usdScopeConfirmed);
  return (
    <li className="ledger-attention-item" data-severity={item.severity}>
      <div className="ledger-attention-item__head">
        <span className="ledger-attention-item__target">
          {item.target.label ?? "Объект не указан"}
        </span>
        <span className="ledger-attention-item__severity" data-severity={item.severity}>
          <Icon size={14} aria-hidden="true" />
          {SEVERITY_LABEL[item.severity]}
        </span>
      </div>
      <h3>
        {copy.title}
        {count > 1 ? (
          <span className="ledger-attention-item__count" data-numeric>
            {count}
          </span>
        ) : null}
      </h3>
      {copy.summary ? <p>{copy.summary}</p> : null}
      {copy.reason ? <p>Причина: {copy.reason}</p> : null}
      {item.action && href === "/system/sources" ? (
        <Link className="ledger-attention-item__action" to="/system/sources">
          {item.action.label}
          <ArrowRight size={14} aria-hidden="true" />
        </Link>
      ) : item.action && href ? (
        <a className="ledger-attention-item__action" href={href}>
          {item.action.label}
          <ArrowRight size={14} aria-hidden="true" />
        </a>
      ) : null}
      <time className="sr-only" dateTime={item.occurred_at}>
        {formatDateTime(item.occurred_at, timezone)}
      </time>
    </li>
  );
}

/**
 * Ранний контур: объявления, которые движок уже посчитал приближающимися
 * к стопу. `empty` здесь — подтверждённое «никто не подходит», поэтому блок
 * выглядит спокойно, а не тревожно.
 */
function ApproachingStopLedger({
  section,
  currency,
  timezone,
}: {
  section: OperatorSnapshot["approaching_stop"];
  currency: string | null;
  timezone: string | null;
}) {
  const total = section.data ? section.data.items.length : null;
  const items = section.data?.items.slice(0, 5) ?? [];
  return (
    <section
      className="ledger-section ledger-section--approaching"
      data-state={section.state}
      aria-labelledby="approaching-stop-title"
    >
      <LedgerSectionHeader
        id="approaching-stop-title"
        title="Подходят к стопу"
        detail={
          section.state === "empty"
            ? "никто не подходит"
            : total === null
              ? "—"
              : `${total} ${russianCountForm(total, "объявление", "объявления", "объявлений")}`
        }
        section={section}
        timezone={timezone}
      />
      <LedgerSectionIssue section={section} />
      {!section.data ? (
        <LedgerEmpty text={DATA_STATE_LABEL[section.state]} />
      ) : items.length === 0 ? (
        <LedgerEmpty text="Ни одно объявление не подходит к стопу." />
      ) : (
        <ol className="ledger-approaching-list">
          {items.map((item) => (
            <li className="ledger-approaching-item" key={item.id}>
              <div className="ledger-approaching-item__head">
                <Link
                  to="/ads/$fbAdId"
                  params={{ fbAdId: item.fb_ad_id }}
                  aria-label={`Открыть объявление: ${item.name}`}
                >
                  {item.name}
                </Link>
                <DataStateBadge state={item.data_state} compact />
              </div>
              <span className="ledger-approaching-item__meta">{item.campaign_name}</span>
              <StopProximityReadout
                proximity={describeStopProximity(item.rule_context, { currency })}
              />
            </li>
          ))}
        </ol>
      )}
      {total ? (
        <Link
          className="ledger-attention-item__action"
          to="/ads"
          search={{ sort: OPERATOR_ADS_STOP_PROXIMITY_SORT, direction: "desc" }}
        >
          Все объявления по близости к стопу
          <ArrowRight size={14} aria-hidden="true" />
        </Link>
      ) : null}
    </section>
  );
}



function ActionJournal({ section }: { section: OperatorSnapshot["actions"] }) {
  // Счётчик считается по полному списку, а не по первым пяти карточкам.
  // Без подтверждённых данных выводим «—»: ноль означал бы «команд нет».
  const activeTotal = section.data ? section.data.items.filter(isActiveAction).length : null;
  const items = section.data?.items.slice(0, 5) ?? [];
  return (
    <section
      className="ledger-section ledger-section--actions"
      data-state={section.state}
      aria-labelledby="action-journal-title"
    >
      <LedgerSectionHeader
        id="action-journal-title"
        title="Действия"
        detail={activeTotal === null ? "—" : `${activeTotal} выполняется`}
        section={section}
      />
      <LedgerSectionIssue section={section} />
      {!section.data ? (
        <LedgerEmpty text={DATA_STATE_LABEL[section.state]} />
      ) : (
        <ActionList items={items} />
      )}
    </section>
  );
}

export function ActionList({ items }: { items: OperatorActionItem[] }) {
  if (!items.length) return <LedgerEmpty text="Активных действий нет." />;
  const groups = collapseConsecutiveOperatorActions(items);
  return (
    <ol className="ledger-action-list" aria-label="Очередь и история действий">
      {groups.map(({ item, count }) => {
        const Icon = ACTION_ICON[item.state];
        return (
          <li className="ledger-action-item" key={item.id}>
            <div className="ledger-action-item__head">
              <span className="ledger-action-item__title">
                {item.title} · {item.target_label ?? "система"}
                {count > 1 ? <span> ×{count}</span> : null}
              </span>
              <span className="ledger-action-item__state" data-state={item.state}>
                <Icon size={14} aria-hidden="true" />
                {ACTION_STATE_LABEL[item.state]}
              </span>
            </div>
            <p>{operatorActionReason(item)}</p>
            {item.state === "running" || item.state === "queued" ? (
              <div className="ledger-action-item__progress" aria-hidden="true" />
            ) : null}
            <div className="ledger-action-item__meta">
              <span>Задача {item.public_id}</span>
              {count > 1 ? (
                <span>Последний повтор {formatDateTime(item.updated_at, item.cabinet_timezone)}</span>
              ) : null}
            </div>
            {item.run_id ? (
              // У залива есть свой экран с составом, созданными объектами и
              // управлением. Вести оператора на общую карточку действия — значит
              // показать конвейер обработки вместо самой кампании.
              <Link
                className="ledger-action-item__link"
                to="/campaigns"
                search={{ run: item.run_id }}
              >
                Открыть залив
                <ArrowRight size={14} aria-hidden="true" />
              </Link>
            ) : (
              <Link
                className="ledger-action-item__link"
                to="/actions/$actionId"
                params={{ actionId: item.id }}
              >
                Открыть действие
                <ArrowRight size={14} aria-hidden="true" />
              </Link>
            )}
          </li>
        );
      })}
    </ol>
  );
}

function FunnelLedger({
  section,
  currency,
  timezone,
}: {
  section: OperatorSnapshot["funnel"];
  currency: string | null;
  timezone: string | null;
}) {
  return (
    <section
      className="ledger-section ledger-section--funnel"
      data-state={section.state}
      aria-labelledby="funnel-ledger-title"
    >
      <LedgerSectionHeader
        id="funnel-ledger-title"
        title="Воронка"
        detail={section.state === "ready" ? "полные данные" : DATA_STATE_LABEL[section.state]}
        section={section}
        timezone={timezone}
      />
      <LedgerSectionIssue section={section} />
      {!section.data ? (
        <LedgerEmpty text={DATA_STATE_LABEL[section.state]} />
      ) : (
        <FunnelStages funnel={section.data} currency={currency === "USD" ? "USD" : null} />
      )}
    </section>
  );
}

function FunnelStages({
  funnel,
  currency,
}: {
  funnel: OperatorFunnelData;
  currency: string | null;
}) {
  return (
    <ol className="ledger-funnel" aria-label="Короткая воронка">
      {funnel.stages.map((stage) => (
        <li className="ledger-funnel__stage" key={stage.key}>
          <span className="ledger-funnel__label">{stage.label}</span>
          <strong className="ledger-funnel__value">
            {stage.count === null ? "—" : stage.count.toLocaleString("ru-RU")}
          </strong>
          <span className="ledger-funnel__meta">
            <span>CR {stage.conversion === null ? "—" : `${stage.conversion}%`}</span>
            <span>{formatUsd(currency ? stage.cost : null)}</span>
          </span>
        </li>
      ))}
    </ol>
  );
}

function LedgerSectionHeader<T>({
  id,
  title,
  detail,
  section,
  timezone,
}: {
  id: string;
  title: string;
  detail: string;
  section: OperatorSection<T>;
  timezone?: string | null;
}) {
  return (
    <header className="ledger-section__header">
      <div className="ledger-section__title">
        <h2 id={id}>{title}</h2>
        <span>{detail}</span>
      </div>
      <div className="ledger-section__meta">
        <span>{section.sources.map(sourceLabel).join(" · ")}</span>
        <span data-numeric>
          {section.as_of
            ? formatDateTime(section.as_of, timezone)
            : freshnessLabel(section.freshness_seconds)}
        </span>
      </div>
    </header>
  );
}

function LedgerTotal({
  label,
  value,
  stop = false,
}: {
  label: string;
  value: string | null;
  stop?: boolean;
}) {
  return (
    <div className={`ledger-total${stop ? " ledger-total--stop" : ""}`}>
      <dt>{label}</dt>
      <dd data-known={value !== null}>{formatUsd(value)}</dd>
    </div>
  );
}

function LedgerSectionIssue<T>({ section }: { section: OperatorSection<T> }) {
  // The shell already owns the single reconnect notice; do not repeat the
  // same transport cause in every ledger section.
  const issue = section.issues.find((candidate) => candidate.code !== "REALTIME_RECONCILING");
  if (!issue) return null;
  const Icon = SEVERITY_ICON[issue.severity];
  return (
    <div className="ledger-section__issue" data-severity={issue.severity} role="status">
      <Icon size={15} aria-hidden="true" />
      <div>
        <strong>{issue.title}</strong>
        <span>{issue.detail}</span>
      </div>
    </div>
  );
}

function StateLabel({ state }: { state: DataState }) {
  const severity: OperatorSeverity =
    state === "ready" || state === "empty" ? "ok" : state === "partial" ? "warning" : "unknown";
  const Icon = SEVERITY_ICON[severity];
  return (
    <span className="ledger-cabinet__state" data-severity={severity}>
      <Icon size={14} aria-hidden="true" />
      {DATA_STATE_LABEL[state]}
    </span>
  );
}

function LedgerEmpty({ text }: { text: string }) {
  return <div className="ledger-section__empty">{text}</div>;
}

function OperatorDashboardSkeleton() {
  return (
    <div role="status" aria-label="Загрузка операторского снимка" className="space-y-5">
      <div className="space-y-3 py-3">
        <Skeleton height={48} width={180} className="rounded-[var(--radius-1)]" />
        <Skeleton height={24} width="min(620px, 90%)" className="rounded-[var(--radius-1)]" />
      </div>
      <div className="grid gap-5 lg:grid-cols-[1.55fr_.72fr]">
        <Skeleton height={430} className="rounded-[var(--radius-1)]" />
        <Skeleton height={430} className="rounded-[var(--radius-1)]" />
      </div>
    </div>
  );
}
