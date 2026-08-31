import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "@tanstack/react-router";
import { useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowRight,
  CircleHelp,
  CirclePause,
  CirclePlay,
  OctagonAlert,
  ShieldCheck,
} from "lucide-react";

import {
  classifyOperatorDelivery,
  formatOperatorCount,
  operatorActiveActionLabel,
  operatorDeliveryLabel,
} from "@fb/shared/operator/adsViewModel";
import {
  OPERATOR_COMMAND_QUEUED_NOTICE,
  operatorCommandTone,
} from "@fb/shared/operator/actionLabels";
import { formatSpend } from "@fb/shared/format/number";
import { describeStopProximity } from "@fb/shared/operator/stopProximity";
import {
  operatorAdsQuerySort,
  OPERATOR_ADS_STOP_PROXIMITY_SORT,
} from "@fb/shared/operator/routeFilters";
import { adsForRealtimeState, severityForDataState } from "@fb/shared/operator/viewModel";
import type {
  OperatorAdRow,
  OperatorSeverity,
} from "@fb/shared/operator/contracts";
import {
  completeOperatorCommandIntent,
  getOrCreateOperatorCommandIntent,
  isOperatorCommandIntentStorageError,
  type OperatorCommandKind,
} from "@fb/shared/operator/commandIntent";
import {
  DataStateBadge,
  DataStateNotice,
  deliveryStatusTextClass,
  Metric,
  StopProximityReadout,
} from "@fb/operator-ui";
import { useOperatorRealtimeStatus } from "@fb/operator-api";

import { Button, EmptyState, ErrorState, Skeleton } from "@/components/ui";
import {
  fetchOperatorAdForCommand,
  operatorProblemMessage,
  useActivateOperatorAd,
  useOperatorAdsList,
  usePauseOperatorAd,
} from "@/lib/operatorApi";
import { haptic, tgAlert, tgConfirm } from "@/lib/tg";

const LABEL: Record<OperatorSeverity, string> = {
  ok: "Норма",
  warning: "Внимание",
  critical: "Опасность",
  unknown: "Неизвестно",
};

// critical и warning не должны различаться одним лишь цветом:
// восьмиугольник читается как «стоп» и при цветовой слепоте.
const ICON = {
  ok: ShieldCheck,
  warning: AlertTriangle,
  critical: OctagonAlert,
  unknown: CircleHelp,
} as const;

export function MiniSeverityBadge({
  severity,
}: {
  severity: OperatorSeverity;
}) {
  const Icon = ICON[severity];
  const color =
    severity === "critical"
      ? "var(--color-danger)"
      : severity === "warning"
        ? "var(--color-warning)"
        : severity === "ok"
          ? "var(--color-success)"
          : "var(--color-bg-8)";
  return (
    <span
      className="inline-flex min-h-6 items-center gap-1 rounded-full border border-[var(--color-hairline-strong)] px-2 text-[12px] font-semibold"
      style={{ color }}
    >
      <Icon aria-hidden="true" size={14} /> {LABEL[severity]}
    </span>
  );
}

export function MiniOperatorAdCard({
  ad,
  currency,
}: {
  ad: OperatorAdRow;
  currency: string | null;
}) {
  return (
    <article className="rounded-[var(--radius-3)] border border-[var(--color-hairline)] bg-bg-1 p-4">
      <div className="flex items-start justify-between gap-3">
        <Link
          to="/ads/$fbAdId"
          params={{ fbAdId: ad.fb_ad_id }}
          className="min-h-11 min-w-0 flex-1 rounded-sm py-1 outline-none focus-visible:ring-2 focus-visible:ring-accent"
        >
          <span className="block truncate font-display text-[16px] text-bg-11">
            {ad.name}
          </span>
          <span className="mt-1 block truncate text-[12px] text-bg-8">
            {ad.campaign_name}
          </span>
        </Link>
        <MiniSeverityBadge
          severity={severityForDataState(ad.severity, ad.data_state)}
        />
      </div>
      <p
        className={`mt-2 text-[13px] ${deliveryStatusTextClass(ad.delivery_status)}`}
      >
        {operatorDeliveryLabel(ad.delivery_status)}
      </p>
      <div className="mt-4 border-t border-[var(--color-hairline)] pt-3">
        <span className="text-[12px] text-bg-8">До стопа</span>
        <div className="mt-1.5">
          <StopProximityReadout
            proximity={describeStopProximity(ad.rule_context, { currency })}
          />
        </div>
      </div>
      <dl className="mt-4 grid grid-cols-4 gap-2 text-right">
        <Metric
          label="Расход"
          value={formatSpend(ad.metrics.spend, currency)}
        />
        <Metric label="Клики" value={formatOperatorCount(ad.metrics.clicks)} />
        <Metric
          label="Рег."
          value={formatOperatorCount(ad.metrics.registrations)}
        />
        <Metric label="FTD" value={formatOperatorCount(ad.metrics.ftd)} />
      </dl>
      <div className="mt-4 flex min-w-0 flex-col items-start gap-2 border-t border-[var(--color-hairline)] pt-3">
        <DataStateBadge state={ad.data_state} compact />
        <MiniAdCommand ad={ad} full />
      </div>
    </article>
  );
}

export function MiniAdCommand({
  ad,
  full = false,
}: {
  ad: OperatorAdRow;
  full?: boolean;
}) {
  const navigate = useNavigate();
  const pause = usePauseOperatorAd();
  const activate = useActivateOperatorAd();
  const realtimeStatus = useOperatorRealtimeStatus();
  const realtimeStatusRef = useRef(realtimeStatus);
  useEffect(() => {
    realtimeStatusRef.current = realtimeStatus;
  }, [realtimeStatus]);
  const queryClient = useQueryClient();
  const [checkingFreshness, setCheckingFreshness] = useState(false);
  const delivery = classifyOperatorDelivery(ad.delivery_status);

  if (ad.active_action) {
    return (
      <Link
        to="/actions/$actionId"
        params={{ actionId: ad.active_action.id }}
        className={`inline-flex min-h-11 items-center rounded-[var(--radius-2)] border border-warning/30 bg-warning-bg px-3 text-[13px] font-semibold text-warning outline-none focus-visible:ring-2 focus-visible:ring-accent ${
          full
            ? "w-full min-w-0 justify-center whitespace-normal text-center"
            : ""
        }`}
      >
        {ad.active_action.public_id} ·{" "}
        {operatorActiveActionLabel(ad.active_action.state)}
      </Link>
    );
  }
  if (realtimeStatus !== "connected") {
    return (
      <span
        role="status"
        className={`text-[12px] text-warning ${
          full
            ? "block w-full min-w-0 whitespace-normal break-words text-left"
            : ""
        }`}
      >
        Действие недоступно до сверки live-снимка
      </span>
    );
  }

  if (ad.data_state !== "ready") {
    return (
      <span
        className={`text-[12px] text-bg-8 ${
          full
            ? "block w-full min-w-0 whitespace-normal break-words text-left"
            : "text-right"
        }`}
      >
        Обновите данные перед действием
      </span>
    );
  }
  if (delivery === "rejected") {
    return (
      <span
        role="status"
        className={`text-[12px] font-semibold text-danger ${
          full
            ? "block w-full min-w-0 whitespace-normal break-words text-left"
            : "text-right"
        }`}
      >
        Исправьте объявление или запросите повторную проверку в Ads Manager
      </span>
    );
  }
  if (
    delivery === "pending" ||
    delivery === "parent_paused" ||
    delivery === "terminal"
  ) {
    return (
      <span
        role="status"
        className={`text-[12px] text-warning ${
          full
            ? "block w-full min-w-0 whitespace-normal break-words text-left"
            : "text-right"
        }`}
      >
        {operatorDeliveryLabel(ad.delivery_status)} · действие доступно в Ads
        Manager
      </span>
    );
  }
  if (delivery === "unknown") {
    return (
      <span
        className={`text-[12px] text-bg-8 ${
          full
            ? "block w-full min-w-0 whitespace-normal break-words text-left"
            : "text-right"
        }`}
      >
        Статус доставки неизвестен
      </span>
    );
  }

  const isPause = delivery === "active";
  const mutation = isPause ? pause : activate;
  const label = isPause ? "Отключить" : "Включить";
  const Icon = isPause ? CirclePause : CirclePlay;
  const actionKind: OperatorCommandKind = isPause ? "pause_ad" : "activate_ad";

  // Сверка «объявление не разошлось с тем, что видел оператор» происходит
  // ДО tgConfirm — оператор не подтверждает команду впустую, чтобы затем
  // получить отказ. Команда всё равно уходит с expected_delivery_status/
  // expected_as_of из этого же свежего чтения, и сервер
  // (core/commands/service.py) держит собственную, независимую проверку
  // precondition — это последнее слово, а не клиентская сверка.
  async function run() {
    if (realtimeStatusRef.current !== "connected") {
      await tgAlert("Действие недоступно до сверки live-снимка.");
      return;
    }
    let current: OperatorAdRow & { as_of: string; delivery_status: string };
    setCheckingFreshness(true);
    try {
      current = await fetchOperatorAdForCommand(queryClient, ad.fb_ad_id);
    } catch (error) {
      await tgAlert(operatorCommandProblemMessage(error));
      return;
    } finally {
      setCheckingFreshness(false);
    }
    if (
      realtimeStatusRef.current !== "connected" ||
      current.as_of !== ad.as_of ||
      current.delivery_status !== ad.delivery_status ||
      classifyOperatorDelivery(current.delivery_status) !== delivery
    ) {
      await tgAlert("Обновите данные перед действием.");
      return;
    }
    haptic.impact(isPause ? "heavy" : "medium");
    const accepted = await tgConfirm(
      `${label} объявление «${ad.name}»? ${OPERATOR_COMMAND_QUEUED_NOTICE}`,
    );
    if (!accepted) return;
    try {
      const idempotencyKey = getOrCreateOperatorCommandIntent(
        actionKind,
        ad.fb_ad_id,
      );
      const receipt = await mutation.mutateAsync({
        params: {
          path: { ad_id: ad.fb_ad_id },
          header: {
            "Idempotency-Key": idempotencyKey,
            "X-Operator-Principal": "operator:tma",
          },
        },
        body: {
          expected_delivery_status: current.delivery_status,
          expected_as_of: current.as_of,
        },
      });
      let intentCleanupWarning: string | null = null;
      try {
        completeOperatorCommandIntent(actionKind, ad.fb_ad_id, idempotencyKey);
      } catch (error) {
        if (!isOperatorCommandIntentStorageError(error)) throw error;
        intentCleanupWarning = error.userMessage;
      }
      // 202 — это queued: «успех» подтверждаем только для confirmed.
      const tone = operatorCommandTone(receipt.state);
      haptic.notify(tone === "success" ? "success" : "warning");
      if (intentCleanupWarning) {
        haptic.notify("warning");
        await tgAlert(
          `${receipt.public_id}: задача уже создана — не повторяйте команду. ${intentCleanupWarning}`,
        );
      }
      await navigate({
        to: "/actions/$actionId",
        params: { actionId: String(receipt.task_id) },
      });
    } catch (error) {
      haptic.notify("error");
      await tgAlert(operatorCommandProblemMessage(error));
    }
  }

  return (
    <Button
      type="button"
      // «Включить» возобновляет реальный спенд — предупреждающий вид, не secondary.
      variant={isPause ? "danger" : "warning"}
      fullWidth={full}
      loading={mutation.isPending || checkingFreshness}
      className="min-h-11"
      onClick={() => void run()}
    >
      <Icon aria-hidden="true" size={16} /> {label}
    </Button>
  );
}

function operatorCommandProblemMessage(error: unknown): string {
  return isOperatorCommandIntentStorageError(error)
    ? `Безопасное действие заблокировано. ${error.userMessage}`
    : operatorProblemMessage(error);
}

const CABINET_ADS_PAGE_SIZE = 20;

/**
 * Список объявлений одного кабинета на экране /cabinets/$cabinetId
 * (issue #344). Тот же каталог, что и /ads, с тем же серверным фильтром
 * `account_id` и той же карточкой команды (`MiniAdCommand` с tgConfirm) —
 * никакого отдельного списка или отдельной логики команд здесь нет.
 */
export function MiniCabinetAdsSection({
  cabinetId,
  currency,
}: {
  cabinetId: string;
  currency: string | null;
}) {
  const realtimeStatus = useOperatorRealtimeStatus();
  const query = useOperatorAdsList({
    account_id: cabinetId,
    sort: operatorAdsQuerySort(OPERATOR_ADS_STOP_PROXIMITY_SORT),
    direction: "desc",
    page_size: CABINET_ADS_PAGE_SIZE,
  });
  const projections = query.data?.pages.map((page) =>
    adsForRealtimeState(page, realtimeStatus === "connected" && !query.isError),
  );
  const displayPayload = projections?.at(-1) ?? null;
  const displayState = displayPayload?.state;
  const displayRows = projections?.flatMap((page) => page.rows);

  return (
    <section
      className="mini-ledger-section mini-ledger-section--cabinet-ads"
      aria-labelledby="mini-cabinet-ads-title"
    >
      <header>
        <div>
          <h2 id="mini-cabinet-ads-title">Объявления кабинета</h2>
          {displayState ? <DataStateBadge state={displayState} compact /> : null}
        </div>
        <Link
          to="/ads"
          search={{ account_id: cabinetId }}
          className="mini-ledger__inline-action"
        >
          Все фильтры
          <ArrowRight size={14} aria-hidden="true" />
        </Link>
      </header>
      {displayPayload && displayState && displayState !== "ready" ? (
        <div className="px-4">
          <DataStateNotice state={displayState} issues={displayPayload.issues} compact />
        </div>
      ) : null}
      <div className="mini-cabinet-ads-list grid gap-3 px-4 py-3">
        {query.isError && !query.data ? (
          <ErrorState
            message={operatorProblemMessage(query.error)}
            onRetry={() => void query.refetch()}
          />
        ) : query.isPending && !query.data ? (
          <div
            role="status"
            aria-label="Загрузка объявлений кабинета"
            className="grid gap-3"
          >
            {Array.from({ length: 3 }, (_, index) => (
              <Skeleton key={index} className="h-40 w-full" />
            ))}
          </div>
        ) : displayRows?.length ? (
          displayRows.map((ad) => (
            <MiniOperatorAdCard key={ad.id} ad={ad} currency={currency} />
          ))
        ) : displayState === "empty" ? (
          <EmptyState
            title="В кабинете нет объявлений"
            description="Сервер подтвердил, что в этом кабинете пока нет объявлений."
          />
        ) : (
          <EmptyState
            title="Список не подтверждён"
            description="Дождитесь сверки live-снимка. Неподтверждённый результат не считается нулём."
          />
        )}
      </div>
      {query.hasNextPage ? (
        <div className="px-4 pb-3">
          <Button
            variant="secondary"
            fullWidth
            loading={query.isFetchingNextPage}
            onClick={() => void query.fetchNextPage()}
          >
            Показать ещё
          </Button>
        </div>
      ) : null}
    </section>
  );
}
