/**
 * Тесты buildConfig визарда создания кампаний.
 *
 * Покрываем:
 *   - concept_refs заполняются из назначенных концептов (CRIT money-fix)
 *   - url_tags не попадает в конфиг (HIGH mislabel-fix)
 *   - пустой campaign_keys = концепт идёт во все кампании
 *   - концепт с campaign_keys только в конкретной кампании
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
function seedStore(concepts: UploadedConcept[], campaigns: { key: string; kind: "image" | "video" }[]) {
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

  it("концепт с пустым campaign_keys → идёт во все кампании СВОЕГО типа (kind-фильтр)", () => {
    // Два концепта без назначения: видео-ref только в video-кампанию, фото — только в image.
    // Иначе чужой тип уронил бы уникализатор уже после создания объектов в Meta (орфаны).
    const concepts: UploadedConcept[] = [
      { ref: "img1.jpg", original_name: "img1.jpg", size_bytes: 1024, content_type: "image/jpeg", campaign_keys: [] },
      { ref: "vid1.mp4", original_name: "vid1.mp4", size_bytes: 2048, content_type: "video/mp4", campaign_keys: [] },
    ];
    seedStore(concepts, [
      { key: "static1", kind: "image" },
      { key: "video1", kind: "video" },
    ]);

    const config = useWizardStore.getState().buildConfig();

    expect(config.campaigns).toHaveLength(2);
    // image-кампания получает только фото, video-кампания — только видео.
    expect(config.campaigns[0]!.concept_refs).toEqual(["img1.jpg"]);
    expect(config.campaigns[1]!.concept_refs).toEqual(["vid1.mp4"]);
  });

  it("видео-концепт без назначения НЕ попадает в image-кампанию (kind-фильтр)", () => {
    const concepts: UploadedConcept[] = [
      { ref: "clip.mp4", original_name: "clip.mp4", size_bytes: 2048, content_type: "video/mp4", campaign_keys: [] },
    ];
    seedStore(concepts, [{ key: "static1", kind: "image" }]);
    const config = useWizardStore.getState().buildConfig();
    expect(config.campaigns[0]!.concept_refs).toEqual([]);
  });

  it("концепт с campaign_keys=['static1'] → попадает только в static1, не в video1", () => {
    const concepts: UploadedConcept[] = [
      { ref: "img1.jpg", original_name: "img1.jpg", size_bytes: 1024, content_type: "image/jpeg", campaign_keys: ["static1"] },
      { ref: "vid1.mp4", original_name: "vid1.mp4", size_bytes: 2048, content_type: "video/mp4", campaign_keys: ["video1"] },
    ];
    seedStore(concepts, [
      { key: "static1", kind: "image" },
      { key: "video1", kind: "video" },
    ]);

    const config = useWizardStore.getState().buildConfig();

    const staticCamp = config.campaigns.find((c) => c.key === "static1");
    const videoCamp  = config.campaigns.find((c) => c.key === "video1");
    expect(staticCamp?.concept_refs).toEqual(["img1.jpg"]);
    expect(videoCamp?.concept_refs).toEqual(["vid1.mp4"]);
  });

  it("смесь: концепт без назначения + концепт с назначением", () => {
    const concepts: UploadedConcept[] = [
      // без назначения — идёт везде
      { ref: "shared.jpg", original_name: "shared.jpg", size_bytes: 512, content_type: "image/jpeg", campaign_keys: [] },
      // только для static1
      { ref: "only_static.jpg", original_name: "only_static.jpg", size_bytes: 512, content_type: "image/jpeg", campaign_keys: ["static1"] },
    ];
    seedStore(concepts, [
      { key: "static1", kind: "image" },
      { key: "video1", kind: "video" },
    ]);

    const config = useWizardStore.getState().buildConfig();

    const staticCamp = config.campaigns.find((c) => c.key === "static1");
    const videoCamp  = config.campaigns.find((c) => c.key === "video1");
    // static1 (image) получает оба фото: shared + only_static
    expect(staticCamp?.concept_refs).toEqual(["shared.jpg", "only_static.jpg"]);
    // video1 (video) — пусто: оба концепта фото, kind-фильтр их не пускает в видео-кампанию
    expect(videoCamp?.concept_refs).toEqual([]);
  });

  it("нет концептов → concept_refs пустые массивы (не падает)", () => {
    seedStore([], [{ key: "static1", kind: "image" }]);
    const config = useWizardStore.getState().buildConfig();
    expect(config.campaigns[0]!.concept_refs).toEqual([]);
  });

  it("нет кампаний → campaigns пустой массив", () => {
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
    seedStore([], [{ key: "static1", kind: "image" }]);
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
    seedStore([], [{ key: "s1", kind: "image" }]);
    // goal.ad_text_mode = "none" по умолчанию
    const config = useWizardStore.getState().buildConfig();
    expect(config.ad_text).toEqual({ mode: "none" });
  });

  it("mode=text + primary → {mode: 'text', primary: '...'}", () => {
    seedStore([], [{ key: "s1", kind: "image" }]);
    useWizardStore.getState().setGoal({ ad_text_mode: "text", ad_text_primary: "Привет мир" });
    const config = useWizardStore.getState().buildConfig();
    expect(config.ad_text).toEqual({ mode: "text", primary: "Привет мир" });
  });
});
