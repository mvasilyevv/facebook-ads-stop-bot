import { describe, expect, it } from "vitest";

import { makeOperatorSnapshot } from "../testFixture";
import {
  operatorReloginRecovery,
  reloginRecoveryButtonState,
  RELOGIN_RECOVERY_BUTTON_LABEL,
  RELOGIN_RECOVERY_BUTTON_TONE,
  type ReloginRecoveryButtonState,
} from "../reloginRecovery";

describe("operator re-login recovery", () => {
  it("appears only for the typed active login incident", () => {
    const snapshot = makeOperatorSnapshot();
    expect(operatorReloginRecovery(snapshot)).toBeNull();

    snapshot.attention.data!.items[0]!.recovery_action = "retry_scan";
    expect(operatorReloginRecovery(snapshot)?.incident.id).toBe("incident-1");
  });

  it("uses the active scan lifecycle instead of creating an untracked UI state", () => {
    const snapshot = makeOperatorSnapshot();
    snapshot.attention.data!.items[0]!.recovery_action = "retry_scan";
    snapshot.actions.data!.items.push({
      id: "1843",
      public_id: "#1843",
      kind: "scan",
      state: "running",
      title: "Сканирование",
      target_id: null,
      target_label: null,
      requested_at: "2026-07-18T10:11:00Z",
      updated_at: "2026-07-18T10:14:00Z",
      requested_by: "operator:web",
      reason: null,
      correlation_id: "corr-scan",
      account_id: null,
      currency: null,
      cabinet_timezone: null,
      account_context_observed_at: null,
      account_context_issues: [],
    });

    expect(operatorReloginRecovery(snapshot)?.scanAction?.id).toBe("1843");
  });

  it.each([
    [{ requestPending: false, requestFailed: false }, "ready"],
    [{ requestPending: true, requestFailed: false }, "sent"],
    [{ requestPending: false, requestFailed: true }, "error"],
    [
      {
        actionState: "running" as const,
        requestPending: false,
        requestFailed: false,
      },
      "running",
    ],
    [
      {
        receiptState: "queued" as const,
        requestPending: false,
        requestFailed: false,
      },
      "sent",
    ],
    [
      {
        actionState: "failed" as const,
        requestPending: false,
        requestFailed: false,
      },
      "error",
    ],
    [
      {
        actionState: "unknown" as const,
        requestPending: false,
        requestFailed: false,
      },
      "error",
    ],
  ])("maps command evidence to %s", (input, expected) => {
    expect(reloginRecoveryButtonState(input)).toBe(expected);
  });

  it("does not call a deliberate cancellation a failure", () => {
    // Отменяет скан только сам observer: сканирование выключено оператором,
    // мониторить нечего или owner scope мульти-каба небезопасен. Ни один из
    // этих исходов не поломка, и повтор завершится ровно так же.
    expect(
      reloginRecoveryButtonState({
        actionState: "cancelled",
        requestPending: false,
        requestFailed: false,
      }),
    ).toBe("blocked");
    expect(
      reloginRecoveryButtonState({
        receiptState: "cancelled",
        requestPending: false,
        requestFailed: false,
      }),
    ).toBe("blocked");
  });

  it("keeps an in-flight request ahead of the previous cancellation", () => {
    // Оператор уже нажал кнопку после того, как починил настройку: показывать
    // прошлую отмену вместо отправки значило бы соврать о текущей команде.
    expect(
      reloginRecoveryButtonState({
        actionState: "cancelled",
        requestPending: true,
        requestFailed: false,
      }),
    ).toBe("sent");
    expect(
      reloginRecoveryButtonState({
        actionState: "cancelled",
        requestPending: false,
        requestFailed: true,
      }),
    ).toBe("error");
  });

  it("never words a blocked scan as an error and never promises a useless retry", () => {
    const label = RELOGIN_RECOVERY_BUTTON_LABEL.blocked;
    expect(label.toLowerCase()).not.toContain("ошибк");
    expect(label.toLowerCase()).not.toContain("повтор");
  });

  it("keeps every unfinished outcome visually distinct from a normal button", () => {
    // partial/stale/unavailable никогда не выглядят зелёными — не выполненный
    // скан подчиняется той же шкале: нейтральной остаётся только рабочая ветка.
    const tones: Record<ReloginRecoveryButtonState, "neutral" | "warning"> = {
      ready: "neutral",
      sent: "neutral",
      running: "neutral",
      blocked: "warning",
      error: "warning",
    };
    expect(RELOGIN_RECOVERY_BUTTON_TONE).toEqual(tones);
    expect(Object.keys(RELOGIN_RECOVERY_BUTTON_LABEL).sort()).toEqual(
      Object.keys(tones).sort(),
    );
  });
});
