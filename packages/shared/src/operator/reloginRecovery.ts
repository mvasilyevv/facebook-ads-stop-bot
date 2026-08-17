import type {
  OperatorActionItem,
  OperatorActionState,
  OperatorAttentionItem,
  OperatorSnapshot,
} from "./contracts";

export interface OperatorReloginRecovery {
  incident: OperatorAttentionItem;
  scanAction: OperatorActionItem | null;
}

export type ReloginRecoveryButtonState =
  | "ready"
  | "sent"
  | "running"
  | "blocked"
  | "error";

/** Find the typed active re-login incident and its latest relevant scan. */
export function operatorReloginRecovery(
  snapshot: OperatorSnapshot,
): OperatorReloginRecovery | null {
  const incident = snapshot.attention.data?.items.find(
    (item) => item.kind === "incident" && item.recovery_action === "retry_scan",
  );
  if (!incident) return null;

  const scans = (snapshot.actions.data?.items ?? [])
    .filter((item) => item.kind === "scan")
    .sort(
      (left, right) =>
        Date.parse(right.requested_at) - Date.parse(left.requested_at),
    );
  const activeScan = scans.find(
    (item) => item.state === "queued" || item.state === "running",
  );
  const incidentStartedAt = Date.parse(incident.occurred_at);
  const latestRecoveryScan = scans.find(
    (item) => Date.parse(item.requested_at) >= incidentStartedAt,
  );

  return {
    incident,
    scanAction: activeScan ?? latestRecoveryScan ?? null,
  };
}

/**
 * Кнопка показывает только подтверждённый lifecycle команды.
 *
 * Отмена — не отказ, а решение системы: скан финализируется через
 * mark_cancelled, когда сканирование выключено самим оператором, мониторить
 * нечего или owner scope мульти-каба небезопасен. Красное «Ошибка — повторить»
 * на таком исходе отправляло бы чинить исправную защиту и обещало бы повтор,
 * который завершится ровно так же, поэтому у отмены отдельное состояние
 * `blocked` — та же шкала внимания, что у `operatorCommandTone`.
 *
 * Конкретной причины отмены на этом уровне нет и она сюда не пробрасывается:
 * `/api/operator/actions` намеренно отдаёт копию, выведенную только из
 * lifecycle (`task_action_reason`), а не из `last_error`. Ради длины подписи
 * потребовалось бы новое allowlist-поле в общем `OperatorActionItem`, повторный
 * экспорт OpenAPI и правка обоих фронтов, тогда как «что чинить» уже живёт
 * рядом в секции внимания со своими маршрутами.
 *
 * Кнопка при этом остаётся нажимаемой: после исправления настройки ручной
 * повтор — ровно то, что нужно оператору, и другого ручного пути нет.
 */
export function reloginRecoveryButtonState({
  actionState,
  receiptState,
  requestPending,
  requestFailed,
}: {
  actionState?: OperatorActionState | null;
  receiptState?: OperatorActionState | null;
  requestPending: boolean;
  requestFailed: boolean;
}): ReloginRecoveryButtonState {
  if (requestFailed) return "error";
  if (requestPending) return "sent";
  const state = actionState ?? receiptState;
  if (state === "running") return "running";
  if (state === "queued") return "sent";
  if (state === "cancelled") return "blocked";
  // failed — команда провалилась, unknown — итог не подтверждён: оба требуют
  // взгляда оператора, и повторный скан ничего не ломает, потому что читает.
  if (state === "failed" || state === "unknown") return "error";
  return "ready";
}

export const RELOGIN_RECOVERY_BUTTON_LABEL: Record<
  ReloginRecoveryButtonState,
  string
> = {
  ready: "Повторить скан",
  sent: "Отправлено",
  running: "Выполняется",
  blocked: "Скан отменён системой",
  error: "Ошибка — повторить",
};

export type ReloginRecoveryButtonTone = "neutral" | "warning";

/**
 * Тон кнопки задаётся здесь, а не в каждом фронте: не выполненный скан не
 * должен выглядеть обычной рабочей кнопкой ни в web, ни в мини-аппе, а новое
 * состояние иначе молча получило бы нейтральный вид.
 */
export const RELOGIN_RECOVERY_BUTTON_TONE: Record<
  ReloginRecoveryButtonState,
  ReloginRecoveryButtonTone
> = {
  ready: "neutral",
  sent: "neutral",
  running: "neutral",
  blocked: "warning",
  error: "warning",
};
