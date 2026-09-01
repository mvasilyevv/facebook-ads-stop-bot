import { describe, expect, it } from "vitest";

import type {
  OperatorActionItem,
  OperatorAttentionItem,
  OperatorSnapshot,
} from "../contracts";
import {
  collapseDecisionRows,
  combineDecisionFeedState,
  compareDecisionRows,
  decisionPrimaryAction,
  decisionRowAge,
  selectDecisionRows,
  type DecisionRow,
} from "../decisionFeed";
import { makeOperatorSnapshot } from "../testFixture";

function attentionItem(
  overrides: Partial<OperatorAttentionItem> & Pick<OperatorAttentionItem, "id">,
): OperatorAttentionItem {
  return {
    kind: "incident",
    severity: "warning",
    title: "Заголовок",
    summary: "Контекст",
    reason: null,
    occurred_at: "2026-07-18T10:00:00Z",
    target: { kind: "ad", id: "1", label: "GH_CR2" },
    action: { label: "Открыть", href: "/incidents/1" },
    recovery_action: null,
    status: null,
    requires_usd_evidence: false,
    ...overrides,
  };
}

function actionItem(
  overrides: Partial<OperatorActionItem> & Pick<OperatorActionItem, "id">,
): OperatorActionItem {
  return {
    public_id: `#${overrides.id}`,
    manual_review_available: false,
    kind: "pause",
    state: "queued",
    title: "Отключение объявления",
    target_label: "GH_CR2",
    requested_at: "2026-07-18T09:00:00Z",
    updated_at: "2026-07-18T09:30:00Z",
    requested_by: "operator",
    reason: null,
    correlation_id: `corr-${overrides.id}`,
    account_id: "act_123",
    currency: "USD",
    cabinet_timezone: "Europe/Kaliningrad",
    account_context_observed_at: "2026-07-18T09:30:00Z",
    account_context_issues: [],
    ...overrides,
  };
}

/** Снимок с полностью управляемыми attention/actions секциями. */
function snapshotWith(
  items: OperatorAttentionItem[],
  actions: OperatorActionItem[] = [],
): OperatorSnapshot {
  const snapshot = makeOperatorSnapshot();
  snapshot.attention.data = {
    items,
    total: items.length,
    truncated: false,
    decisions_count: items.length,
    decisions_critical: false,
  };
  snapshot.actions.data = { items: actions };
  return snapshot;
}

function sortedRows(snapshot: OperatorSnapshot): DecisionRow[] {
  return [...selectDecisionRows(snapshot)].sort(compareDecisionRows);
}

describe("selectDecisionRows", () => {
  it("always takes incidents regardless of status", () => {
    const snapshot = snapshotWith([
      attentionItem({ id: "inc-open", kind: "incident", status: "open" }),
      attentionItem({ id: "inc-resolved", kind: "incident", status: "resolved" }),
    ]);
    expect(selectDecisionRows(snapshot).map((row) => row.id)).toEqual([
      "inc-open",
      "inc-resolved",
    ]);
  });

  it("takes action rows only when the joined action state is failed or unknown", () => {
    const snapshot = snapshotWith(
      [
        attentionItem({ id: "task:1", kind: "action", severity: "warning" }),
        attentionItem({ id: "task:2", kind: "action", severity: "warning" }),
        attentionItem({ id: "task:3", kind: "action", severity: "warning" }),
      ],
      [
        actionItem({ id: "1", state: "failed" }),
        actionItem({ id: "2", state: "unknown" }),
        actionItem({ id: "3", state: "running" }),
      ],
    );
    expect(selectDecisionRows(snapshot).map((row) => row.id)).toEqual([
      "task:1",
      "task:2",
    ]);
  });

  it("includes an action row when the join to snapshot.actions finds nothing (fail open, not closed)", () => {
    const snapshot = snapshotWith(
      [attentionItem({ id: "task:missing", kind: "action" })],
      [], // actions section empty/not yet confirmed
    );
    expect(selectDecisionRows(snapshot).map((row) => row.id)).toEqual([
      "task:missing",
    ]);
    expect(selectDecisionRows(snapshot)[0]!.actionState).toBeNull();
  });

  it("takes source rows only when severity is not ok", () => {
    const snapshot = snapshotWith([
      attentionItem({ id: "source:ok", kind: "source", severity: "ok" }),
      attentionItem({ id: "source:warn", kind: "source", severity: "warning" }),
    ]);
    expect(selectDecisionRows(snapshot).map((row) => row.id)).toEqual([
      "source:warn",
    ]);
  });

  it("excludes kinds outside the v1 decision list (e.g. recommendation)", () => {
    const snapshot = snapshotWith([
      attentionItem({
        id: "rec-1",
        kind: "recommendation",
        severity: "critical",
      }),
    ]);
    expect(selectDecisionRows(snapshot)).toHaveLength(0);
  });
});

describe("compareDecisionRows", () => {
  it("produces a fixed, deterministic order for a fixed snapshot", () => {
    const snapshot = snapshotWith([
      attentionItem({
        id: "b-warning-ad",
        severity: "warning",
        target: { kind: "ad", id: "2", label: "ad-2" },
        occurred_at: "2026-07-18T09:00:00Z",
      }),
      attentionItem({
        id: "a-critical",
        severity: "critical",
        target: { kind: "ad", id: "3", label: "ad-3" },
        occurred_at: "2026-07-18T09:30:00Z",
      }),
      attentionItem({
        id: "c-unknown",
        severity: "unknown",
        target: { kind: "account", id: "act_1", label: "acc" },
        occurred_at: "2026-07-18T08:00:00Z",
      }),
      attentionItem({
        id: "d-ok",
        kind: "source",
        severity: "warning",
        target: { kind: "system", id: null, label: "Система" },
        occurred_at: "2026-07-18T07:00:00Z",
      }),
    ]);

    const order = sortedRows(snapshot).map((row) => row.id);
    expect(order).toEqual(["a-critical", "c-unknown", "b-warning-ad", "d-ok"]);
    // Same fixture, ran again — same order (no hidden non-determinism).
    expect(sortedRows(snapshot).map((row) => row.id)).toEqual(order);
  });

  it("ranks unknown above warning", () => {
    const unknown = attentionItem({ id: "u", severity: "unknown" });
    const warning = attentionItem({ id: "w", severity: "warning" });
    const snapshot = snapshotWith([warning, unknown]);
    expect(sortedRows(snapshot).map((row) => row.id)).toEqual(["u", "w"]);
  });

  it("ranks money targets (ad/campaign/account) above system at equal severity", () => {
    const system = attentionItem({
      id: "system-row",
      kind: "source",
      severity: "critical",
      target: { kind: "system", id: null, label: "Система" },
    });
    const money = attentionItem({
      id: "money-row",
      kind: "incident",
      severity: "critical",
      target: { kind: "campaign", id: "9", label: "camp" },
    });
    const snapshot = snapshotWith([system, money]);
    expect(sortedRows(snapshot).map((row) => row.id)).toEqual([
      "money-row",
      "system-row",
    ]);
  });

  it("ranks the oldest occurred_at first at equal severity and money rank (queue, not a news feed)", () => {
    const newer = attentionItem({
      id: "newer",
      occurred_at: "2026-07-18T10:00:00Z",
    });
    const older = attentionItem({
      id: "older",
      occurred_at: "2026-07-18T08:00:00Z",
    });
    const snapshot = snapshotWith([newer, older]);
    expect(sortedRows(snapshot).map((row) => row.id)).toEqual([
      "older",
      "newer",
    ]);
  });

  it("falls back to id ascending as the final deterministic tie-break", () => {
    const same = "2026-07-18T10:00:00Z";
    const b = attentionItem({ id: "b", occurred_at: same });
    const a = attentionItem({ id: "a", occurred_at: same });
    const snapshot = snapshotWith([b, a]);
    expect(sortedRows(snapshot).map((row) => row.id)).toEqual(["a", "b"]);
  });
});

describe("decisionPrimaryAction", () => {
  it("returns pause for an incident targeting a numeric ad id", () => {
    expect(
      decisionPrimaryAction({
        kind: "incident",
        target: { kind: "ad", id: "501", label: "ad" },
        status: "resolved",
        actionState: null,
        href: null,
      }),
    ).toEqual({ kind: "pause", adId: "501" });
  });

  it("never returns pause for a non-numeric target.id (redacted UUID, not a real ad id)", () => {
    expect(
      decisionPrimaryAction({
        kind: "incident",
        target: { kind: "ad", id: "объект", label: "ad" },
        status: "open",
        actionState: null,
        href: null,
      }),
    ).toEqual({ kind: "acknowledge" });

    expect(
      decisionPrimaryAction({
        kind: "incident",
        target: { kind: "ad", id: "объект", label: "ad" },
        status: "resolved",
        actionState: null,
        href: null,
      }),
    ).toBeNull();
  });

  it("returns acknowledge only when status is open", () => {
    expect(
      decisionPrimaryAction({
        kind: "incident",
        target: { kind: "campaign", id: "9", label: "camp" },
        status: "open",
        actionState: null,
        href: null,
      }),
    ).toEqual({ kind: "acknowledge" });

    expect(
      decisionPrimaryAction({
        kind: "incident",
        target: { kind: "campaign", id: "9", label: "camp" },
        status: "acknowledged",
        actionState: null,
        href: null,
      }),
    ).toBeNull();
  });

  it("returns check_meta for a failed/unknown action, and null for running", () => {
    expect(
      decisionPrimaryAction({
        kind: "action",
        target: { kind: "system", id: null, label: null },
        status: null,
        actionState: "unknown",
        href: "/actions/1",
      }),
    ).toEqual({ kind: "check_meta", href: "/actions/1" });

    expect(
      decisionPrimaryAction({
        kind: "action",
        target: { kind: "system", id: null, label: null },
        status: null,
        actionState: "running",
        href: "/actions/1",
      }),
    ).toBeNull();
  });

  it("returns null for a source row (diagnostics-only, navigation lives on the row itself)", () => {
    expect(
      decisionPrimaryAction({
        kind: "source",
        target: { kind: "system", id: null, label: "Система" },
        status: null,
        actionState: null,
        href: "/system/sources",
      }),
    ).toBeNull();
  });
});

describe("collapseDecisionRows", () => {
  it("does not collapse rows that carry a primary action, even when their copy is identical", () => {
    const first = attentionItem({
      id: "ad-1",
      kind: "incident",
      status: "open",
      title: "CPL выше базы",
      summary: "CPL $9.56 при базе $3.00.",
      target: { kind: "ad", id: "501", label: "ad-501" },
    });
    const second = attentionItem({
      id: "ad-2",
      kind: "incident",
      status: "open",
      title: "CPL выше базы",
      summary: "CPL $9.56 при базе $3.00.",
      target: { kind: "ad", id: "502", label: "ad-502" },
    });
    const rows = sortedRows(snapshotWith([first, second]));
    expect(rows).toHaveLength(2);
    expect(rows.every((row) => row.primaryAction?.kind === "pause")).toBe(true);

    const collapsed = collapseDecisionRows(rows, true);
    expect(collapsed).toHaveLength(2);
    expect(collapsed.map((entry) => entry.row.id)).toEqual(["ad-1", "ad-2"]);
    expect(collapsed.every((entry) => entry.count === 1)).toBe(true);
  });

  it("collapses identical rows that have no primary action (e.g. repeated source diagnostics)", () => {
    const diag = () =>
      attentionItem({
        id: `source:${Math.random()}`,
        kind: "source",
        severity: "warning",
        title: "Источник недоступен",
        summary: "Откройте диагностику источников.",
        target: { kind: "system", id: null, label: "Система" },
      });
    const rows = sortedRows(
      snapshotWith([
        { ...diag(), id: "source:a" },
        { ...diag(), id: "source:b" },
        { ...diag(), id: "source:c" },
      ]),
    );
    const collapsed = collapseDecisionRows(rows, true);
    expect(collapsed).toHaveLength(1);
    expect(collapsed[0]!.count).toBe(3);
  });

  it("preserves the sorted order of the collapsed result", () => {
    const critical = attentionItem({
      id: "critical",
      kind: "source",
      severity: "critical",
      title: "Критично",
      summary: "s1",
      target: { kind: "system", id: null, label: "Система" },
    });
    const dupA = attentionItem({
      id: "warn-a",
      kind: "source",
      severity: "warning",
      title: "Дубликат",
      summary: "s2",
      target: { kind: "system", id: null, label: "Система" },
    });
    const dupB = attentionItem({
      id: "warn-b",
      kind: "source",
      severity: "warning",
      title: "Дубликат",
      summary: "s2",
      target: { kind: "system", id: null, label: "Система" },
    });
    const rows = sortedRows(snapshotWith([dupB, critical, dupA]));
    const collapsed = collapseDecisionRows(rows, true);
    // At equal severity/moneyRank/occurred_at, compareDecisionRows breaks the
    // tie by id ascending — "warn-a" sorts before "warn-b" regardless of the
    // input array order above, so the surviving (first-occurrence) row is
    // "warn-a".
    expect(collapsed.map((entry) => entry.row.id)).toEqual([
      "critical",
      "warn-a",
    ]);
    expect(collapsed[1]!.count).toBe(2);
  });
});

describe("decisionRowAge", () => {
  const occurred_at = "2026-07-18T10:00:00Z";

  it("formats age in Russian buckets", () => {
    expect(decisionRowAge({ occurred_at }, "2026-07-18T10:00:20Z")).toBe(
      "меньше минуты",
    );
    expect(decisionRowAge({ occurred_at }, "2026-07-18T10:05:00Z")).toBe(
      "5 мин",
    );
    expect(decisionRowAge({ occurred_at }, "2026-07-18T13:00:00Z")).toBe(
      "3 ч",
    );
    expect(decisionRowAge({ occurred_at }, "2026-07-20T10:00:00Z")).toBe(
      "2 дн",
    );
  });

  it("never fabricates an age for an invalid timestamp", () => {
    expect(
      decisionRowAge({ occurred_at: "not-a-date" }, "2026-07-18T10:00:00Z"),
    ).toBe("—");
  });
});

describe("combineDecisionFeedState", () => {
  it("covers the full precedence table", () => {
    expect(combineDecisionFeedState([])).toBe("unavailable");
    expect(combineDecisionFeedState(["unavailable"])).toBe("unavailable");
    expect(combineDecisionFeedState(["unavailable", "unavailable"])).toBe(
      "unavailable",
    );
    expect(combineDecisionFeedState(["unavailable", "ready"])).toBe("partial");
    expect(combineDecisionFeedState(["stale", "ready"])).toBe("stale");
    expect(combineDecisionFeedState(["unavailable", "stale"])).toBe(
      "partial",
    ); // "часть" (unavailable) выигрывает у stale по порядку правил спеки.
    expect(combineDecisionFeedState(["partial", "ready"])).toBe("partial");
    expect(combineDecisionFeedState(["empty", "empty"])).toBe("empty");
    expect(combineDecisionFeedState(["empty", "ready"])).toBe("ready");
    expect(combineDecisionFeedState(["ready", "ready"])).toBe("ready");
  });
});
