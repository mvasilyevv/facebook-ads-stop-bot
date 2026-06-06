import { describe, it, expect } from "vitest";
import { buildDraftDiff } from "../diff";

describe("buildDraftDiff — set_adset_budget", () => {
  // Бюджет в центах: changed=true когда значения различаются
  it("daily budget cents изменился → changed=true", () => {
    const rows = buildDraftDiff(
      "set_adset_budget",
      { budget_cents: 5000, budget_type: "daily" },
      { daily_budget_cents: 3000 },
    );
    expect(rows).toHaveLength(1);
    const row = rows[0]!;
    expect(row.field).toBe("Суточный бюджет");
    expect(row.current).toBe("$30.00");
    expect(row.target).toBe("$50.00");
    expect(row.changed).toBe(true);
  });

  // Бюджет не изменился → changed=false
  it("бюджет совпадает → changed=false", () => {
    const rows = buildDraftDiff(
      "set_adset_budget",
      { budget_cents: 5000, budget_type: "daily" },
      { daily_budget_cents: 5000 },
    );
    expect(rows[0]!.changed).toBe(false);
  });

  // Текущий бюджет неизвестен → current = "—"
  it("нет currentState → current = —", () => {
    const rows = buildDraftDiff("set_adset_budget", { budget_cents: 10000, budget_type: "daily" });
    expect(rows[0]!.current).toBe("—");
    expect(rows[0]!.changed).toBe(true);
  });

  // Lifetime budget
  it("lifetime budget", () => {
    const rows = buildDraftDiff(
      "set_adset_budget",
      { budget_cents: 200000, budget_type: "lifetime" },
      { lifetime_budget_cents: 100000 },
    );
    const row = rows[0]!;
    expect(row.field).toBe("Бюджет (lifetime)");
    expect(row.target).toBe("$2000.00");
    expect(row.current).toBe("$1000.00");
  });
});

describe("buildDraftDiff — pause_ad", () => {
  // Пауза активного объявления
  it("ACTIVE → PAUSED, changed=true", () => {
    const rows = buildDraftDiff(
      "pause_ad",
      { fb_ad_id: "120211" },
      { status: "ACTIVE" },
    );
    const row = rows[0]!;
    expect(row.field).toBe("Статус объявления");
    expect(row.current).toBe("ACTIVE");
    expect(row.target).toBe("PAUSED");
    expect(row.changed).toBe(true);
  });

  // Уже на паузе → changed=false
  it("уже PAUSED → changed=false", () => {
    const rows = buildDraftDiff("pause_ad", { fb_ad_id: "120211" }, { status: "PAUSED" });
    expect(rows[0]!.changed).toBe(false);
  });

  // ID отображается как дополнительная строка
  it("fb_ad_id в payload → дополнительная строка", () => {
    const rows = buildDraftDiff("pause_ad", { fb_ad_id: "120211" }, { status: "ACTIVE" });
    expect(rows).toHaveLength(2);
    expect(rows[1]!.field).toBe("Ad ID");
    expect(rows[1]!.changed).toBe(false);
  });
});

describe("buildDraftDiff — activate_ad", () => {
  // Активация
  it("PAUSED → ACTIVE, changed=true", () => {
    const rows = buildDraftDiff("activate_ad", {}, { status: "PAUSED" });
    expect(rows[0]!.target).toBe("ACTIVE");
    expect(rows[0]!.changed).toBe(true);
  });
});

describe("buildDraftDiff — bulk_status_change", () => {
  // Массовая пауза
  it("bulk pause 5 объектов", () => {
    const rows = buildDraftDiff("bulk_status_change", {
      action: "pause",
      object_type: "ad",
      object_ids: ["1", "2", "3", "4", "5"],
    });
    const countRow = rows.find((r) => r.field === "Количество объектов");
    const statusRow = rows.find((r) => r.field === "Целевой статус");
    // Используем ! так как find() может вернуть undefined (но мы знаем что строки есть)
    expect(countRow!.target).toBe("5");
    expect(statusRow!.target).toBe("PAUSED");
  });

  // Пустой список → changed=false для count
  it("пустой список объектов → changed=false", () => {
    const rows = buildDraftDiff("bulk_status_change", { action: "pause", object_ids: [] });
    const countRow = rows.find((r) => r.field === "Количество объектов");
    expect(countRow!.changed).toBe(false);
  });

  // activate
  it("bulk activate → ACTIVE", () => {
    const rows = buildDraftDiff("bulk_status_change", {
      action: "activate",
      object_ids: ["1"],
    });
    const statusRow = rows.find((r) => r.field === "Целевой статус");
    expect(statusRow!.target).toBe("ACTIVE");
  });
});

describe("buildDraftDiff — pause_campaign / activate_campaign", () => {
  // Пауза кампании
  it("pause_campaign → PAUSED", () => {
    const rows = buildDraftDiff("pause_campaign", {}, { status: "ACTIVE" });
    expect(rows[0]!.target).toBe("PAUSED");
    expect(rows[0]!.changed).toBe(true);
  });

  // Активация кампании
  it("activate_campaign → ACTIVE", () => {
    const rows = buildDraftDiff("activate_campaign", {}, { status: "PAUSED" });
    expect(rows[0]!.target).toBe("ACTIVE");
    expect(rows[0]!.changed).toBe(true);
  });
});

describe("buildDraftDiff — duplicate_campaign", () => {
  // Дублирование: показывает новое имя
  it("показывает новое имя кампании", () => {
    const rows = buildDraftDiff("duplicate_campaign", { campaign_name: "CR2 | Copy | 07.06" });
    expect(rows[0]!.field).toBe("Новое имя кампании");
    expect(rows[0]!.target).toBe("CR2 | Copy | 07.06");
    expect(rows[0]!.changed).toBe(true);
  });

  // Без имени — показывает placeholder
  it("без имени — placeholder", () => {
    const rows = buildDraftDiff("duplicate_campaign", {});
    expect(rows[0]!.target).toContain("будет назначено");
  });
});

describe("buildDraftDiff — set_adset_budget (dollars формат)", () => {
  // Бюджет в долларах (daily_budget float)
  it("daily_budget float без currentState", () => {
    const rows = buildDraftDiff("set_adset_budget", { daily_budget: 50.0 });
    expect(rows[0]!.field).toBe("Суточный бюджет");
    expect(rows[0]!.target).toBe("$50.00");
  });

  // Lifetime в долларах
  it("lifetime_budget float", () => {
    const rows = buildDraftDiff(
      "set_adset_budget",
      { lifetime_budget: 1000 },
      { lifetime_budget: 500 },
    );
    expect(rows[0]!.field).toBe("Бюджет (lifetime)");
    expect(rows[0]!.target).toBe("$1000.00");
    expect(rows[0]!.changed).toBe(true);
  });
});

describe("buildDraftDiff — неизвестный mutation_kind", () => {
  // Fallback: сырой payload как JSON
  it("неизвестный kind → raw JSON", () => {
    const rows = buildDraftDiff("unknown_kind", { foo: "bar" });
    expect(rows).toHaveLength(1);
    const row = rows[0]!;
    expect(row.field).toBe("Параметры");
    expect(row.changed).toBe(true);
    expect(row.target).toContain("foo");
  });

  // null payload — не бросает
  it("null payload — не бросает", () => {
    expect(() => buildDraftDiff("pause_ad", null)).not.toThrow();
  });
});
