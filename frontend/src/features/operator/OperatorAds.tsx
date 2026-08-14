import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "@tanstack/react-router";
import { useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, CircleHelp, CirclePause, CirclePlay, ShieldCheck } from "lucide-react";

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
} from "@fb/shared/operator/adsViewModel";
import {
  operatorActionStateReason,
  operatorCommandTone,
} from "@fb/shared/operator/actionLabels";
import { formatSpend } from "@fb/shared/format/number";
import { ACTION_STATE_LABEL, severityForDataState } from "@fb/shared/operator/viewModel";
import { DataStateBadge } from "@fb/operator-ui";
import { useOperatorRealtimeStatus } from "@fb/operator-api";

import { Button } from "@/components/ui/Button";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { toast } from "@/components/ui/Toast";
import {
  fetchOperatorAdForCommand,
  operatorProblemMessage,
  useActivateOperatorAd,
  usePauseOperatorAd,
} from "@/lib/api/operator";

const SEVERITY_LABEL: Record<OperatorSeverity, string> = {
  ok: "Норма",
  warning: "Внимание",
  critical: "Опасность",
  unknown: "Неизвестно",
};

const SEVERITY_ICON = {
  ok: ShieldCheck,
  warning: AlertTriangle,
  critical: AlertTriangle,
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
      <table className="w-full min-w-[860px] border-collapse text-left text-[14px]">
        <thead className="text-[12px] uppercase tracking-[.06em] text-bg-8">
          <tr className="border-b border-[var(--color-hairline)]">
            <th className="px-3 py-3 font-medium">Объявление</th>
            <th className="px-3 py-3 font-medium">Состояние</th>
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
                </div>
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
        variant={isPause ? "danger" : "secondary"}
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
        confirmVariant={isPause ? "danger" : "primary"}
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

function MetricCell({ value }: { value: string }) {
  return <td className="px-3 py-3 text-right font-numeric text-[14px] text-bg-11">{value}</td>;
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-[12px] text-bg-8">{label}</dt>
      <dd className="mt-1 font-numeric text-[14px] text-bg-11">{value}</dd>
    </div>
  );
}
