/**
 * Лента «Решения» (issue #338, PR4/mini) — главный операционный экран TMA:
 * очередь того, что требует клика оператора СЕЙЧАС. Не хронология (она в
 * /incidents и /actions) и не второй сборщик данных — единственный источник
 * строк, порядок и применимое действие берутся из `packages/shared/operator/
 * decisionFeed.ts` (`selectDecisionRows`/`compareDecisionRows`/
 * `decisionPrimaryAction`/`decisionRowAge`/`collapseDecisionRows`/
 * `combineDecisionFeedState`) — здесь этот модуль ПОТРЕБЛЯЕТСЯ, не
 * переписывается.
 *
 * Инвариант ленты (спека eng-lead): строка — повод открыть решение, а не
 * доказательство состояния объекта. Пауза объявления идёт буква в букву тем
 * же путём, что `MiniAdCommand` в OperatorAds.tsx (ConfirmDialog → сверка
 * `fetchOperatorAdForCommand` → Idempotency-Key → 202=queued тост →
 * /actions/$actionId) — компонент переиспользуется, а не дублируется.
 */
import { useState } from "react";
import { useNavigate } from "@tanstack/react-router";
import { ArrowRight, ShieldCheck } from "lucide-react";

import {
  collapseDecisionRows,
  combineDecisionFeedState,
  compareDecisionRows,
  decisionRowAge,
  selectDecisionRows,
  type DecisionRow,
} from "@fb/shared/operator/decisionFeed";
import { snapshotForRealtimeState } from "@fb/shared/operator/viewModel";
import { confirmedOperatorCurrency } from "@fb/shared/operator/adsViewModel";
import { adsForRealtimeState } from "@fb/shared/operator/viewModel";
import { formatShownOfRussianCount } from "@fb/shared";
import { DataStateBadge, DataStateNotice } from "@fb/operator-ui";
import { useOperatorRealtimeStatus } from "@fb/operator-api";

import { MiniHeader } from "@/components/layout/MiniHeader";
import { PullToRefresh } from "@/components/layout/PullToRefresh";
import { Button, EmptyState, ErrorState, Skeleton } from "@/components/ui";
import { MiniAdCommand, MiniSeverityBadge } from "@/features/operator/OperatorAds";
import {
  operatorIncidentProblemMessage,
  operatorProblemMessage,
  useAcknowledgeOperatorIncident,
  useOperatorAds,
  useOperatorSnapshot,
} from "@/lib/operatorApi";
import {
  parseTmaAttentionHref,
  storeResolvedNavigation,
} from "@/lib/transientNavigation";
import { haptic, tgAlert } from "@/lib/tg";

export function MiniDecisionsPage() {
  const navigate = useNavigate();
  const realtimeStatus = useOperatorRealtimeStatus();
  const snapshotQuery = useOperatorSnapshot({ window: "today" });
  const acknowledge = useAcknowledgeOperatorIncident();
  const [ackId, setAckId] = useState<string | null>(null);
  const [ackError, setAckError] = useState<string | null>(null);

  async function openHref(href: string) {
    const destination = parseTmaAttentionHref(href);
    if (!destination) {
      await tgAlert("Ссылка недоступна.");
      return;
    }
    haptic.selection();
    if (destination.kind === "target") {
      storeResolvedNavigation(destination.target);
      await navigate({ to: "/open" });
      return;
    }
    await navigate({ to: destination.to });
  }

  async function acknowledgeRow(row: DecisionRow) {
    setAckError(null);
    setAckId(row.id);
    haptic.impact("medium");
    try {
      await acknowledge.mutateAsync({
        params: {
          path: { incident_id: row.id },
          header: { "X-Operator-Principal": "operator:tma" },
        },
      });
      haptic.notify("success");
      await snapshotQuery.refetch();
    } catch (error) {
      haptic.notify("error");
      setAckError(operatorIncidentProblemMessage(error));
    } finally {
      setAckId(null);
    }
  }

  if (snapshotQuery.isError && !snapshotQuery.data) {
    return (
      <div className="flex flex-col pb-5">
        <MiniHeader title="Решения" />
        <div className="px-4 py-5">
          <ErrorState
            message={operatorProblemMessage(snapshotQuery.error)}
            onRetry={() => void snapshotQuery.refetch()}
          />
        </div>
      </div>
    );
  }

  if (!snapshotQuery.data) {
    return (
      <div className="flex flex-col pb-5">
        <MiniHeader title="Решения" />
        <div
          role="status"
          aria-label="Загрузка решений"
          className="grid gap-px bg-bg-3 p-4"
        >
          {Array.from({ length: 4 }, (_, index) => (
            <Skeleton key={index} className="h-40 w-full" />
          ))}
        </div>
      </div>
    );
  }

  const snapshot = snapshotForRealtimeState(
    snapshotQuery.data,
    realtimeStatus === "connected",
  );
  const usdScopeConfirmed = confirmedOperatorCurrency(snapshot.meta) === "USD";
  const dataState = combineDecisionFeedState([snapshot.attention.state]);
  const dataReady = dataState === "ready";
  const rows = selectDecisionRows(snapshot).slice().sort(compareDecisionRows);
  const collapsed = collapseDecisionRows(rows, usdScopeConfirmed);
  const attentionData = snapshot.attention.data;
  const truncated = Boolean(attentionData?.truncated);
  const shownOfTotal =
    attentionData && truncated
      ? formatShownOfRussianCount(
          attentionData.items.length,
          attentionData.total,
          "сигнал",
          "сигнала",
          "сигналов",
        )
      : null;

  return (
    <div className="flex flex-col pb-5">
      <MiniHeader
        title="Решения"
        right={<DataStateBadge state={dataState} compact />}
      />
      <PullToRefresh onRefresh={() => snapshotQuery.refetch()}>
        <div className="px-4 pb-3 pt-3">
          <p className="text-[14px] leading-5 text-bg-9">
            Очередь того, что требует клика сейчас. Хронология — в «Инцидентах»
            и «Действиях».
          </p>
        </div>

        {dataState !== "ready" && dataState !== "empty" ? (
          <div className="px-4 pb-3">
            <DataStateNotice
              state={dataState}
              issues={snapshot.attention.issues}
              compact
            />
          </div>
        ) : null}

        {shownOfTotal ? (
          <div
            role="status"
            className="mx-4 mb-3 rounded-[var(--radius-2)] border border-warning/35 bg-warning-bg px-3 py-3 text-[13px] font-semibold text-warning"
          >
            Показано {shownOfTotal} — сервер режет список; полный список в
            «Инцидентах» и «Действиях».
          </div>
        ) : null}

        {ackError ? (
          <p
            role="alert"
            className="mx-4 mb-3 border-y border-danger/35 bg-danger-bg px-3 py-3 text-[14px] text-danger"
          >
            {ackError}
          </p>
        ) : null}

        <section
          aria-label="Лента решений"
          className="border-y border-[var(--color-hairline)]"
        >
          {collapsed.length > 0 ? (
            <ol className="m-0 divide-y divide-[var(--color-hairline)] p-0">
              {collapsed.map(({ row, count }) => (
                <DecisionRowCard
                  key={row.id}
                  row={row}
                  count={count}
                  usdScopeConfirmed={usdScopeConfirmed}
                  dataReady={dataReady}
                  acking={ackId === row.id}
                  onAcknowledge={acknowledgeRow}
                  onOpenHref={(href) => void openHref(href)}
                />
              ))}
            </ol>
          ) : dataState === "empty" || dataState === "ready" ? (
            <div className="px-4">
              <EmptyState
                title="Решений нет"
                description="Сервер подтвердил: решений нет — крутить нечего."
              />
            </div>
          ) : (
            <div className="px-4">
              <EmptyState
                title="Лента не подтверждена"
                description="Обновите данные. Неизвестный список не считается пустым."
              />
            </div>
          )}
        </section>
      </PullToRefresh>
    </div>
  );
}

/** Дежурные заголовки для строки без подтверждённого USD-скоупа по её же
 * признаку (`requires_usd_evidence`), а не по глобальному флагу валюты —
 * см. дефект в спеке issue #338 («денежная копия глушится по глобальному
 * состоянию, должна — по признаку строки»). */
const GENERIC_TITLE: Record<DecisionRow["kind"], string> = {
  incident: "Сигнал требует проверки",
  action: "Команда требует сверки",
  source: "Источник требует проверки",
};

function decisionRowCopy(
  row: DecisionRow,
  usdScopeConfirmed: boolean,
): { title: string; context: string | null } {
  if (row.requiresUsdEvidence && !usdScopeConfirmed) {
    return { title: GENERIC_TITLE[row.kind], context: null };
  }
  return { title: row.title, context: row.contextLine || null };
}

function DecisionRowCard({
  row,
  count,
  usdScopeConfirmed,
  dataReady,
  acking,
  onAcknowledge,
  onOpenHref,
}: {
  row: DecisionRow;
  count: number;
  usdScopeConfirmed: boolean;
  dataReady: boolean;
  acking: boolean;
  onAcknowledge: (row: DecisionRow) => void;
  onOpenHref: (href: string) => void;
}) {
  const copy = decisionRowCopy(row, usdScopeConfirmed);
  const age = decisionRowAge(row, Date.now());
  return (
    <li className="bg-bg-0 px-4 py-4" data-severity={row.severity}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <MiniSeverityBadge severity={row.severity} />
        <span
          className="font-numeric text-[12px] text-bg-8"
          title="Время с момента появления строки"
        >
          Висит {age}
        </span>
      </div>
      <p className="mt-3 text-[14px] text-bg-9">
        {row.target.label ?? "Объект не указан"}
        {count > 1 ? ` · ${count}×` : ""}
      </p>
      <h2 className="m-0 mt-1 text-[18px] font-semibold leading-6 text-bg-11">
        {copy.title}
      </h2>
      {copy.context ? (
        <p className="mt-2 text-[14px] leading-5 text-bg-10">{copy.context}</p>
      ) : null}
      <div className="mt-4">
        <DecisionRowAction
          row={row}
          dataReady={dataReady}
          acking={acking}
          onAcknowledge={onAcknowledge}
          onOpenHref={onOpenHref}
        />
      </div>
    </li>
  );
}

function DecisionRowAction({
  row,
  dataReady,
  acking,
  onAcknowledge,
  onOpenHref,
}: {
  row: DecisionRow;
  dataReady: boolean;
  acking: boolean;
  onAcknowledge: (row: DecisionRow) => void;
  onOpenHref: (href: string) => void;
}) {
  const action = row.primaryAction;
  if (action?.kind === "pause") {
    return <DecisionPauseAction adId={action.adId} />;
  }
  if (action?.kind === "acknowledge") {
    return (
      <Button
        variant="secondary"
        fullWidth
        loading={acking}
        disabled={!dataReady}
        onClick={() => onAcknowledge(row)}
      >
        <ShieldCheck size={16} aria-hidden="true" /> Подтвердить
      </Button>
    );
  }
  if (action?.kind === "check_meta") {
    if (!action.href) {
      return (
        <span className="block text-[12px] text-bg-8">
          Ссылка недоступна — откройте журнал действий
        </span>
      );
    }
    return (
      <Button variant="secondary" fullWidth onClick={() => onOpenHref(action.href!)}>
        Проверить в Meta <ArrowRight size={15} aria-hidden="true" />
      </Button>
    );
  }
  // Нет применимого командного действия (kind=source, свёрнутые строки,
  // уже подтверждённые инциденты) — фолбэк на навигацию по исходной ссылке
  // (спека: "Фолбэк — safeOperatorAttentionHref / parseTmaAttentionHref").
  if (row.href) {
    return (
      <Button variant="secondary" fullWidth onClick={() => onOpenHref(row.href!)}>
        Открыть <ArrowRight size={15} aria-hidden="true" />
      </Button>
    );
  }
  return null;
}

/**
 * Пауза объявления из ленты решений — не собственная реализация, а тот же
 * `MiniAdCommand`, что и на /ads (money-путь буква в букву). Строке ленты
 * известен только `target.id` (fb_ad_id), поэтому полная `OperatorAdRow`
 * дозагружается тем же способом, что и `MiniAdDetail` (routes/ads/$fbAdId):
 * `useOperatorAds({ search: fbAdId, ... })` + поиск по `fb_ad_id`.
 */
function DecisionPauseAction({ adId }: { adId: string }) {
  const realtimeStatus = useOperatorRealtimeStatus();
  const query = useOperatorAds({ search: adId, page: 1, page_size: 10 });
  if (query.isPending) {
    return <Skeleton className="h-11 w-full" />;
  }
  if (query.isError || !query.data) {
    return (
      <span className="block text-[12px] text-bg-8">
        Обновите данные перед действием
      </span>
    );
  }
  const projection = adsForRealtimeState(
    query.data,
    realtimeStatus === "connected" && !query.isError,
  );
  const ad = projection.rows.find((candidate) => candidate.fb_ad_id === adId);
  if (!ad) {
    return (
      <span className="block text-[12px] text-bg-8">
        Объявление не найдено в каталоге — обновите данные
      </span>
    );
  }
  return <MiniAdCommand ad={ad} full />;
}
