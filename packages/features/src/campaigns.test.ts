import { describe, expect, it } from "vitest";

import type { components } from "@fb/shared/api/generated";

import {
  aggregateCampaignLaunchState,
  applyCampaignPreset,
  buildCampaignConfig,
  campaignPresetIsIncomplete,
  campaignPresetPayload,
  campaignLaunchUnits,
  campaignPresetsDataState,
  campaignWizardFromDraft,
  campaignWizardReducer,
  campaignWizardToDraft,
  createCampaignWizardState,
  createCampaignPresetDraft,
  nextCampaignKey,
  parseCampaignCountryInput,
  validateCampaignPresetDraft,
  validateCampaignStep,
  type CampaignWizardConcept,
  type CampaignPreset,
  type CampaignWizardState,
} from "./campaigns";

describe("campaignLaunchUnits", () => {
  type LaunchAccount = components["schemas"]["LaunchAccountOut"];

  const account = (overrides: Partial<LaunchAccount>): LaunchAccount => ({
    account_id: "123",
    status: "queued",
    replayed: false,
    ...overrides,
  });

  it("разворачивает кампании кабинета в отдельные единицы залива", () => {
    const units = campaignLaunchUnits([
      account({
        campaigns: [
          {
            campaign_key: "camp1",
            run_id: "run-1",
            status: "queued",
            replayed: false,
          },
          {
            campaign_key: "camp2",
            status: "rejected",
            error: "Концепт кампании не найден",
            replayed: false,
          },
        ],
      }),
    ]);

    expect(units).toEqual([
      {
        accountId: "123",
        campaignKey: "camp1",
        runId: "run-1",
        status: "queued",
        error: null,
      },
      {
        accountId: "123",
        campaignKey: "camp2",
        runId: null,
        status: "rejected",
        error: "Концепт кампании не найден",
      },
    ]);
  });

  // Кабинет, отвергнутый до разбора плана, кампаний не имеет — но исчезнуть из
  // знаменателя он не должен, иначе запрос без единой постановки выглядел бы
  // принятым.
  it("кабинет без кампаний остаётся одной единицей без ключа кампании", () => {
    expect(
      campaignLaunchUnits([account({ status: "rejected", error: "Кабинет не привязан к офферу" })]),
    ).toEqual([
      {
        accountId: "123",
        campaignKey: null,
        runId: null,
        status: "rejected",
        error: "Кабинет не привязан к офферу",
      },
    ]);
  });

  it("пустой receipt не даёт ни одной единицы", () => {
    expect(campaignLaunchUnits(null)).toEqual([]);
    expect(campaignLaunchUnits(undefined)).toEqual([]);
    expect(campaignLaunchUnits([])).toEqual([]);
  });

  it("сохраняет порядок кабинетов и кампаний внутри них", () => {
    const units = campaignLaunchUnits([
      account({
        account_id: "111",
        campaigns: [
          {
            campaign_key: "camp1",
            run_id: "run-1",
            status: "queued",
            replayed: false,
          },
        ],
      }),
      account({
        account_id: "222",
        campaigns: [
          {
            campaign_key: "camp1",
            run_id: "run-2",
            status: "queued",
            replayed: false,
          },
          {
            campaign_key: "camp2",
            run_id: "run-3",
            status: "queued",
            replayed: false,
          },
        ],
      }),
    ]);

    expect(units.map((unit) => [unit.accountId, unit.campaignKey])).toEqual([
      ["111", "camp1"],
      ["222", "camp1"],
      ["222", "camp2"],
    ]);
  });
});

describe("aggregateCampaignLaunchState", () => {
  it("не показывает общий успех при частичном результате", () => {
    expect(aggregateCampaignLaunchState(["succeeded", "failed"])).toBe("partial");
  });

  it("UNKNOWN остаётся отдельным состоянием и не становится retry/success", () => {
    expect(aggregateCampaignLaunchState(["unknown", "failed"])).toBe("unknown");
  });

  it("зелёный итог возможен только когда успешны все кабинеты", () => {
    expect(aggregateCampaignLaunchState(["succeeded", "succeeded"])).toBe("succeeded");
  });
});

function readyState(): CampaignWizardState {
  let state = createCampaignWizardState();
  state = campaignWizardReducer(state, {
    type: "patchIdentity",
    value: {
      act_id: "123",
      ad_account_ids: ["123"],
      page_id: "456",
      pixel_id: "789",
      account_context_state: "ready",
      timezone_name: "Europe/Kaliningrad",
      currency: "USD",
      currency_exponent: 2,
      account_context_observed_at: "2026-08-09T10:00:00Z",
      offer_code: "GH_CR2",
    },
  });
  state = campaignWizardReducer(state, {
    type: "patchGoal",
    value: {
      destination_link: "https://example.com/click",
      daily_budget: "200.00",
      bid_amount: "5.00",
      countries: ["BR"],
      start_date: "2099-08-10",
    },
  });
  state = campaignWizardReducer(state, {
    type: "setCampaigns",
    campaigns: [{ key: "static", label: null, adset_count: 2 }],
  });
  const concept: CampaignWizardConcept = {
    ref: "creative.jpg",
    original_name: "creative.jpg",
    size_bytes: 1024,
    content_type: "image/jpeg",
    campaign_keys: ["static"],
  };
  return campaignWizardReducer(state, {
    type: "patchCreatives",
    value: { upload_id: "upload-id", concepts: [concept] },
  });
}

describe("campaign feature model", () => {
  it("builds one all-paused-compatible config from explicit concept assignments", () => {
    const config = buildCampaignConfig(readyState());
    expect(config.campaigns).toEqual([
      {
        key: "static",
        label: null,
        adset_count: 2,
        concept_refs: ["creative.jpg"],
      },
    ]);
    expect(config.daily_budget).toBe("200.00");
    expect(config).not.toHaveProperty("currency");
    expect(config).not.toHaveProperty("account_context_observed_at");
  });

  it("fails closed for non-ready or non-USD account evidence", () => {
    const state = readyState();
    state.identity = {
      ...state.identity,
      currency: "",
      account_context_state: "stale",
    };
    expect(validateCampaignStep(state, 2)).toHaveProperty("account_context_state");
    expect(() => buildCampaignConfig(state)).toThrow("USD-контекст");
  });

  it("keeps server draft free of preview, run and secret state", () => {
    const draft = campaignWizardToDraft(readyState());
    expect(draft).not.toHaveProperty("preview");
    expect(draft).not.toHaveProperty("runId");
    expect(JSON.stringify(draft)).not.toMatch(/secret|token|task_id/);
    expect(campaignWizardFromDraft(draft)).toEqual(readyState());
  });

  it("hydrates drafts saved before targeting preset fields existed", () => {
    const draft = campaignWizardToDraft(readyState());
    const legacyGoal = draft.goal as unknown as Record<string, unknown>;
    delete legacyGoal.genders;
    delete legacyGoal.placements;
    delete legacyGoal.naming_template;
    delete legacyGoal.url_tags_template;

    expect(campaignWizardFromDraft(draft).goal).toMatchObject({
      genders: [],
      placements: [],
      naming_template: "",
      url_tags_template: "",
    });
  });

  it("validates every transition through one shared step policy", () => {
    const empty = createCampaignWizardState();
    expect(validateCampaignStep(empty, 2)).toMatchObject({
      act_id: expect.any(String),
      account_context_state: expect.any(String),
    });
    expect(validateCampaignStep(readyState(), 2)).toEqual({});
    expect(validateCampaignStep(readyState(), 3)).toEqual({});
    expect(validateCampaignStep(readyState(), 4)).toEqual({});
    expect(validateCampaignStep(readyState(), 5)).toEqual({});
  });

  it("generates the first free stable campaign key after deletions", () => {
    expect(
      nextCampaignKey([
        { key: "camp1", adset_count: 1 },
        { key: "camp3", adset_count: 1 },
      ]),
    ).toBe("camp2");
  });

  it("normalizes and deduplicates ISO-2 country input without hiding invalid tokens", () => {
    expect(parseCampaignCountryInput("us, CA; us bad")).toEqual({
      countries: ["US", "CA"],
      invalid: ["BAD"],
    });
  });

  it("copies a preset into editable goal fields without replacing run identity", () => {
    const state = readyState();
    const preset: CampaignPreset = {
      id: "preset-1",
      name: "GH scale",
      countries: ["GH"],
      age_min: 25,
      age_max: 54,
      genders: ["female"],
      placements: ["facebook", "instagram"],
      custom_event_type: "PURCHASE",
      budget_level: "adset",
      daily_budget: "350.00",
      bid_strategy: "COST_CAP",
      bid_amount: "5.00",
      display_link: "play.ghana.com",
      naming_template: "{byer} | {offer} | SCALE | {date}",
      url_tags_template: "sub2={byer}",
      created_at: "2026-08-15T10:00:00Z",
      updated_at: "2026-08-15T10:00:00Z",
    };

    const applied = applyCampaignPreset(state, preset);
    applied.goal.daily_budget = "400.00";

    expect(applied.identity).toEqual(state.identity);
    expect(applied.goal).toMatchObject({
      countries: ["GH"],
      genders: ["female"],
      placements: ["facebook", "instagram"],
      budget_level: "adset",
      daily_budget: "400.00",
      custom_event_type: "PURCHASE",
      bid_strategy: "COST_CAP",
      bid_amount: "5.00",
      display_link: "play.ghana.com",
    });
    expect(buildCampaignConfig(applied)).toMatchObject({
      daily_budget: "400.00",
      genders: ["female"],
      placements: ["facebook", "instagram"],
      naming_template: preset.naming_template,
      url_tags: preset.url_tags_template,
      bid_strategy: "COST_CAP",
      bid_amount: "5.00",
      display_link: "play.ghana.com",
    });
  });

  it("не затирает уже введённые ставку и отображаемую ссылку пустым значением пресета", () => {
    let state = readyState();
    state = campaignWizardReducer(state, {
      type: "patchGoal",
      value: { bid_amount: "9.00", display_link: "existing.example.com" },
    });
    const preset: CampaignPreset = {
      id: "preset-2",
      name: "GH broad",
      countries: ["GH"],
      age_min: 21,
      age_max: 65,
      genders: [],
      placements: [],
      custom_event_type: "PURCHASE",
      budget_level: "campaign",
      daily_budget: "150.00",
      bid_strategy: "LOWEST_COST_WITHOUT_CAP",
      bid_amount: "",
      display_link: "",
      naming_template: null,
      url_tags_template: null,
      created_at: "2026-08-15T10:00:00Z",
      updated_at: "2026-08-15T10:00:00Z",
    };

    const applied = applyCampaignPreset(state, preset);

    expect(applied.goal.bid_strategy).toBe("LOWEST_COST_WITHOUT_CAP");
    expect(applied.goal.bid_amount).toBe("9.00");
    expect(applied.goal.display_link).toBe("existing.example.com");
  });

  it("builds a create payload from current wizard values", () => {
    const draft = createCampaignPresetDraft(readyState());
    draft.name = " Scale ";

    expect(campaignPresetPayload(draft)).toMatchObject({
      name: "Scale",
      countries: ["BR"],
      custom_event_type: "PURCHASE",
      daily_budget: "200.00",
    });
  });

  it("rejects invalid preset money before calling the API", () => {
    const draft = createCampaignPresetDraft(readyState());
    draft.name = "Scale";
    draft.daily_budget = "100000.01";

    expect(validateCampaignPresetDraft(draft).daily_budget).toMatch(/Максимум/);
  });

  it("requires bid_amount only for bid strategies that need a cap", () => {
    const draft = createCampaignPresetDraft(readyState());
    draft.name = "Scale";
    draft.bid_strategy = "COST_CAP";
    draft.bid_amount = "";
    expect(validateCampaignPresetDraft(draft).bid_amount).toBeTruthy();

    draft.bid_strategy = "LOWEST_COST_WITHOUT_CAP";
    expect(validateCampaignPresetDraft(draft).bid_amount).toBeUndefined();
  });

  it("помечает пресет COST_CAP без ставки неполным, а не только по бюджету и гео", () => {
    const base = {
      daily_budget: "200.00",
      countries: ["BR"],
      bid_strategy: "COST_CAP" as const,
      bid_amount: "",
    };
    expect(campaignPresetIsIncomplete(base)).toBe(true);
    expect(campaignPresetIsIncomplete({ ...base, bid_amount: "5.00" })).toBe(false);
    expect(
      campaignPresetIsIncomplete({ ...base, bid_strategy: "LOWEST_COST_WITHOUT_CAP" }),
    ).toBe(false);
    expect(campaignPresetIsIncomplete({ ...base, bid_amount: "5.00", daily_budget: null })).toBe(
      true,
    );
    expect(
      campaignPresetIsIncomplete({ ...base, bid_amount: "5.00", countries: [] }),
    ).toBe(true);
  });

  it("keeps empty and unavailable preset collections distinct", () => {
    expect(campaignPresetsDataState({ isPending: false, isError: false, count: 0 })).toBe("empty");
    expect(campaignPresetsDataState({ isPending: false, isError: true, count: 0 })).toBe(
      "unavailable",
    );
  });
});
