import { describe, expect, it } from "vitest";

import { makeOperatorSnapshot } from "../testFixture";
import {
  collapseConsecutiveOperatorActions,
  formatOperatorDateTime,
  formatOperatorFreshness,
  collapseOperatorAttentionItems,
  operatorAttentionCopy,
  operatorCabinetDisplayName,
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
      [source("a", "critical"), source("b", "unknown"), source("c", "unknown")],
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

  it("collapses only adjacent action repeats and keeps the freshest of each group", () => {
    const base = makeOperatorSnapshot().actions.data!.items[0]!;
    const repeat = (id: string, updatedAt: string) => ({
      ...base,
      id,
      public_id: `#${id}`,
      updated_at: updatedAt,
    });
    // A, A, B, A — вторая «A» не рядом с первой парой: сливать её означало бы
    // перемешать хронологию ленты.
    const groups = collapseConsecutiveOperatorActions([
      repeat("1845", "2026-07-18T10:16:00Z"),
      repeat("1844", "2026-07-18T10:15:00Z"),
      { ...repeat("9001", "2026-07-18T10:14:00Z"), state: "failed" as const },
      repeat("1842", "2026-07-18T10:13:00Z"),
    ]);

    expect(groups.map((group) => group.count)).toEqual([2, 1, 1]);
    expect(groups.map((group) => group.item.public_id)).toEqual([
      "#1845",
      "#9001",
      "#1842",
    ]);
  });

  it("does not merge two failures that fell for different reasons", () => {
    const base = makeOperatorSnapshot().actions.data!.items[0]!;
    const failure = (id: string, reason: string) => ({
      ...base,
      id,
      public_id: `#${id}`,
      state: "failed" as const,
      reason,
    });

    // Свёртка по состоянию спрятала бы вторую причину за «×2» — ровно так
    // пять разных отказов залива читались одной строкой.
    const groups = collapseConsecutiveOperatorActions([
      failure("9101", "Шаг: создание объектов кампании. Meta отказала."),
      failure("9102", "Шаг: загрузка креативов. Истёк отведённый заливу срок."),
    ]);

    expect(groups.map((group) => group.count)).toEqual([1, 1]);
  });

  it("does not merge the same command aimed at different ads", () => {
    const base = makeOperatorSnapshot().actions.data!.items[0]!;

    const groups = collapseConsecutiveOperatorActions([
      { ...base, id: "1", public_id: "#1", target_label: "Ad A" },
      { ...base, id: "2", public_id: "#2", target_label: "Ad B" },
    ]);

    expect(groups.map((group) => group.count)).toEqual([1, 1]);
  });

  it("takes the freshest repeat as the row even when the list is not ordered", () => {
    const base = makeOperatorSnapshot().actions.data!.items[0]!;

    const groups = collapseConsecutiveOperatorActions([
      { ...base, id: "1", public_id: "#1", updated_at: "2026-07-18T10:13:00Z" },
      { ...base, id: "2", public_id: "#2", updated_at: "2026-07-18T10:19:00Z" },
      { ...base, id: "3", public_id: "#3", updated_at: "2026-07-18T10:15:00Z" },
    ]);

    expect(groups).toHaveLength(1);
    expect(groups[0]!.count).toBe(3);
    expect(groups[0]!.item.public_id).toBe("#2");
  });
});

// act_ — приставка адреса Meta, а не часть идентичности кабинета: в списке она
// одинакова у всех строк, удлиняет их и мешает сверить номер глазами.
describe("operatorCabinetDisplayName", () => {
  it("снимает транспортный префикс act_", () => {
    expect(operatorCabinetDisplayName("act_1234567890123456")).toBe("1234567890123456");
  });

  it("оставляет чистый номер как есть", () => {
    expect(operatorCabinetDisplayName("1234567890123456")).toBe("1234567890123456");
  });

  it("не падает на отсутствующем значении", () => {
    expect(operatorCabinetDisplayName(null)).toBe("");
  });
});
