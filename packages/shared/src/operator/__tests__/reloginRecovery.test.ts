import { describe, expect, it } from "vitest";

import { makeOperatorSnapshot } from "../testFixture";
import {
  operatorReloginRecovery,
  reloginRecoveryButtonState,
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
  ])("maps command evidence to %s", (input, expected) => {
    expect(reloginRecoveryButtonState(input)).toBe(expected);
  });
});
