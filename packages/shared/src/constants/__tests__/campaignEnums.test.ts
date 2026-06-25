import { describe, it, expect } from "vitest";
import {
  CAMPAIGN_OBJECTIVES,
  OPTIMIZATION_GOALS_BY_OBJECTIVE,
  PIXEL_EVENT_TYPES,
  CALL_TO_ACTIONS,
  OPTIMIZATION_GOAL_REQUIRES_EVENT,
  CAMPAIGN_GOAL_DEFAULTS,
  defaultOptimizationGoal,
} from "../campaignEnums";

const goalValues = (objective: string) =>
  (OPTIMIZATION_GOALS_BY_OBJECTIVE[objective] ?? []).map((o) => o.value);

describe("campaignEnums — матрица валидности FB", () => {
  // У каждой ODAX-цели есть хотя бы один optimization_goal (иначе дропдаун пустой).
  it("каждая objective имеет непустой список optimization_goal", () => {
    for (const obj of CAMPAIGN_OBJECTIVES) {
      expect(goalValues(obj.value).length).toBeGreaterThan(0);
    }
  });

  // Деньги: гемблинг-дефолт по SOP должен быть внутренне согласован.
  it("гемблинг-дефолт OUTCOME_SALES/OFFSITE_CONVERSIONS/PURCHASE согласован", () => {
    expect(CAMPAIGN_OBJECTIVES.map((o) => o.value)).toContain(CAMPAIGN_GOAL_DEFAULTS.objective);
    expect(goalValues(CAMPAIGN_GOAL_DEFAULTS.objective)).toContain(
      CAMPAIGN_GOAL_DEFAULTS.optimization_goal,
    );
    expect(PIXEL_EVENT_TYPES.map((e) => e.value)).toContain(CAMPAIGN_GOAL_DEFAULTS.custom_event_type);
    expect(CALL_TO_ACTIONS.map((c) => c.value)).toContain(CAMPAIGN_GOAL_DEFAULTS.cta);
  });

  // custom_event_type требуется только для OFFSITE_CONVERSIONS (promoted_object пикселя).
  it("событие пикселя завязано на OFFSITE_CONVERSIONS", () => {
    expect(OPTIMIZATION_GOAL_REQUIRES_EVENT).toBe("OFFSITE_CONVERSIONS");
  });

  // При смене objective дефолтный goal — валидный для НОВОЙ цели (первый из матрицы).
  it("defaultOptimizationGoal возвращает валидный goal для каждой цели", () => {
    for (const obj of CAMPAIGN_OBJECTIVES) {
      const def = defaultOptimizationGoal(obj.value);
      expect(goalValues(obj.value)).toContain(def);
    }
  });

  // Неизвестная objective → безопасный фолбэк, не пустая строка.
  it("неизвестная objective → фолбэк OFFSITE_CONVERSIONS", () => {
    expect(defaultOptimizationGoal("UNKNOWN_XYZ")).toBe("OFFSITE_CONVERSIONS");
  });

  // OUTCOME_SALES дефолтит ровно в OFFSITE_CONVERSIONS (первый в матрице).
  it("OUTCOME_SALES дефолтит в OFFSITE_CONVERSIONS", () => {
    expect(defaultOptimizationGoal("OUTCOME_SALES")).toBe("OFFSITE_CONVERSIONS");
  });
});
