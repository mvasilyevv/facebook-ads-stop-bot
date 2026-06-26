/**
 * Тесты buildConfig визарда создания кампаний.
 *
 * Покрываем:
 *   - concept_refs заполняются из назначенных концептов (CRIT money-fix)
 *   - url_tags не попадает в конфиг (HIGH mislabel-fix)
 *   - пустой campaign_keys = концепт не распределён (ни в одну кампанию)
 *   - концепт с campaign_keys только в конкретной кампании
 *   - смешанные медиа (фото+видео) без kind-фильтра
 */
import { describe, it, expect, beforeEach } from "vitest";
import { useWizardStore } from "@/stores/campaignWizard";
import type { UploadedConcept } from "@/stores/campaignWizard";

const TOMORROW = (() => {
  const d = new Date();
  d.setDate(d.getDate() + 1);
  return d.toISOString().slice(0, 10);
})();

/** Заполняет минимальный валидный стор для buildConfig. */
function seedStore(concepts: UploadedConcept[], campaigns: { key: string }[]) {
  const store = useWizardStore.getState();
  store.setIdentity({
    act_id: "act_123",
    page_id: "page_456",
    pixel_id: "pixel_789",
    tz_offset: 0,
    offer_code: "GH_AVI",
    byer_tag: "MV",
  });
  store.setGoal({
    objective: "OUTCOME_SALES",
    optimization_goal: "OFFSITE_CONVERSIONS",
    custom_event_type: "PURCHASE",
    destination_link: "https://trk.example.com/click",
    cta: "PLAY_GAME",
    text_optimizations: "OPT_OUT",
    start_date: TOMORROW,
    budget_level: "campaign",
    daily_budget_cents: 20000,
    bid_amount_cents: 500,
    bid_strategy: "COST_CAP",
    countries: ["GH"],
    age_min: 18,
    age_max: 65,
    advantage_audience: true,
    click_through_days: 1,
    view_through_days: 1,
    ad_text_mode: "none",
    ad_text_primary: "",
  });
  store.setStructure({
    campaigns: campaigns.map((c) => ({ ...c, adset_count: 3, concept_refs: [] })),
  });
  store.setCreatives({ upload_id: "upload-abc", concepts, copies_per_concept: null });
  store.setPreview({ launch_state: "campaign_paused", plan: null });
}

describe("buildConfig — concept_refs из назначения", () => {
  beforeEach(() => {
    useWizardStore.getState().reset();
  });

  it("кампания получает смешанные концепты (фото+видео) без kind-фильтра", () => {
    // Кампания получает и фото, и видео — kind-фильтр убран, медиа смешанные.
    const concepts: UploadedConcept[] = [
      { ref: "img.jpg", original_name: "img.jpg", size_bytes: 1, content_type: "image/jpeg", campaign_keys: ["c1"] },
      { ref: "clip.mp4", original_name: "clip.mp4", size_bytes: 1, content_type: "video/mp4", campaign_keys: ["c1"] },
    ];
    seedStore(concepts, [{ key: "c1" }]);
    const config = useWizardStore.getState().buildConfig();
    expect(config.campaigns[0]!.concept_refs).toEqual(["img.jpg", "clip.mp4"]);
  });

  it("концепт без назначения (пустой campaign_keys) → ни в одну кампанию", () => {
    // Пустой campaign_keys = концепт лежит в пуле «не распределены», не идёт никуда.
    const concepts: UploadedConcept[] = [
      { ref: "img1.jpg", original_name: "img1.jpg", size_bytes: 1024, content_type: "image/jpeg", campaign_keys: [] },
    ];
    seedStore(concepts, [
      { key: "camp1" },
      { key: "camp2" },
    ]);

    const config = useWizardStore.getState().buildConfig();

    expect(config.campaigns).toHaveLength(2);
    expect(config.campaigns[0]!.concept_refs).toEqual([]);
    expect(config.campaigns[1]!.concept_refs).toEqual([]);
  });

  it("концепт с обоими ключами → попадает в обе кампании", () => {
    // Явное назначение в обе кампании (и фото, и видео — без фильтра по типу).
    const concepts: UploadedConcept[] = [
      { ref: "img1.jpg", original_name: "img1.jpg", size_bytes: 1024, content_type: "image/jpeg", campaign_keys: ["camp1", "camp2"] },
      { ref: "vid1.mp4", original_name: "vid1.mp4", size_bytes: 2048, content_type: "video/mp4", campaign_keys: ["camp1", "camp2"] },
    ];
    seedStore(concepts, [
      { key: "camp1" },
      { key: "camp2" },
    ]);

    const config = useWizardStore.getState().buildConfig();

    expect(config.campaigns[0]!.concept_refs).toEqual(["img1.jpg", "vid1.mp4"]);
    expect(config.campaigns[1]!.concept_refs).toEqual(["img1.jpg", "vid1.mp4"]);
  });

  it("концепт с campaign_keys=['camp1'] → попадает только в camp1, не в camp2", () => {
    // Привязка по ключу работает независимо от типа медиа.
    const concepts: UploadedConcept[] = [
      { ref: "img1.jpg", original_name: "img1.jpg", size_bytes: 1024, content_type: "image/jpeg", campaign_keys: ["camp1"] },
      { ref: "vid1.mp4", original_name: "vid1.mp4", size_bytes: 2048, content_type: "video/mp4", campaign_keys: ["camp2"] },
    ];
    seedStore(concepts, [
      { key: "camp1" },
      { key: "camp2" },
    ]);

    const config = useWizardStore.getState().buildConfig();

    const c1 = config.campaigns.find((c) => c.key === "camp1");
    const c2 = config.campaigns.find((c) => c.key === "camp2");
    expect(c1?.concept_refs).toEqual(["img1.jpg"]);
    expect(c2?.concept_refs).toEqual(["vid1.mp4"]);
  });

  it("смесь: концепт в обеих кампаниях + концепт только в одной", () => {
    // shared привязан к обеим; only_c1 — только к camp1.
    const concepts: UploadedConcept[] = [
      { ref: "shared.jpg", original_name: "shared.jpg", size_bytes: 512, content_type: "image/jpeg", campaign_keys: ["camp1", "camp2"] },
      { ref: "only_c1.mp4", original_name: "only_c1.mp4", size_bytes: 512, content_type: "video/mp4", campaign_keys: ["camp1"] },
    ];
    seedStore(concepts, [
      { key: "camp1" },
      { key: "camp2" },
    ]);

    const config = useWizardStore.getState().buildConfig();

    const c1 = config.campaigns.find((c) => c.key === "camp1");
    const c2 = config.campaigns.find((c) => c.key === "camp2");
    // camp1 получает оба: shared + only_c1
    expect(c1?.concept_refs).toEqual(["shared.jpg", "only_c1.mp4"]);
    // camp2 получает только shared; only_c1 не привязан к camp2
    expect(c2?.concept_refs).toEqual(["shared.jpg"]);
  });

  it("нет концептов → concept_refs пустые массивы (не падает)", () => {
    // Пустой upload не должен крашить buildConfig.
    seedStore([], [{ key: "camp1" }]);
    const config = useWizardStore.getState().buildConfig();
    expect(config.campaigns[0]!.concept_refs).toEqual([]);
  });

  it("нет кампаний → campaigns пустой массив", () => {
    // Корнер-кейс: структура без кампаний.
    seedStore([], []);
    const config = useWizardStore.getState().buildConfig();
    expect(config.campaigns).toEqual([]);
  });
});

describe("buildConfig — url_tags отсутствует в конфиге", () => {
  beforeEach(() => {
    useWizardStore.getState().reset();
  });

  it("buildConfig не включает поле url_tags (вычисляется бэком по SOP)", () => {
    // url_tags не должен редактироваться пользователем — бэк генерирует его сам.
    seedStore([], [{ key: "camp1" }]);
    const config = useWizardStore.getState().buildConfig();
    // url_tags должен быть undefined или отсутствовать (не редактируется пользователем)
    expect((config as unknown as Record<string, unknown>)["url_tags"]).toBeUndefined();
  });
});

describe("buildConfig — ad_text контракт {mode, primary}", () => {
  beforeEach(() => {
    useWizardStore.getState().reset();
  });

  it("mode=none → {mode: 'none'}", () => {
    // По умолчанию текст объявления отключён.
    seedStore([], [{ key: "s1" }]);
    // goal.ad_text_mode = "none" по умолчанию
    const config = useWizardStore.getState().buildConfig();
    expect(config.ad_text).toEqual({ mode: "none" });
  });

  it("mode=text + primary → {mode: 'text', primary: '...'}", () => {
    // При включённом тексте primary обязателен.
    seedStore([], [{ key: "s1" }]);
    useWizardStore.getState().setGoal({ ad_text_mode: "text", ad_text_primary: "Привет мир" });
    const config = useWizardStore.getState().buildConfig();
    expect(config.ad_text).toEqual({ mode: "text", primary: "Привет мир" });
  });
});
