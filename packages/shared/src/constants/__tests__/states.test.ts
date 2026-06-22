import { describe, it, expect } from "vitest";
import {
  ALERT_STATES,
  ALERT_STATE_LABELS,
  TASK_STATUSES,
  TASK_STATUS_LABELS,
  displayAdState,
  normalizeAlertState,
  normalizeTaskStatus,
  type AlertState,
  type TaskStatus,
} from "../states";

describe("displayAdState (FSM + delivery_status)", () => {
  // Норма + объявление НЕ крутится в FB (OFF) → «Выключено», а не ложная «Норма».
  it("normal + delivery OFF → Выключено (state disabled)", () => {
    expect(displayAdState("normal", "OFF")).toEqual({ label: "Выключено", state: "disabled" });
  });
  // Норма + объявление активно → обычная Норма.
  it("normal + delivery ACTIVE → Норма", () => {
    expect(displayAdState("normal", "ACTIVE")).toEqual({ label: "Норма", state: "normal" });
  });
  // delivery неизвестен (null) → не переопределяем, оставляем FSM-лейбл.
  it("normal + delivery null → Норма (без переопределения)", () => {
    expect(displayAdState("normal", null)).toEqual({ label: "Норма", state: "normal" });
  });
  // Инцидентное FSM-состояние — вердикт бота, не переопределяется доставкой.
  it("stop_sent + delivery OFF → Стоп (FSM приоритетнее)", () => {
    expect(displayAdState("stop_sent", "OFF")).toEqual({ label: "Стоп", state: "stop_sent" });
  });
  it("warning_sent + delivery ACTIVE → Предупреждение", () => {
    expect(displayAdState("warning_sent", "ACTIVE")).toEqual({
      label: "Предупреждение",
      state: "warning_sent",
    });
  });
  // PAUSED тоже считается «не крутится» при норме.
  it("normal + delivery PAUSED → Выключено", () => {
    expect(displayAdState("normal", "PAUSED")).toEqual({ label: "Выключено", state: "disabled" });
  });
});

describe("normalizeAlertState", () => {
  // Канонические lowercase значения проходят без изменений
  it.each(["normal", "warning_sent", "stop_sent", "claimed", "disabled"] as AlertState[])(
    "canonical %s → без изменений",
    (state) => {
      expect(normalizeAlertState(state)).toBe(state);
    },
  );

  // TMA-API отдаёт UPPERCASE — поглощаем
  it("UPPERCASE NORMAL → normal", () => {
    expect(normalizeAlertState("NORMAL")).toBe("normal");
  });
  it("UPPERCASE WARNING_SENT → warning_sent", () => {
    expect(normalizeAlertState("WARNING_SENT")).toBe("warning_sent");
  });
  it("UPPERCASE STOP_SENT → stop_sent", () => {
    expect(normalizeAlertState("STOP_SENT")).toBe("stop_sent");
  });
  it("UPPERCASE CLAIMED → claimed", () => {
    expect(normalizeAlertState("CLAIMED")).toBe("claimed");
  });
  it("UPPERCASE DISABLED → disabled", () => {
    expect(normalizeAlertState("DISABLED")).toBe("disabled");
  });

  // Неизвестное → fallback normal + console.warn (не бросаем)
  it("неизвестное → normal (не бросает)", () => {
    expect(normalizeAlertState("ARCHIVED")).toBe("normal");
  });
  it("пустая строка → normal", () => {
    expect(normalizeAlertState("")).toBe("normal");
  });
  it("null → normal", () => {
    expect(normalizeAlertState(null)).toBe("normal");
  });
  it("undefined → normal", () => {
    expect(normalizeAlertState(undefined)).toBe("normal");
  });
});

describe("normalizeTaskStatus", () => {
  // UPPERCASE проходят как есть
  it.each(["PENDING", "RUNNING", "SUCCEEDED", "FAILED", "RETRYING", "CANCELLED"] as TaskStatus[])(
    "UPPERCASE %s → без изменений",
    (status) => {
      expect(normalizeTaskStatus(status)).toBe(status);
    },
  );

  // lowercase из прямых SQL-запросов
  it("lowercase succeeded → SUCCEEDED", () => {
    expect(normalizeTaskStatus("succeeded")).toBe("SUCCEEDED");
  });
  it("lowercase failed → FAILED", () => {
    expect(normalizeTaskStatus("failed")).toBe("FAILED");
  });

  // draft → PENDING (внутренний статус БД)
  it("draft → PENDING", () => {
    expect(normalizeTaskStatus("draft")).toBe("PENDING");
  });
  it("DRAFT → PENDING", () => {
    expect(normalizeTaskStatus("DRAFT")).toBe("PENDING");
  });

  // null → PENDING
  it("null → PENDING", () => {
    expect(normalizeTaskStatus(null)).toBe("PENDING");
  });
});

describe("Parity: каждый AlertState имеет лейбл", () => {
  // Parity-тест: все состояния покрыты лейблами, нет осиротевших
  it("ALERT_STATES и ALERT_STATE_LABELS покрывают друг друга", () => {
    const labelKeys = Object.keys(ALERT_STATE_LABELS) as AlertState[];
    // Каждый state имеет лейбл
    for (const state of ALERT_STATES) {
      expect(ALERT_STATE_LABELS[state], `Нет лейбла для state="${state}"`).toBeDefined();
    }
    // Нет лишних лейблов (осиротевших)
    for (const key of labelKeys) {
      expect(ALERT_STATES.includes(key), `Лишний лейбл: "${key}"`).toBe(true);
    }
  });

  it("TASK_STATUSES и TASK_STATUS_LABELS покрывают друг друга", () => {
    const labelKeys = Object.keys(TASK_STATUS_LABELS) as TaskStatus[];
    for (const status of TASK_STATUSES) {
      expect(TASK_STATUS_LABELS[status], `Нет лейбла для status="${status}"`).toBeDefined();
    }
    for (const key of labelKeys) {
      expect(TASK_STATUSES.includes(key), `Лишний лейбл: "${key}"`).toBe(true);
    }
  });
});

describe("Расхождение мини-апп vs канона", () => {
  // CLAIMED должен быть "В работе", не "Ожидает OFF" (баг мини-апп)
  it('claimed → "В работе" (не "Ожидает OFF")', () => {
    expect(ALERT_STATE_LABELS["claimed"]).toBe("В работе");
  });

  // ARCHIVED не должен существовать в канонических состояниях
  it("нет ARCHIVED в ALERT_STATES", () => {
    expect(ALERT_STATES).not.toContain("archived");
    expect(ALERT_STATES).not.toContain("ARCHIVED");
  });
});
