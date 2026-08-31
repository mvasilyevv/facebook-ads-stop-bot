/**
 * Портфельный экран «Сейчас» — стартовый экран мини-приложения. Всё, что нужно
 * только другим экранам (кабинет, объявления, список действий), живёт в
 * отдельных модулях: иначе оно попадает в стартовый чанк (issue #349).
 */
import { AlertTriangle, ArrowRight } from "lucide-react";
import { Link, useNavigate } from "@tanstack/react-router";

import {
  DATA_STATE_LABEL,
  snapshotForRealtimeState,
  snapshotHeadline,
  snapshotOverviewState,
} from "@fb/shared/operator/viewModel";
import { useOperatorRealtimeStatus } from "@fb/operator-api";
import { DataStateBadge, StopProximityReadout } from "@fb/operator-ui";
import { confirmedOperatorCurrency } from "@fb/shared/operator/adsViewModel";
import { describeStopProximity } from "@fb/shared/operator/stopProximity";
import { OPERATOR_ADS_STOP_PROXIMITY_SORT } from "@fb/shared/operator/routeFilters";
import {
  formatOperatorDateTime as formatDateTime,
  formatOperatorScaleTick as formatScaleTick,
  formatOperatorUsd as formatUsd,
  operatorCabinetDisplayName,
  operatorLedgerTimezone,
} from "@fb/shared/operator/ledgerSemantics";
import {
  buildOperatorPortfolioScale,
  operatorPortfolioScalePosition,
} from "@fb/shared/operator/portfolioModel";
import type {
  OperatorCabinetLedgerRow,
  OperatorCurrencyGroup,
  OperatorFunnelData,
  OperatorSnapshot,
} from "@fb/shared/operator/contracts";
import { russianCountForm } from "@fb/shared";

import { haptic } from "@/lib/tg";
import { useOperatorSnapshot } from "@/lib/operatorApi";

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

export function OperatorMiniDashboard() {
  const snapshotQuery = useOperatorSnapshot({ window: "today" });
  return <OperatorMiniLedgerScreen snapshotQuery={snapshotQuery} />;
}

function OperatorMiniLedgerScreen({
  snapshotQuery,
}: {
  snapshotQuery: SnapshotQueryLike;
}) {
  const navigate = useNavigate();
  const realtimeStatus = useOperatorRealtimeStatus();
  const snapshot = snapshotQuery.data
    ? snapshotForRealtimeState(snapshotQuery.data, realtimeStatus === "connected")
    : null;
  const { recovery, recoveryState, currentReceipt, scanPending, runScan } =
    useMiniScanRecovery(snapshot, snapshotQuery.refetch);
  const openAttentionAction = useOpenAttentionAction();

  if (snapshotQuery.isLoading && !snapshotQuery.data) {
    return <MiniLoading />;
  }
  if (snapshotQuery.isError || !snapshot) {
    return (
      <MiniSnapshotError
        title="Снимок недоступен"
        error={snapshotQuery.error}
        onRetry={() => void snapshotQuery.refetch()}
      />
    );
  }

  const headline = snapshotHeadline(snapshot);
  const overviewState = snapshotOverviewState(snapshot);
  const StatusIcon = SEVERITY_ICON[headline.severity];
  const displayTimezone = operatorLedgerTimezone(snapshot);
  const usdScopeConfirmed = confirmedOperatorCurrency(snapshot.meta) === "USD";

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
          <h1 id="mini-ledger-title">Сейчас</h1>
          <p>Деньги, расхождения и команды</p>
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
          as_of {formatDateTime(snapshot.meta.generated_at, displayTimezone)}
        </span>
      </div>

      <div className="mini-ledger__flow">
        <MiniAttentionLedger
          section={snapshot.attention}
          timezone={displayTimezone}
          usdScopeConfirmed={usdScopeConfirmed}
          onAction={openAttentionAction}
        />
        <MiniApproachingStopLedger
          section={snapshot.approaching_stop}
          currency={usdScopeConfirmed ? "USD" : null}
          timezone={displayTimezone}
        />
        <MiniPortfolioLedger
          section={snapshot.portfolio}
          timezone={displayTimezone}
          usdScopeConfirmed={usdScopeConfirmed}
          onCabinet={openCabinet}
        />
        <MiniActionJournal section={snapshot.actions} />
        <MiniFunnelLedger
          section={snapshot.funnel}
          currency={snapshot.meta.currency ?? null}
          timezone={displayTimezone}
        />
      </div>
    </div>
  );
}

/**
 * Ранний контур на компактном шелле. `empty` — подтверждённое «никто не
 * подходит», поэтому блок выглядит спокойно, а не тревожно.
 */
function MiniApproachingStopLedger({
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
    <MiniLedgerSection
      className="mini-ledger-section--approaching"
      id="mini-approaching-title"
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
    >
      {!section.data ? (
        <MiniLedgerEmpty text={DATA_STATE_LABEL[section.state]} />
      ) : items.length === 0 ? (
        <MiniLedgerEmpty text="Ни одно объявление не подходит к стопу." />
      ) : (
        <ol className="mini-approaching-list">
          {items.map((item) => (
            <li key={item.id}>
              <div className="mini-approaching-item__head">
                <Link
                  to="/ads/$fbAdId"
                  params={{ fbAdId: item.fb_ad_id }}
                  aria-label={`Открыть объявление: ${item.name}`}
                >
                  {item.name}
                </Link>
                <DataStateBadge state={item.data_state} compact />
              </div>
              <span className="mini-approaching-item__meta">
                {item.campaign_name}
              </span>
              <StopProximityReadout
                proximity={describeStopProximity(item.rule_context, {
                  currency,
                })}
              />
            </li>
          ))}
        </ol>
      )}
      {total ? (
        <Link
          to="/ads"
          search={{ sort: OPERATOR_ADS_STOP_PROXIMITY_SORT, direction: "desc" }}
          className="mini-ledger__inline-action mx-4 min-h-11"
        >
          Все объявления по близости к стопу
          <ArrowRight size={14} aria-hidden="true" />
        </Link>
      ) : null}
    </MiniLedgerSection>
  );
}

function MiniPortfolioLedger({
  section,
  timezone,
  usdScopeConfirmed,
  onCabinet,
}: {
  section: OperatorSnapshot["portfolio"];
  timezone: string | null;
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
      {scale.usdConfirmed ? (
        <div className="mini-ledger-axis" aria-hidden="true">
          <span>$0</span>
          <span>{formatScaleTick(scale.maximum / 2)}</span>
          <span>{formatScaleTick(scale.maximum)}</span>
        </div>
      ) : null}
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
          <strong>{operatorCabinetDisplayName(cabinet.name)}</strong>
          <small>
            {usdConfirmed ? "USD" : "валюта не подтверждена"} ·{" "}
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

function MiniFunnelLedger({
  section,
  currency,
  timezone,
}: {
  section: OperatorSnapshot["funnel"];
  currency: string | null;
  timezone: string | null;
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
