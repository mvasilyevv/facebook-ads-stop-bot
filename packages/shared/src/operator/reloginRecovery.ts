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

export type ReloginRecoveryButtonState = "ready" | "sent" | "running" | "error";

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
  if (state === "failed" || state === "cancelled" || state === "unknown") {
    return "error";
  }
  return "ready";
}

export const RELOGIN_RECOVERY_BUTTON_LABEL: Record<
  ReloginRecoveryButtonState,
  string
> = {
  ready: "Повторить скан",
  sent: "Отправлено",
  running: "Выполняется",
  error: "Ошибка — повторить",
};
