/**
 * Лента «Решения» (issue #338) — одна очередь того, что требует клика
 * оператора сейчас, вместо трёх разрозненных потоков (сигналы внимания,
 * инциденты, неподтверждённые команды). Отбор, сортировка и свёртка строк
 * уже реализованы в `packages/shared/src/operator/decisionFeed.ts`
 * (`selectDecisionRows`/`compareDecisionRows`/`collapseDecisionRows`) — этот
 * модуль только рендерит результат и подключает три применимых действия.
 *
 * Инвариант (спека issue #338, п.0): строка ленты — повод открыть решение,
 * а не доказательство состояния объекта. Команда «Отключить» не берёт
 * предусловие из строки — `expected_delivery_status`/`expected_as_of`
 * приходят из свежего чтения `fetchOperatorAdForCommand` непосредственно
 * перед диалогом подтверждения, тем же путём, что и `/ads`
 * (`features/operator/OperatorAds.tsx`).
 */
import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "@tanstack/react-router";
import { useQueryClient } from "@tanstack/react-query";
import { CircleCheck, ExternalLink } from "lucide-react";

import type { OperatorAdRow, OperatorSnapshot } from "@fb/shared/operator/contracts";
import {
  collapseDecisionRows,
  combineDecisionFeedState,
  compareDecisionRows,
  decisionRowAge,
  selectDecisionRows,
  type DecisionRow,
} from "@fb/shared/operator/decisionFeed";
import { safeOperatorAttentionHref } from "@fb/shared/operator/attentionNavigation";
import { confirmedOperatorCurrency } from "@fb/shared/operator/adsViewModel";
import {
  completeOperatorCommandIntent,
  getOrCreateOperatorCommandIntent,
  isOperatorCommandIntentStorageError,
} from "@fb/shared/operator/commandIntent";
import {
  OPERATOR_COMMAND_QUEUED_NOTICE,
  operatorActionStateReason,
  operatorCommandTone,
} from "@fb/shared/operator/actionLabels";
import { ACTION_STATE_LABEL } from "@fb/shared/operator/viewModel";
import { DataStateBadge, DataStateNotice } from "@fb/operator-ui";
import { useOperatorRealtimeStatus } from "@fb/operator-api";

import { Button } from "@/components/ui/Button";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { EmptyState } from "@/components/ui/EmptyState";
import { toast } from "@/components/ui/Toast";
import { OperatorSeverityBadge } from "@/features/operator/OperatorAds";
import {
  fetchOperatorAdForCommand,
  operatorProblemMessage,
  useAcknowledgeOperatorIncident,
  usePauseOperatorAd,
} from "@/lib/api/operator";

/**
 * Денежная копия строки гасится по признаку САМОЙ строки
 * (`requires_usd_evidence`), а не по глобальному флагу валюты — тот же
 * канон, что уже действует в журнале инцидентов (`operatorIncidentCopy`,
 * `packages/shared/src/operator/incidentViewModel.ts`). Дашборд по ошибке
 * гасит копию по глобальному состоянию валюты для всех строк подряд (issue
 * #338, найденный по ходу дефект) — здесь этот дефект не повторяем.
 */
function decisionRowCopy(
  row: DecisionRow,
  usdScopeConfirmed: boolean,
): { title: string; contextLine: string | null } {
  if (row.requiresUsdEvidence && !usdScopeConfirmed) {
    return {
      title: "Денежный сигнал требует проверки",
      contextLine: "Валюта кабинета не подтверждена. Денежные детали скрыты.",
    };
  }
  return {
    title: row.title.trim() || "Решение требует проверки",
    contextLine: row.contextLine.trim() || null,
  };
}

export function DecisionsFeed({
  snapshot,
  realtimeConnected,
  now = new Date(),
}: {
  snapshot: OperatorSnapshot;
  realtimeConnected: boolean;
  /** Инъекция «сейчас» — без неё возраст строки недетерминирован и не
   * тестируется фиксированным снимком (тот же приём, что и в `decisionRowAge`). */
  now?: Date;
}) {
  const usdScopeConfirmed = confirmedOperatorCurrency(snapshot.meta) === "USD";
  // Сегодня источник строк один (`attention`) — обёртка в
  // `combineDecisionFeedState` держит контракт форвард-совместимым: появится
  // второй источник (см. открытые вопросы issue #338), правило комбинации
  // DataState не переписывается заново по месту.
  const feedState = combineDecisionFeedState([snapshot.attention.state]);
  // partial/stale/unavailable никогда не выглядят зелёными и не пускают
  // money-действия — сверка идёт по этому единственному флагу, а не по
  // состоянию отдельной строки.
  const actionsEnabled = realtimeConnected && feedState === "ready";

  const rows = snapshot.attention.data
    ? selectDecisionRows(snapshot).slice().sort(compareDecisionRows)
    : [];
  const collapsed = collapseDecisionRows(rows, usdScopeConfirmed);
  const total = snapshot.attention.data?.total ?? null;
  const visibleCount = snapshot.attention.data?.items.length ?? 0;
  const truncated = Boolean(snapshot.attention.data?.truncated);

  return (
    <div className="mx-auto max-w-6xl">
      <header className="mb-5 flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="m-0 font-display text-[clamp(30px,4vw,44px)] font-medium text-bg-11">
            Решения
          </h1>
          <p className="mt-2 max-w-2xl text-[16px] leading-6 text-bg-9">
            Очередь того, что требует вашего клика сейчас. Хронология — в «Инцидентах» и
            «Действиях».
          </p>
        </div>
        <div className="flex items-center gap-3">
          <DataStateBadge state={feedState} />
        </div>
      </header>

      {feedState !== "ready" && feedState !== "empty" ? (
        <div className="mb-4">
          <DataStateNotice state={feedState} issues={snapshot.attention.issues} />
        </div>
      ) : null}

      {truncated ? (
        // Байер сделал это условием одобрения порядка: срез лимитом обязан
        // быть заметной строкой, а не сноской (issue #338, вердикт п.1).
        <div
          role="status"
          className="mb-4 border-y border-warning/35 bg-warning-bg px-4 py-3 text-[14px] font-medium text-warning"
        >
          Показано {visibleCount} из {total ?? "не подтверждено"} — полный список в{" "}
          <Link to="/incidents" className="underline">
            журнале инцидентов
          </Link>{" "}
          и{" "}
          <Link to="/actions" className="underline">
            логе действий
          </Link>
          .
        </div>
      ) : null}

      {collapsed.length > 0 ? (
        <ol className="m-0 divide-y divide-[var(--color-hairline)] border-y border-[var(--color-hairline)] p-0">
          {collapsed.map(({ row, count }) => (
            <DecisionRowItem
              key={row.id}
              row={row}
              count={count}
              copy={decisionRowCopy(row, usdScopeConfirmed)}
              actionsEnabled={actionsEnabled}
              now={now}
            />
          ))}
        </ol>
      ) : feedState === "empty" || feedState === "ready" ? (
        // «Решений нет» — хорошая новость, а не ошибка: подтверждённый ноль,
        // а не пропуск данных.
        <EmptyState
          icon={<CircleCheck aria-hidden="true" size={32} />}
          title="Решений нет"
          description="Сервер подтвердил: сейчас нет ничего, что требует вашего клика."
        />
      ) : (
        <EmptyState
          title="Лента не подтверждена"
          description="Обновите данные. Неподтверждённый список не считается пустым."
        />
      )}
    </div>
  );
}

function DecisionRowItem({
  row,
  count,
  copy,
  actionsEnabled,
  now,
}: {
  row: DecisionRow;
  count: number;
  copy: { title: string; contextLine: string | null };
  actionsEnabled: boolean;
  now: Date;
}) {
  const age = decisionRowAge(row, now);
  return (
    <li className="grid gap-4 bg-bg-0 px-4 py-4 sm:grid-cols-[minmax(0,1fr)_auto] sm:px-5">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <OperatorSeverityBadge severity={row.severity} />
          <span className="text-[14px] text-bg-9">
            {row.target.label?.trim() || "Объект не указан"}
          </span>
          {/* Возраст — условие одобрения порядка «старейшее первым» (вердикт
              byer, issue #338): без него порядок читается как случайный. */}
          <span className="font-numeric text-[12px] text-bg-8">Висит {age}</span>
        </div>
        <h2 className="m-0 mt-3 text-[18px] font-semibold leading-6 text-bg-11">
          {copy.title}
          {count > 1 ? (
            <span className="ml-2 font-numeric text-[13px] text-bg-8">×{count}</span>
          ) : null}
        </h2>
        {copy.contextLine ? (
          <p className="mt-2 max-w-3xl text-[16px] leading-6 text-bg-10">{copy.contextLine}</p>
        ) : null}
      </div>
      <div className="flex items-end gap-2 sm:flex-col sm:items-stretch sm:justify-end">
        <DecisionRowAction row={row} actionsEnabled={actionsEnabled} />
      </div>
    </li>
  );
}

function DecisionRowAction({
  row,
  actionsEnabled,
}: {
  row: DecisionRow;
  actionsEnabled: boolean;
}) {
  const action = row.primaryAction;
  if (!action) return null;

  if (!actionsEnabled) {
    return (
      <span role="status" className="text-[12px] text-warning">
        Действие недоступно до сверки live-снимка
      </span>
    );
  }

  if (action.kind === "pause") {
    return (
      <DecisionAdPauseButton
        adId={action.adId}
        fallbackLabel={row.target.label?.trim() || row.title}
      />
    );
  }

  if (action.kind === "acknowledge") {
    return <DecisionAcknowledgeButton incidentId={row.id} />;
  }

  // check_meta — навигация, а не выполненное действие (спека, п.3): ссылка
  // ведёт к сверке в Meta, но не оформляется как команда.
  const safeHref = action.href ? safeOperatorAttentionHref(action.href) : null;
  return (
    <a
      href={safeHref ?? "/actions"}
      className="inline-flex min-h-11 flex-1 items-center justify-center gap-2 rounded-[var(--radius-2)] border border-[var(--color-hairline-strong)] px-4 text-[14px] font-semibold text-bg-11 no-underline hover:bg-bg-2 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent sm:flex-none"
    >
      Проверить в Meta
      <ExternalLink size={15} aria-hidden="true" />
    </a>
  );
}

/**
 * «Отключить» из ленты — та же money-команда, что и на `/ads`
 * (`AdCommandButtons` в `features/operator/OperatorAds.tsx`): сверка live-
 * снимка → свежее чтение `fetchOperatorAdForCommand` → диалог подтверждения
 * → `POST /ads/{id}/pause` с `Idempotency-Key` и `expected_*` из этого же
 * чтения → тост по `receipt.state` (202 = queued, зелёный только confirmed)
 * → переход на `/actions/$actionId`. Строка ленты не несёт своего
 * `delivery_status`/`as_of` (только `target.id`), поэтому вся сверка
 * состояния идёт через сеть, а не через сравнение с уже отрисованной
 * строкой.
 */
function DecisionAdPauseButton({
  adId,
  fallbackLabel,
}: {
  adId: string;
  fallbackLabel: string;
}) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const pause = usePauseOperatorAd();
  const realtimeStatus = useOperatorRealtimeStatus();
  const realtimeStatusRef = useRef(realtimeStatus);
  useEffect(() => {
    realtimeStatusRef.current = realtimeStatus;
  }, [realtimeStatus]);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const [confirmTarget, setConfirmTarget] = useState<
    (OperatorAdRow & { as_of: string; delivery_status: string }) | null
  >(null);
  const [checkingFreshness, setCheckingFreshness] = useState(false);

  async function requestConfirm() {
    if (realtimeStatusRef.current !== "connected") {
      toast.error("Отключить недоступно", "Действие недоступно до сверки live-снимка.");
      return;
    }
    setCheckingFreshness(true);
    try {
      const current = await fetchOperatorAdForCommand(queryClient, adId);
      if (realtimeStatusRef.current !== "connected") {
        toast.error("Отключить недоступно", "Обновите данные перед действием.");
        return;
      }
      setConfirmTarget(current);
    } catch (error) {
      toast.error("Отключить недоступно", operatorProblemMessage(error));
    } finally {
      setCheckingFreshness(false);
    }
  }

  async function runCommand(current: OperatorAdRow & { as_of: string; delivery_status: string }) {
    try {
      const idempotencyKey = getOrCreateOperatorCommandIntent("pause_ad", adId);
      const receipt = await pause.mutateAsync({
        params: {
          path: { ad_id: adId },
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
        completeOperatorCommandIntent("pause_ad", adId, idempotencyKey);
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
      toast.error("Отключить не удалось", operatorProblemMessage(error));
      throw error;
    }
  }

  return (
    <>
      <Button
        ref={buttonRef}
        type="button"
        variant="danger"
        size="sm"
        className="min-h-11"
        loading={pause.isPending || checkingFreshness}
        onClick={() => void requestConfirm()}
      >
        Отключить
      </Button>
      <ConfirmDialog
        open={confirmTarget !== null}
        onOpenChange={(open) => {
          if (!open) setConfirmTarget(null);
        }}
        title="Отключить объявление?"
        description={`«${confirmTarget?.name ?? fallbackLabel}». Команда будет поставлена в очередь. ${OPERATOR_COMMAND_QUEUED_NOTICE}`}
        confirmLabel="Отключить"
        confirmVariant="danger"
        onConfirm={() => (confirmTarget ? runCommand(confirmTarget) : Promise.resolve())}
        returnFocusRef={buttonRef}
      />
    </>
  );
}

/**
 * «Подтвердить» — `useAcknowledgeOperatorIncident` без confirm-диалога
 * (спека, п.3): ack гасит сигнал, а не решает денежную проблему. `pendingRef`
 * — синхронный барьер от двойного клика: `mutation.isPending` обновляется
 * реактивно и не успевает истинностью прикрыть второй клик в том же тике.
 */
function DecisionAcknowledgeButton({ incidentId }: { incidentId: string }) {
  const acknowledge = useAcknowledgeOperatorIncident();
  const pendingRef = useRef(false);
  const [error, setError] = useState<string | null>(null);

  async function handleClick() {
    if (pendingRef.current) return;
    pendingRef.current = true;
    setError(null);
    try {
      await acknowledge.mutateAsync({
        params: {
          path: { incident_id: incidentId },
          header: { "X-Operator-Principal": "operator:web" },
        },
      });
    } catch (err) {
      setError(operatorProblemMessage(err));
    } finally {
      pendingRef.current = false;
    }
  }

  return (
    <div className="flex flex-col items-stretch gap-1">
      <Button
        type="button"
        variant="secondary"
        className="min-h-11"
        loading={acknowledge.isPending}
        leftIcon={<CircleCheck aria-hidden="true" />}
        onClick={() => void handleClick()}
      >
        Подтвердить
      </Button>
      {error ? (
        <span role="alert" className="text-[12px] text-danger">
          {error}
        </span>
      ) : null}
    </div>
  );
}
