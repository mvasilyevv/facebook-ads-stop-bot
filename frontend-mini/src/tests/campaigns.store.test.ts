/**
 * Тест wizardStore (Zustand): навигация по шагам, обновление конфига, пресеты.
 * Изолируем хранилище между тестами через reset().
 */
import { describe, it, expect, beforeEach } from "vitest";
import { useWizardStore } from "@/routes/campaigns/-wizardStore";
import type { CampaignPreset } from "@/lib/campaignTypes";

const MOCK_PRESET: CampaignPreset = {
  id: "preset-1",
  name: "Test Preset",
  act_id: "act_123",
  page_id: "page_456",
  pixel_id: "pixel_789",
  tz_offset: 3,
  offer_code: "GH_AVI",
  byer_tag: "MV",
  objective: "OUTCOME_SALES",
  optimization_goal: "OFFSITE_CONVERSIONS",
  custom_event_type: "PURCHASE",
  special_ad_categories: ["NONE"],
  cta: "PLAY_GAME",
  text_optimizations: "OPT_OUT",
  click_through_days: 1,
  view_through_days: 1,
  url_tags_template: null,
  naming_template: null,
  extra: {},
  created_at: "2026-06-22T00:00:00Z",
  updated_at: "2026-06-22T00:00:00Z",
};

describe("wizardStore навигация", () => {
  beforeEach(() => {
    useWizardStore.getState().reset();
  });

  it("начинает с шага start", () => {
    expect(useWizardStore.getState().step).toBe("start");
  });

  it("nextStep переходит к identity", () => {
    useWizardStore.getState().nextStep();
    expect(useWizardStore.getState().step).toBe("identity");
  });

  it("nextStep проходит все 7 шагов последовательно", () => {
    const steps = ["identity", "config", "structure", "creatives", "preview", "launch"];
    for (const expected of steps) {
      useWizardStore.getState().nextStep();
      expect(useWizardStore.getState().step).toBe(expected);
    }
  });

  it("nextStep не выходит за launch (последний шаг)", () => {
    // Перемотать до конца
    for (let i = 0; i < 10; i++) useWizardStore.getState().nextStep();
    expect(useWizardStore.getState().step).toBe("launch");
  });

  it("prevStep не уходит ниже start", () => {
    useWizardStore.getState().prevStep();
    expect(useWizardStore.getState().step).toBe("start");
  });

  it("prevStep возвращается назад с config → identity", () => {
    useWizardStore.getState().setStep("config");
    useWizardStore.getState().prevStep();
    expect(useWizardStore.getState().step).toBe("identity");
  });

  it("setStep прыгает на любой шаг напрямую", () => {
    useWizardStore.getState().setStep("preview");
    expect(useWizardStore.getState().step).toBe("preview");
  });
});

describe("wizardStore конфиг", () => {
  beforeEach(() => {
    useWizardStore.getState().reset();
  });

  it("updateConfig мержит поля без потери существующих", () => {
    useWizardStore.getState().updateConfig({ act_id: "act_111" });
    useWizardStore.getState().updateConfig({ offer_code: "GH_AVI" });
    const cfg = useWizardStore.getState().config;
    expect(cfg.act_id).toBe("act_111");
    expect(cfg.offer_code).toBe("GH_AVI");
  });

  it("дефолтный launch_state = campaign_paused (money-инвариант)", () => {
    const cfg = useWizardStore.getState().config;
    expect(cfg.launch_state).toBe("campaign_paused");
  });

  it("дефолтный budget_level = campaign (CBO)", () => {
    const cfg = useWizardStore.getState().config;
    expect(cfg.budget_level).toBe("campaign");
  });

  it("дефолтный age_min = 21, age_max = 65 (зашитый инвариант по SOP)", () => {
    const cfg = useWizardStore.getState().config;
    expect(cfg.age_min).toBe(21);
    expect(cfg.age_max).toBe(65);
  });

  it("дефолтный bid_strategy = COST_CAP (зашитый инвариант)", () => {
    const cfg = useWizardStore.getState().config;
    expect(cfg.bid_strategy).toBe("COST_CAP");
  });

  it("campaigns по умолчанию пустые", () => {
    expect(useWizardStore.getState().config.campaigns).toEqual([]);
  });
});

describe("wizardStore пресеты", () => {
  beforeEach(() => {
    useWizardStore.getState().reset();
  });

  it("setPreset заполняет идентификационные поля из пресета", () => {
    useWizardStore.getState().setPreset(MOCK_PRESET);
    const cfg = useWizardStore.getState().config;
    expect(cfg.act_id).toBe("act_123");
    expect(cfg.page_id).toBe("page_456");
    expect(cfg.pixel_id).toBe("pixel_789");
    expect(cfg.offer_code).toBe("GH_AVI");
    expect(cfg.byer_tag).toBe("MV");
  });

  it("setPreset(null) не затирает существующий конфиг", () => {
    useWizardStore.getState().updateConfig({ act_id: "act_999" });
    useWizardStore.getState().setPreset(null);
    expect(useWizardStore.getState().config.act_id).toBe("act_999");
  });

  it("setPreset сохраняет selectedPreset", () => {
    useWizardStore.getState().setPreset(MOCK_PRESET);
    expect(useWizardStore.getState().selectedPreset?.id).toBe("preset-1");
  });
});

describe("wizardStore upload и reset", () => {
  beforeEach(() => {
    useWizardStore.getState().reset();
  });

  it("setUpload сохраняет uploadId и концепты", () => {
    const concepts = [
      { ref: "img1.jpg", original_name: "img1.jpg", size_bytes: 1024, content_type: "image/jpeg" },
    ];
    useWizardStore.getState().setUpload("upload-abc", concepts);
    expect(useWizardStore.getState().uploadId).toBe("upload-abc");
    expect(useWizardStore.getState().concepts).toHaveLength(1);
  });

  it("reset возвращает всё к исходному состоянию", () => {
    useWizardStore.getState().setPreset(MOCK_PRESET);
    useWizardStore.getState().updateConfig({ destination_link: "https://example.com" });
    useWizardStore.getState().setStep("config");
    useWizardStore.getState().setRunId("run-123");

    useWizardStore.getState().reset();

    expect(useWizardStore.getState().step).toBe("start");
    expect(useWizardStore.getState().selectedPreset).toBeNull();
    expect(useWizardStore.getState().runId).toBeNull();
    expect(useWizardStore.getState().config.destination_link).toBeUndefined();
  });
});
