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

import type { OperatorAdRow, OperatorSeverity } from "@fb/shared/operator/contracts";
import {
  completeOperatorCommandIntent,
  getOrCreateOperatorCommandIntent,
  isOperatorCommandIntentStorageError,
  type OperatorCommandKind,
} from "@fb/shared/operator/commandIntent";
import {
  classifyOperatorDelivery,
  formatOperatorCount,
  operatorActiveActionLabel,
  operatorDeliveryLabel,
} from "@fb/shared/operator/adsViewModel";
import { operatorActionStateReason, operatorCommandTone } from "@fb/shared/operator/actionLabels";
import { formatSpend } from "@fb/shared/format/number";
import { describeStopProximity } from "@fb/shared/operator/stopProximity";
import {
  operatorAdsQuerySort,
  OPERATOR_ADS_STOP_PROXIMITY_SORT,
} from "@fb/shared/operator/routeFilters";
import {
  ACTION_STATE_LABEL,
  adsForRealtimeState,
  severityForDataState,
} from "@fb/shared/operator/viewModel";
import { formatShownOfRussianCount } from "@fb/shared";
import {
  DataStateBadge,
  DataStateNotice,
  deliveryStatusTextClass,
  Metric,
  MetricCell,
  StopProximityReadout,
} from "@fb/operator-ui";
import { useOperatorRealtimeStatus } from "@fb/operator-api";

import { Button } from "@/components/ui/Button";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
import { toast } from "@/components/ui/Toast";
import { OperatorUnavailableState } from "@/components/layout/OperatorPageBoundary";
import {
  fetchOperatorAdForCommand,
  operatorProblemMessage,
  useActivateOperatorAd,
  useOperatorAdsList,
  usePauseOperatorAd,
} from "@/lib/api/operator";

const SEVERITY_LABEL: Record<OperatorSeverity, string> = {
  ok: "Норма",
  warning: "Внимание",
  critical: "Опасность",
  unknown: "Неизвестно",
};

// critical и warning не должны различаться одним лишь цветом:
// восьмиугольник читается как «стоп» и при цветовой слепоте.
const SEVERITY_ICON = {
  ok: ShieldCheck,
  warning: AlertTriangle,
  critical: OctagonAlert,
  unknown: CircleHelp,
} as const;

export function OperatorSeverityBadge({ severity }: { severity: OperatorSeverity }) {
  const Icon = SEVERITY_ICON[severity];
  return (
    <span className="operator-ad-severity" data-severity={severity}>
      <Icon aria-hidden="true" size={14} />
      {SEVERITY_LABEL[severity]}
    </span>
  );
}

export function OperatorAdsTable({
  rows,
  currency,
}: {
  rows: OperatorAdRow[];
  currency: string | null;
}) {
  return (
    <div className="hidden overflow-x-auto md:block">
      <table className="w-full min-w-[1040px] border-collapse text-left text-[14px]">
        <thead className="text-[12px] uppercase tracking-[.06em] text-bg-8">
          <tr className="border-b border-[var(--color-hairline)]">
            <th className="px-3 py-3 font-medium">Объявление</th>
            <th className="px-3 py-3 font-medium">Состояние</th>
            <th className="px-3 py-3 font-medium">До стопа</th>
            <th className="px-3 py-3 text-right font-medium">Расход</th>
            <th className="px-3 py-3 text-right font-medium">Клики</th>
            <th className="px-3 py-3 text-right font-medium">Рег.</th>
            <th className="px-3 py-3 text-right font-medium">FTD</th>
            <th className="px-3 py-3 text-right font-medium">Действие</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((ad) => (
            <tr key={ad.id} className="border-b border-[var(--color-hairline)] last:border-0">
              <td className="max-w-[340px] px-3 py-3">
                <Link
                  to="/ads/$fbAdId"
                  params={{ fbAdId: ad.fb_ad_id }}
                  className="block min-h-11 rounded-sm py-1 font-display text-[15px] text-bg-11 outline-none hover:text-accent focus-visible:ring-2 focus-visible:ring-accent"
                >
                  <span className="block truncate">{ad.name}</span>
                  <span className="mt-1 block truncate font-body text-[12px] text-bg-8">
                    {ad.campaign_name} · {ad.adset_name}
                  </span>
                </Link>
              </td>
              <td className="px-3 py-3">
                <div className="flex flex-col items-start gap-1.5">
                  <OperatorSeverityBadge
                    severity={severityForDataState(ad.severity, ad.data_state)}
                  />
                  <DataStateBadge state={ad.data_state} compact />
                  <span className={`text-[12px] ${deliveryStatusTextClass(ad.delivery_status)}`}>
                    {operatorDeliveryLabel(ad.delivery_status)}
                  </span>
                </div>
              </td>
              <td className="max-w-[240px] px-3 py-3 align-top">
                <StopProximityReadout
                  proximity={describeStopProximity(ad.rule_context, { currency })}
                />
              </td>
              <MetricCell value={formatSpend(ad.metrics.spend, currency)} />
              <MetricCell value={formatOperatorCount(ad.metrics.clicks)} />
              <MetricCell value={formatOperatorCount(ad.metrics.registrations)} />
              <MetricCell value={formatOperatorCount(ad.metrics.ftd)} />
              <td className="px-3 py-3 text-right">
                <AdCommandButtons ad={ad} compact />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function OperatorAdCards({
  rows,
  currency,
}: {
  rows: OperatorAdRow[];
  currency: string | null;
}) {
  return (
    <div className="grid gap-3 md:hidden">
      {rows.map((ad) => (
        <article
          key={ad.id}
          className="rounded-[var(--radius-3)] border border-[var(--color-hairline)] bg-bg-1 p-4"
        >
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <Link
                to="/ads/$fbAdId"
                params={{ fbAdId: ad.fb_ad_id }}
                className="block min-h-11 rounded-sm py-1 font-display text-[16px] text-bg-11 outline-none focus-visible:ring-2 focus-visible:ring-accent"
              >
                <span className="block truncate">{ad.name}</span>
                <span className="mt-1 block truncate font-body text-[12px] text-bg-8">
                  {ad.campaign_name}
                </span>
              </Link>
            </div>
            <OperatorSeverityBadge severity={severityForDataState(ad.severity, ad.data_state)} />
          </div>
          <p className={`mt-2 text-[13px] ${deliveryStatusTextClass(ad.delivery_status)}`}>
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
            <Metric label="Расход" value={formatSpend(ad.metrics.spend, currency)} />
            <Metric label="Клики" value={formatOperatorCount(ad.metrics.clicks)} />
            <Metric label="Рег." value={formatOperatorCount(ad.metrics.registrations)} />
            <Metric label="FTD" value={formatOperatorCount(ad.metrics.ftd)} />
          </dl>
          <div className="mt-4 flex min-w-0 flex-col items-start gap-2 border-t border-[var(--color-hairline)] pt-3">
            <DataStateBadge state={ad.data_state} compact />
            <AdCommandButtons ad={ad} compact fullWidth />
          </div>
        </article>
      ))}
    </div>
  );
}

export function AdCommandButtons({
  ad,
  compact = false,
  fullWidth = false,
}: {
  ad: OperatorAdRow;
  compact?: boolean;
  fullWidth?: boolean;
}) {
  const navigate = useNavigate();
  const pause = usePauseOperatorAd();
  const activate = useActivateOperatorAd();
  const realtimeStatus = useOperatorRealtimeStatus();
  const realtimeStatusRef = useRef(realtimeStatus);
  const commandButtonRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    realtimeStatusRef.current = realtimeStatus;
  }, [realtimeStatus]);
  const queryClient = useQueryClient();
  const [confirmOpen, setConfirmOpen] = useState(false);
  const delivery = classifyOperatorDelivery(ad.delivery_status);

  if (ad.active_action) {
    return (
      <Link
        to="/actions/$actionId"
        params={{ actionId: ad.active_action.id }}
        className={`inline-flex min-h-11 items-center rounded-[var(--radius-2)] border border-warning/30 bg-warning-bg px-3 text-[13px] font-semibold text-warning outline-none focus-visible:ring-2 focus-visible:ring-accent ${
          fullWidth ? "w-full min-w-0 justify-center whitespace-normal text-center" : ""
        }`}
      >
        {ad.active_action.public_id} · {operatorActiveActionLabel(ad.active_action.state)}
      </Link>
    );
  }

  if (realtimeStatus !== "connected") {
    return (
      <span
        role="status"
        className={`text-[12px] text-warning ${
          fullWidth ? "block w-full min-w-0 whitespace-normal break-words text-left" : ""
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
          fullWidth ? "block w-full min-w-0 whitespace-normal break-words text-left" : ""
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
          fullWidth ? "block w-full min-w-0 whitespace-normal break-words text-left" : ""
        }`}
      >
        Исправьте объявление или запросите повторную проверку в Ads Manager
      </span>
    );
  }

  if (delivery === "pending" || delivery === "parent_paused" || delivery === "terminal") {
    return (
      <span
        role="status"
        className={`text-[12px] text-warning ${
          fullWidth ? "block w-full min-w-0 whitespace-normal break-words text-left" : ""
        }`}
      >
        {operatorDeliveryLabel(ad.delivery_status)} · действие доступно в Ads Manager
      </span>
    );
  }

  if (delivery === "unknown") {
    return (
      <span
        className={`text-[12px] text-bg-8 ${
          fullWidth ? "block w-full min-w-0 whitespace-normal break-words text-left" : ""
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

  async function runCommand() {
    try {
      if (realtimeStatusRef.current !== "connected") {
        throw new Error("Live-связь изменилась во время подтверждения");
      }
      const current = await fetchOperatorAdForCommand(queryClient, ad.fb_ad_id);
      if (
        realtimeStatusRef.current !== "connected" ||
        current.as_of !== ad.as_of ||
        current.delivery_status !== ad.delivery_status ||
        classifyOperatorDelivery(current.delivery_status) !== delivery
      ) {
        throw new Error(
          "Состояние объявления изменилось. Проверьте карточку и повторите действие.",
        );
      }
      const idempotencyKey = getOrCreateOperatorCommandIntent(actionKind, ad.fb_ad_id);
      const receipt = await mutation.mutateAsync({
        params: {
          path: { ad_id: ad.fb_ad_id },
          header: {
            "Idempotency-Key": idempotencyKey,
            "X-Operator-Principal": "operator:web",
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
      // 202 — это queued, а не выполнено: зелёный тон только для confirmed.
      const tone = operatorCommandTone(receipt.state);
      toast[tone](
        `${receipt.public_id}: ${ACTION_STATE_LABEL[receipt.state]}`,
        receipt.created
          ? operatorActionStateReason(receipt.state)
          : `Задача уже существует — не повторяйте команду. ${operatorActionStateReason(receipt.state)}`,
      );
      if (intentCleanupWarning) {
        toast.error(
          `${receipt.public_id}: ключ защиты не очищен`,
          `Задача уже создана — не повторяйте команду. ${intentCleanupWarning}`,
        );
      }
      await navigate({ to: "/actions/$actionId", params: { actionId: String(receipt.task_id) } });
    } catch (error) {
      toast.error(`${label} не удалось`, operatorCommandProblemMessage(error));
      throw error;
    }
  }

  return (
    <>
      <Button
        ref={commandButtonRef}
        type="button"
        // «Включить» возобновляет реальный спенд — это не нейтральная утилита
        // рядом с «Обновить», поэтому предупреждающий вид, а не secondary.
        variant={isPause ? "danger" : "warning"}
        size={compact ? "md" : "lg"}
        className={fullWidth ? "min-h-11 w-full" : "min-h-11"}
        loading={mutation.isPending}
        leftIcon={<Icon aria-hidden="true" />}
        onClick={() => setConfirmOpen(true)}
      >
        {label}
      </Button>
      <ConfirmDialog
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
        title={`${label} объявление?`}
        description={`«${ad.name}». Команда будет поставлена в очередь, а результат подтверждён отдельной задачей.`}
        confirmLabel={label}
        confirmVariant={isPause ? "danger" : "warning"}
        onConfirm={runCommand}
        returnFocusRef={commandButtonRef}
      />
    </>
  );
}

function operatorCommandProblemMessage(error: unknown): string {
  return isOperatorCommandIntentStorageError(error)
    ? `Безопасное действие заблокировано. ${error.userMessage}`
    : operatorProblemMessage(error);
}

const CABINET_ADS_PAGE_SIZE = 20;

/**
 * Список объявлений одного кабинета для экрана /cabinets/$cabinetId
 * (issue #344). Переиспользует тот же каталог, что и /ads — тот же серверный
 * фильтр `account_id`, те же `OperatorAdsTable`/`OperatorAdCards` и те же
 * команды pause/activate через `AdCommandButtons`, только с предустановленным
 * кабинетом вместо собственного списка и собственной логики команд.
 */
export function OperatorCabinetAdsSection({
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
  const hasConfirmedCount = displayState === "ready" || displayState === "empty";

  return (
    <section
      className="ledger-section ledger-section--cabinet-ads"
      aria-labelledby="cabinet-ads-title"
    >
      <div className="ledger-section__header">
        <div className="ledger-section__title">
          <h2 id="cabinet-ads-title">Объявления кабинета</h2>
          <span>
            {displayPayload && hasConfirmedCount
              ? formatShownOfRussianCount(
                  displayRows?.length ?? 0,
                  displayPayload.total,
                  "строка",
                  "строки",
                  "строк",
                )
              : displayPayload
                ? "— строк"
                : "Загрузка…"}
          </span>
        </div>
        <Link
          className="ledger-attention-item__action"
          to="/ads"
          search={{ account_id: cabinetId }}
        >
          Все фильтры
          <ArrowRight size={14} aria-hidden="true" />
        </Link>
      </div>
      {displayPayload && displayState && displayState !== "ready" ? (
        <div className="p-3">
          <DataStateNotice state={displayState} issues={displayPayload.issues} compact />
        </div>
      ) : null}
      <div className="p-3 sm:p-4">
        {query.isError && !query.data ? (
          <OperatorUnavailableState
            title="Объявления кабинета недоступны"
            resource="список объявлений кабинета"
            details={operatorProblemMessage(query.error)}
            onRetry={() => void query.refetch()}
          />
        ) : query.isPending && !query.data ? (
          <div role="status" aria-label="Загрузка объявлений кабинета" className="grid gap-3">
            {Array.from({ length: 4 }, (_, index) => (
              <Skeleton key={index} className="h-20 w-full" />
            ))}
          </div>
        ) : displayRows?.length ? (
          <>
            <OperatorAdsTable rows={displayRows} currency={currency} />
            <OperatorAdCards rows={displayRows} currency={currency} />
          </>
        ) : displayState === "empty" ? (
          <EmptyState
            title="В кабинете нет объявлений"
            description="Сервер подтвердил, что в этом кабинете пока нет объявлений."
          />
        ) : (
          <EmptyState
            title="Список не подтверждён"
            description="Дождитесь сверки live-снимка или обновите данные. Неподтверждённый результат не считается нулём."
          />
        )}
      </div>
      {query.hasNextPage ? (
        <div className="border-t border-[var(--color-hairline)] p-3">
          <Button
            variant="secondary"
            className="min-h-11 w-full"
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
