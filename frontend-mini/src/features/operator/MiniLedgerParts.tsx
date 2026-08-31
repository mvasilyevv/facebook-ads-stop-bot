/**
 * Общие части двух реестровых экранов: портфельного («Сейчас») и кабинетного.
 * Экраны различаются составом секций, поэтому лежат в разных модулях: экран
 * кабинета тянет за собой список объявлений и путь команды, и пока он жил в
 * одном файле с портфелем, этот код попадал в стартовый чанк мини-приложения
 * (issue #349).
 */
import { useState, type ReactNode } from "react";
import { Link, useNavigate } from "@tanstack/react-router";
import {
  AlertTriangle,
  ArrowRight,
  Check,
  CircleHelp,
  Clock3,
  OctagonAlert,
  RefreshCw,
  X,
  type LucideIcon,
} from "lucide-react";

import {
  ACTION_STATE_LABEL,
  DATA_STATE_LABEL,
  SEVERITY_LABEL,
} from "@fb/shared/operator/viewModel";
import { operatorActionReason } from "@fb/shared/operator/actionLabels";
import {
  completeOperatorCommandIntent,
  getOrCreateOperatorCommandIntent,
} from "@fb/shared/operator/commandIntent";
import {
  formatOperatorDateTime as formatDateTime,
  formatOperatorFreshness as freshnessLabel,
  formatOperatorUsd as formatUsd,
  isActiveOperatorAction as isActiveAction,
  collapseConsecutiveOperatorActions,
  operatorAttentionCopy,
  operatorReasonNoun as pluralReason,
  operatorSourceLabel as sourceLabel,
} from "@fb/shared/operator/ledgerSemantics";
import {
  operatorReloginRecovery,
  RELOGIN_RECOVERY_BUTTON_LABEL,
  RELOGIN_RECOVERY_BUTTON_TONE,
  reloginRecoveryButtonState,
} from "@fb/shared/operator/reloginRecovery";
import type {
  OperatorActionItem,
  OperatorAttentionItem,
  OperatorCommandResponse,
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
import { operatorProblemMessage, useOperatorRetryScan } from "@/lib/operatorApi";

import "./operator-mini-ledger.css";

// critical и warning не должны различаться одним лишь цветом:
// восьмиугольник читается как «стоп» и при цветовой слепоте.
export const SEVERITY_ICON: Record<OperatorSeverity, LucideIcon> = {
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

export interface SnapshotQueryLike {
  data?: OperatorSnapshot;
  isLoading: boolean;
  isError: boolean;
  error: unknown;
  refetch: () => unknown;
}

/**
 * Постановка повторного скана после разлогина. Хук вызывается до ранних
 * возвратов экрана и принимает `snapshot === null`, пока снимок не подтверждён:
 * порядок хуков не должен зависеть от состояния загрузки.
 */
export function useMiniScanRecovery(
  snapshot: OperatorSnapshot | null,
  refetch: () => unknown,
) {
  const scan = useOperatorRetryScan();
  const [scanReceipt, setScanReceipt] = useState<{
    incidentId: string;
    receipt: OperatorCommandResponse;
  } | null>(null);
  const [failedIncidentId, setFailedIncidentId] = useState<string | null>(null);

  const recovery = snapshot ? operatorReloginRecovery(snapshot) : null;
  const currentReceipt =
    recovery && scanReceipt?.incidentId === recovery.incident.id
      ? scanReceipt.receipt
      : null;
  const receiptAction =
    currentReceipt && snapshot
      ? snapshot.actions.data?.items.find(
          (item) => item.id === String(currentReceipt.task_id),
        )
      : null;
  const recoveryState = recovery
    ? reloginRecoveryButtonState({
        actionState: currentReceipt
          ? receiptAction?.state
          : recovery.scanAction?.state,
        receiptState: currentReceipt?.state,
        requestPending: scan.isPending,
        requestFailed: failedIncidentId === recovery.incident.id,
      })
    : null;

  const runScan = async () => {
    if (!recovery) return;
    haptic.impact("medium");
    setFailedIncidentId(null);
    const incidentId = recovery.incident.id;
    try {
      const idempotencyKey = getOrCreateOperatorCommandIntent(
        "retry_scan",
        incidentId,
      );
      const receipt = await scan.mutateAsync({
        params: { header: { "Idempotency-Key": idempotencyKey } },
      });
      completeOperatorCommandIntent("retry_scan", incidentId, idempotencyKey);
      setScanReceipt({ incidentId, receipt });
      haptic.notify("warning");
      void refetch();
    } catch {
      setFailedIncidentId(incidentId);
      haptic.notify("error");
    }
  };

  return {
    recovery,
    recoveryState,
    currentReceipt,
    scanPending: scan.isPending,
    runScan,
  };
}

/** Переход по действию карточки «Требует внимания». */
export function useOpenAttentionAction() {
  const navigate = useNavigate();
  return async (href: string) => {
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
}

/** Правый блок шапки: доказательство свежести и кнопка повторного скана. */
export function MiniLedgerTools({
  overviewState,
  freshnessSeconds,
  StatusIcon,
  recovery,
  recoveryState,
  scanPending,
  onScan,
}: {
  overviewState: OperatorSnapshot["portfolio"]["state"];
  freshnessSeconds: number | null;
  StatusIcon: LucideIcon;
  recovery: ReturnType<typeof operatorReloginRecovery>;
  recoveryState: ReturnType<typeof reloginRecoveryButtonState> | null;
  scanPending: boolean;
  onScan: () => Promise<void>;
}) {
  return (
    <div className="mini-ledger__tools">
      <Link className="mini-proof" to="/system/sources">
        <StatusIcon size={15} aria-hidden="true" />
        <span>{miniStateLabel(overviewState)}</span>
        <span>{freshnessLabel(freshnessSeconds)}</span>
      </Link>
      {recovery && recoveryState ? (
        <Button
          type="button"
          variant={
            RELOGIN_RECOVERY_BUTTON_TONE[recoveryState] === "warning"
              ? "warning"
              : "primary"
          }
          size="md"
          className="mini-scan"
          aria-live="polite"
          disabled={recoveryState === "sent"}
          loading={scanPending || recoveryState === "running"}
          data-state={recoveryState}
          onClick={() => void onScan()}
        >
          {!scanPending && recoveryState !== "running" ? (
            <RefreshCw size={18} aria-hidden="true" />
          ) : null}
          <span>{RELOGIN_RECOVERY_BUTTON_LABEL[recoveryState]}</span>
        </Button>
      ) : null}
    </div>
  );
}

/** Расписка поставленного скана: 202 — это очередь, а не подтверждённый успех. */
export function MiniScanReceipt({
  receipt,
}: {
  receipt: OperatorCommandResponse;
}) {
  return (
    <div role="status" aria-live="polite" className="mini-ledger__receipt">
      <div>
        <strong>
          {receipt.state === "running"
            ? "Сканирование выполняется"
            : "Сканирование поставлено в очередь"}
        </strong>
        <span>Задача {receipt.public_id}</span>
      </div>
      <Link
        to="/actions/$actionId"
        params={{ actionId: String(receipt.task_id) }}
        className="mini-ledger__inline-action"
      >
        Открыть выполнение
      </Link>
    </div>
  );
}

export function MiniSnapshotError({
  title,
  error,
  onRetry,
}: {
  title: string;
  error: unknown;
  onRetry: () => void;
}) {
  return (
    <div
      role="alert"
      className="m-4 rounded-[var(--radius-3)] border border-danger/40 bg-danger-bg p-4"
    >
      <strong className="text-[16px] text-bg-11">{title}</strong>
      <p className="mt-2 text-[14px] leading-5 text-bg-10">
        {operatorProblemMessage(error)}
      </p>
      <Button className="mt-4 min-h-11" onClick={onRetry}>
        Повторить
      </Button>
    </div>
  );
}

export function MiniAttentionLedger({
  section,
  timezone,
  usdScopeConfirmed,
  onAction,
}: {
  section: OperatorSnapshot["attention"];
  timezone: string | null;
  usdScopeConfirmed: boolean;
  onAction: (href: string) => Promise<void>;
}) {
  // Счётчик считается по полному списку, а не по первым пяти карточкам.
  // Без подтверждённых данных выводим «—»: ноль означал бы «причин нет».
  const total = section.data ? section.data.items.length : null;
  const items = section.data?.items.slice(0, 5) ?? [];
  return (
    <MiniLedgerSection
      className="mini-ledger-section--attention"
      id="mini-attention-title"
      title="Требует внимания"
      detail={total === null ? "—" : `${total} ${pluralReason(total)}`}
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
            <MiniAttentionItem
              key={item.id}
              item={item}
              usdScopeConfirmed={usdScopeConfirmed}
              onAction={onAction}
            />
          ))}
        </ol>
      )}
      <Link to="/incidents" className="mini-ledger__inline-action mx-4 min-h-11">
        Все инциденты
        <ArrowRight size={14} aria-hidden="true" />
      </Link>
    </MiniLedgerSection>
  );
}

function MiniAttentionItem({
  item,
  usdScopeConfirmed,
  onAction,
}: {
  item: OperatorAttentionItem;
  usdScopeConfirmed: boolean;
  onAction: (href: string) => Promise<void>;
}) {
  const Icon = SEVERITY_ICON[item.severity];
  const copy = operatorAttentionCopy(item, usdScopeConfirmed);
  return (
    <li className="mini-attention-item" data-severity={item.severity}>
      <div className="mini-attention-item__head">
        <span>{item.target.label ?? "Объект не указан"}</span>
        <span data-severity={item.severity}>
          <Icon size={14} aria-hidden="true" />
          {SEVERITY_LABEL[item.severity]}
        </span>
      </div>
      <h3>{copy.title}</h3>
      {copy.summary ? <p>{copy.summary}</p> : null}
      {copy.reason ? <p>Причина: {copy.reason}</p> : null}
      {item.action?.href === "/system/sources" ? (
        <Link to="/system/sources">
          {item.action.label}
          <ArrowRight size={14} aria-hidden="true" />
        </Link>
      ) : item.action ? (
        <button type="button" onClick={() => void onAction(item.action!.href)}>
          {item.action.label}
          <ArrowRight size={14} aria-hidden="true" />
        </button>
      ) : null}
    </li>
  );
}

export function MiniActionJournal({
  section,
}: {
  section: OperatorSnapshot["actions"];
}) {
  // Счётчик считается по полному списку, а не по первым пяти карточкам.
  // Без подтверждённых данных выводим «—»: ноль означал бы «команд нет».
  const activeTotal = section.data
    ? section.data.items.filter(isActiveAction).length
    : null;
  const items = section.data?.items.slice(0, 5) ?? [];
  return (
    <MiniLedgerSection
      className="mini-ledger-section--actions"
      id="mini-actions-title"
      title="Действия"
      detail={activeTotal === null ? "—" : `${activeTotal} выполняется`}
      section={section}
    >
      {!section.data ? (
        <MiniLedgerEmpty text={DATA_STATE_LABEL[section.state]} />
      ) : items.length === 0 ? (
        <MiniLedgerEmpty text="Активных действий нет." />
      ) : (
        <ol className="mini-action-journal">
          {collapseConsecutiveOperatorActions(items).map(({ item, count }) => {
            const Icon = ACTION_ICON[item.state];
            return (
              <li key={item.id}>
                <div className="mini-action-journal__head">
                  <span>
                    {item.title} · {item.target_label ?? "система"}
                  </span>
                  {/* Счётчик вынесен из заголовка: в нём ellipsis, и на узком
                      экране длинное имя цели съело бы именно его. */}
                  {count > 1 ? (
                    <span className="mini-action-journal__repeats">
                      ×{count}
                    </span>
                  ) : null}
                  <span data-state={item.state}>
                    <Icon size={14} aria-hidden="true" />
                    {ACTION_STATE_LABEL[item.state]}
                  </span>
                </div>
                <p>{operatorActionReason(item)}</p>
                {isActiveAction(item) ? (
                  <div
                    className="mini-action-journal__progress"
                    aria-hidden="true"
                  />
                ) : null}
                <div className="mini-action-journal__meta">
                  <span>Задача {item.public_id}</span>
                  {count > 1 ? (
                    <span>
                      Последний повтор{" "}
                      {formatDateTime(item.updated_at, item.cabinet_timezone)}
                    </span>
                  ) : null}
                </div>
                {item.run_id ? (
                  // У залива есть свой экран: состав, созданные объекты и
                  // управление. Карточка действия показала бы конвейер обработки.
                  <Link to="/campaigns" search={{ run: item.run_id }}>
                    Открыть залив
                    <ArrowRight size={14} aria-hidden="true" />
                  </Link>
                ) : (
                  <Link to="/actions/$actionId" params={{ actionId: item.id }}>
                    Открыть действие
                    <ArrowRight size={14} aria-hidden="true" />
                  </Link>
                )}
              </li>
            );
          })}
        </ol>
      )}
      <Link to="/actions" className="mini-ledger__inline-action mx-4 min-h-11">
        Все действия
        <ArrowRight size={14} aria-hidden="true" />
      </Link>
    </MiniLedgerSection>
  );
}

export function MiniLedgerSection<T>({
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
  timezone?: string | null;
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
            {section.as_of
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

export function MiniLedgerTotal({
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

export function MiniLedgerEmpty({ text }: { text: string }) {
  return <div className="mini-ledger-section__empty">{text}</div>;
}

export function miniStateLabel(
  state: OperatorSnapshot["portfolio"]["state"],
): string {
  const labels = {
    ready: "Актуально",
    empty: "Пусто",
    partial: "Неполные",
    stale: "Устарели",
    unavailable: "Недоступны",
  } as const;
  return labels[state];
}

export function MiniLoading() {
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
