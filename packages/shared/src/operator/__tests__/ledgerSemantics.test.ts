import { describe, expect, it } from "vitest";

import { makeOperatorSnapshot } from "../testFixture";
import {
  formatOperatorDateTime,
  formatOperatorFreshness,
  collapseOperatorAttentionItems,
  operatorAttentionCopy,
  operatorCabinetTimezone,
  operatorLedgerTimezone,
  operatorReasonNoun,
  operatorSourceLabel,
} from "../ledgerSemantics";

describe("operator ledger semantics", () => {
  it("uses the selected cabinet timezone on a cabinet route", () => {
    const snapshot = makeOperatorSnapshot();

    expect(operatorLedgerTimezone(snapshot)).toBe("Europe/Kaliningrad");
    expect(operatorLedgerTimezone(snapshot, "123")).toBe("Africa/Accra");
    expect(operatorLedgerTimezone(snapshot, "act_456")).toBe("Europe/Warsaw");
    expect(
      formatOperatorDateTime(
        snapshot.meta.generated_at,
        operatorLedgerTimezone(snapshot, "123"),
      ),
    ).toContain("10:15");
  });

  it("does not present the global formatting fallback as cabinet evidence", () => {
    const snapshot = makeOperatorSnapshot();
    snapshot.portfolio.data!.currency_groups[0]!.cabinets[0]!.timezone = null;
    snapshot.meta.cabinet_timezone = null;
    snapshot.meta.cabinet_timezone_known = false;
    snapshot.meta.cabinet_timezone_state = "unknown";

    expect(operatorCabinetTimezone(snapshot, "123")).toBeNull();
    expect(operatorLedgerTimezone(snapshot, "123")).toBeNull();
    expect(
      formatOperatorDateTime(
        snapshot.meta.generated_at,
        operatorLedgerTimezone(snapshot, "123"),
      ),
    ).toBe("не подтверждено");
  });

  it("keeps shared Russian ledger labels consistent", () => {
    expect(formatOperatorFreshness(null)).toBe("не подтверждено");
    expect(formatOperatorFreshness(61)).toBe("1 мин");
    expect([1, 2, 5, 11, 21].map(operatorReasonNoun)).toEqual([
      "причина",
      "причины",
      "причин",
      "причин",
      "причина",
    ]);
    expect(operatorSourceLabel("task_queue")).toBe("CommandService");
    expect(operatorSourceLabel("cabinet_runtime")).toBe("акторы кабинетов");
    expect(operatorSourceLabel("custom_source")).toBe("Источник данных");
  });

  it("keeps incident business copy only for confirmed USD", () => {
    const incident = makeOperatorSnapshot().attention.data!.items[0]!;

    expect(operatorAttentionCopy(incident, true)).toEqual({
      title: "CPL выше базы",
      summary: "CPL $9.56 при базе $3.00.",
      reason: "Расход растёт без FTD",
    });
  });

  it("removes incident money and details when USD is not confirmed", () => {
    const incident = makeOperatorSnapshot().attention.data!.items[0]!;
    const copy = operatorAttentionCopy(
      {
        ...incident,
        title: "Перерасход $18.40",
        summary: "CPL $9.56 при базе $3.00.",
        reason: "До stop осталось $4.40",
      },
      false,
    );

    expect(copy).toEqual({
      title: "Сигнал требует проверки",
      summary: null,
      reason: null,
    });
    expect(JSON.stringify(copy)).not.toMatch(/[$0-9]/);
  });

  it("uses deterministic source/action copy without backend internals", () => {
    const incident = makeOperatorSnapshot().attention.data!.items[0]!;

    expect(
      operatorAttentionCopy(
        {
          ...incident,
          kind: "source",
          title: "worker_telemetry unavailable",
          summary: "worker_telemetry.connection_refused",
          reason: "cabinet_actor_error",
        },
        true,
      ),
    ).toEqual({
      title: "Источник требует проверки",
      summary: null,
      reason: null,
    });
    expect(
      operatorAttentionCopy(
        {
          ...incident,
          kind: "action",
          title: "pause_ad failed",
          summary: "#42 · failed",
          reason: "raw worker exception",
        },
        true,
      ),
    ).toEqual({
      title: "Команда требует сверки",
      summary: null,
      reason: null,
    });
  });

  it("collapses signals an operator cannot tell apart, but keeps severities separate", () => {
    const base = makeOperatorSnapshot().attention.data!.items[0]!;
    const source = (id: string, severity: "critical" | "unknown") => ({
      ...base,
      id,
      kind: "source" as const,
      severity,
      title: `worker_telemetry ${id}`,
      summary: `internal.${id}`,
      reason: null,
    });

    const collapsed = collapseOperatorAttentionItems(
      [
        source("a", "critical"),
        source("b", "unknown"),
        source("c", "unknown"),
      ],
      true,
    );

    // Тексты источников детерминированы, поэтому b и c для оператора
    // неразличимы и занимают одну строку. Критичное не сливается с
    // неподтверждённым: это разная срочность.
    expect(collapsed.map((entry) => entry.count)).toEqual([1, 2]);
    expect(collapsed.map((entry) => entry.item.severity)).toEqual([
      "critical",
      "unknown",
    ]);
  });

});
