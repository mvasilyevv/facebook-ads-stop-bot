import { describe, expect, it } from "vitest";

import { readTrackerRealtime } from "@/lib/types/trackerRealtime";

describe("readTrackerRealtime", () => {
  it("читает новый вложенный event-driven контракт", () => {
    const result = readTrackerRealtime({
      tracker: {
        available: true,
        totals: {
          registrations: 12,
          ftds: 5,
          confirmed_deposits: 4,
          redeposits: 2,
        },
        unmatched_events: 1,
        last_event_at: "2026-07-14T10:00:00Z",
        processing_lag_ms: 420,
        data_quality: "live",
        backlog: 0,
        duplicate_events: 3,
        unsupported_events: 2,
        reconciliation_drift: 0,
      },
    });

    expect(result).toMatchObject({
      registrations: 12,
      ftds: 5,
      confirmedDeposits: 4,
      redeposits: 2,
      unmatchedEvents: 1,
      processingLagMs: 420,
      dataQuality: "live",
      backlog: 0,
      duplicateEvents: 3,
      unsupportedEvents: 2,
      reconciliationDrift: 0,
    });
  });

  it("legacy deposits трактует как FTD, но не как подтверждённый депозит", () => {
    const result = readTrackerRealtime({
      available: true,
      totals: { registrations: 8, deposits: 3 },
    });

    expect(result?.registrations).toBe(8);
    expect(result?.ftds).toBe(3);
    expect(result?.confirmedDeposits).toBeNull();
  });

  it("поддерживает временные плоские tracker_* поля", () => {
    const result = readTrackerRealtime({
      tracker_registrations: 7,
      tracker_ftds: 2,
      tracker_confirmed_deposits: 1,
      tracker_unmatched_events: 4,
    });

    expect(result).toMatchObject({
      registrations: 7,
      ftds: 2,
      confirmedDeposits: 1,
      unmatchedEvents: 4,
    });
  });

  it("не выдаёт Meta history totals за AdSet.pro", () => {
    expect(
      readTrackerRealtime({
        totals: { registrations: 150, deposits: 50, spend: "1234.56" },
        alerts: { stop_count: 3 },
      }),
    ).toBeNull();
  });
});
