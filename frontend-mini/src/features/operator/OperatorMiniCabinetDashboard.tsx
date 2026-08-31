/**
 * Экран кабинета (issue #344): сводка и объявления одного кабинета, а не
 * уменьшенная копия портфельного дашборда. Портфель (список всех кабинетов) и
 * воронка сюда не идут, список объявлений — главный элемент экрана.
 */
import { Link } from "@tanstack/react-router";

import {
  DATA_STATE_LABEL,
  snapshotForRealtimeState,
  snapshotOverviewState,
} from "@fb/shared/operator/viewModel";
import { confirmedOperatorCurrency } from "@fb/shared/operator/adsViewModel";
import {
  operatorCabinetDisplayName,
  operatorCabinetTimezone,
  operatorLedgerTimezone,
} from "@fb/shared/operator/ledgerSemantics";
import { findOperatorCabinetLedgerRow } from "@fb/shared/operator/portfolioModel";
import { useOperatorRealtimeStatus } from "@fb/operator-api";
import type { OperatorSnapshot } from "@fb/shared/operator/contracts";

import { useOperatorCabinetSnapshot } from "@/lib/operatorApi";

import { MiniCabinetAdsSection } from "./OperatorAds";
import {
  MiniActionJournal,
  MiniAttentionLedger,
  MiniLedgerEmpty,
  MiniLedgerSection,
  MiniLedgerTools,
  MiniLedgerTotal,
  MiniLoading,
  MiniScanReceipt,
  MiniSnapshotError,
  SEVERITY_ICON,
  useMiniScanRecovery,
  useOpenAttentionAction,
  type SnapshotQueryLike,
} from "./MiniLedgerParts";

export function OperatorMiniCabinetDashboard({
  cabinetId,
}: {
  cabinetId: string;
}) {
  const snapshotQuery = useOperatorCabinetSnapshot(cabinetId, {
    window: "today",
  });
  return (
    <OperatorMiniCabinetScreen
      cabinetId={cabinetId}
      snapshotQuery={snapshotQuery}
    />
  );
}

function OperatorMiniCabinetScreen({
  cabinetId,
  snapshotQuery,
}: {
  cabinetId: string;
  snapshotQuery: SnapshotQueryLike;
}) {
  const realtimeStatus = useOperatorRealtimeStatus();
  const snapshot = snapshotQuery.data
    ? snapshotForRealtimeState(snapshotQuery.data, realtimeStatus === "connected")
    : null;
  // Снимок уже сужен до этого кабинета (account_id=cabinet_id на бэкенде),
  // поэтому инцидент разлогина в attention принадлежит именно ему — баннер
  // больше не глушится безусловным `cabinetId ? null : ...`.
  const { recovery, recoveryState, currentReceipt, scanPending, runScan } =
    useMiniScanRecovery(snapshot, snapshotQuery.refetch);
  const openAttentionAction = useOpenAttentionAction();

  if (snapshotQuery.isLoading && !snapshotQuery.data) {
    return <MiniLoading />;
  }
  if (snapshotQuery.isError || !snapshot) {
    return (
      <MiniSnapshotError
        title="Снимок кабинета недоступен"
        error={snapshotQuery.error}
        onRetry={() => void snapshotQuery.refetch()}
      />
    );
  }

  const overviewState = snapshotOverviewState(snapshot);
  const StatusIcon =
    SEVERITY_ICON[
      overviewState === "ready" || overviewState === "empty"
        ? "ok"
        : overviewState === "partial"
          ? "warning"
          : "unknown"
    ];
  const cabinetTimezone = operatorCabinetTimezone(snapshot, cabinetId);
  const displayTimezone = operatorLedgerTimezone(snapshot, cabinetId);
  const usdScopeConfirmed = confirmedOperatorCurrency(snapshot.meta) === "USD";
  const pageTitle =
    snapshot.meta.account.name ??
    `Кабинет ${operatorCabinetDisplayName(cabinetId)}`;
  const cabinetCurrencyLabel = usdScopeConfirmed ? "USD" : "USD не подтверждён";
  const pageDetail = `${cabinetCurrencyLabel} · ${cabinetTimezone ?? "часовой пояс не подтверждён"}`;

  return (
    <div className="mini-ledger" aria-labelledby="mini-cabinet-title">
      <header className="mini-ledger__header">
        <div>
          <Link className="mini-ledger__inline-action" to="/">
            Портфель
          </Link>
          <h1 id="mini-cabinet-title">{pageTitle}</h1>
          <p>{pageDetail}</p>
        </div>
        <MiniLedgerTools
          overviewState={overviewState}
          freshnessSeconds={snapshot.portfolio.freshness_seconds}
          StatusIcon={StatusIcon}
          recovery={recovery}
          recoveryState={recoveryState}
          scanPending={scanPending}
          onScan={runScan}
        />
      </header>

      {currentReceipt ? <MiniScanReceipt receipt={currentReceipt} /> : null}

      <MiniCabinetMoneyStrip
        section={snapshot.portfolio}
        cabinetId={cabinetId}
        usdScopeConfirmed={usdScopeConfirmed}
      />

      <div className="mini-ledger__flow">
        <MiniAttentionLedger
          section={snapshot.attention}
          timezone={displayTimezone}
          usdScopeConfirmed={usdScopeConfirmed}
          onAction={openAttentionAction}
        />
        <MiniCabinetAdsSection
          cabinetId={cabinetId}
          currency={usdScopeConfirmed ? "USD" : null}
        />
        <MiniActionJournal section={snapshot.actions} />
      </div>
    </div>
  );
}

/**
 * Компактная шапка кабинета: расход/база/стоп одной строкой и превышение,
 * если оно подтверждено сервером (`risk_label`/`risk_reason`). Отдельная
 * секция «Портфель» со шкалой по всем кабинетам сюда не переносится.
 */
function MiniCabinetMoneyStrip({
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
    <MiniLedgerSection
      className="mini-ledger-section--cabinet-money"
      id="mini-cabinet-money-title"
      title="Бюджет кабинета"
      detail={row ? row.risk_label : DATA_STATE_LABEL[section.state]}
      section={section}
    >
      {row?.risk_reason ? (
        <div
          className="mini-ledger-section__issue"
          data-severity={row.severity}
          role="status"
        >
          <Icon size={15} aria-hidden="true" />
          <div>
            <strong>{row.risk_label}</strong>
            <span>{row.risk_reason}</span>
          </div>
        </div>
      ) : null}
      {!row ? (
        <MiniLedgerEmpty text={DATA_STATE_LABEL[section.state]} />
      ) : (
        <dl className="mini-ledger-totals">
          <MiniLedgerTotal label="Расход" value={totals.spend} />
          <MiniLedgerTotal label="База" value={totals.base} />
          <MiniLedgerTotal label="Стоп" value={totals.stop} stop />
        </dl>
      )}
    </MiniLedgerSection>
  );
}
