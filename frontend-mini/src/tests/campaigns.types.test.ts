/**
 * Тест типов и констант кампаний.
 * Проверяем правильность WIZARD_STEPS, статус-лейблов, TERMINAL_STATUSES.
 */
import { describe, it, expect } from "vitest";
import {
  WIZARD_STEPS,
  WIZARD_STEP_LABEL,
  RUN_STATUS_LABEL,
  TERMINAL_STATUSES,
} from "@/lib/campaignTypes";
import type { WizardStep, CampaignRunStatus } from "@/lib/campaignTypes";

describe("WIZARD_STEPS", () => {
  // Визард содержит 7 шагов в правильном порядке
  it("содержит ровно 7 шагов", () => {
    expect(WIZARD_STEPS).toHaveLength(7);
  });

  it("начинается с start и заканчивается launch", () => {
    expect(WIZARD_STEPS[0]).toBe("start");
    expect(WIZARD_STEPS[WIZARD_STEPS.length - 1]).toBe("launch");
  });

  it("содержит все обязательные шаги по порядку", () => {
    const expected: WizardStep[] = [
      "start", "identity", "config", "structure", "creatives", "preview", "launch",
    ];
    expect(WIZARD_STEPS).toEqual(expected);
  });
});

describe("WIZARD_STEP_LABEL", () => {
  // У каждого шага есть лейбл
  it("покрывает все шаги визарда", () => {
    for (const step of WIZARD_STEPS) {
      expect(WIZARD_STEP_LABEL[step]).toBeTruthy();
    }
  });
});

describe("RUN_STATUS_LABEL", () => {
  // Все статусы проклеены лейблами на русском
  const allStatuses: CampaignRunStatus[] = [
    "queued", "uniquifying", "uploading", "creating", "succeeded", "failed", "cancelled",
  ];

  it("покрывает все статусы воркера", () => {
    for (const s of allStatuses) {
      expect(RUN_STATUS_LABEL[s]).toBeTruthy();
    }
  });

  it("succeeded → Готово", () => {
    expect(RUN_STATUS_LABEL.succeeded).toBe("Готово");
  });

  it("failed → Ошибка", () => {
    expect(RUN_STATUS_LABEL.failed).toBe("Ошибка");
  });
});

describe("TERMINAL_STATUSES", () => {
  // Финальные статусы — поллинг можно остановить
  it("содержит succeeded, failed, cancelled", () => {
    expect(TERMINAL_STATUSES.has("succeeded")).toBe(true);
    expect(TERMINAL_STATUSES.has("failed")).toBe(true);
    expect(TERMINAL_STATUSES.has("cancelled")).toBe(true);
  });

  it("не содержит промежуточные статусы", () => {
    expect(TERMINAL_STATUSES.has("queued")).toBe(false);
    expect(TERMINAL_STATUSES.has("uniquifying")).toBe(false);
    expect(TERMINAL_STATUSES.has("uploading")).toBe(false);
    expect(TERMINAL_STATUSES.has("creating")).toBe(false);
  });
});
