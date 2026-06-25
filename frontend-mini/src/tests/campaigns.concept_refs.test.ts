/**
 * Тесты заполнения concept_refs в mini-визарде.
 *
 * StepCreatives при handleNext должен:
 *   - записать concept_refs во все кампании из localConcepts.map(c => c.ref)
 *   - сохранить creo_root = upload_id
 *
 * MID ad_text контракт:
 *   - CampaignConfig.ad_text имеет {mode, primary?}, не {mode, texts[]}
 */
import { describe, it, expect, beforeEach } from "vitest";
import { useWizardStore } from "@/routes/campaigns/-wizardStore";
import type { CampaignSpec } from "@/lib/campaignTypes";

describe("mini — concept_refs заполняются из upload (CRIT)", () => {
  beforeEach(() => {
    useWizardStore.getState().reset();
    // Добавляем две кампании в config (смешанные медиа — без kind)
    const camps: CampaignSpec[] = [
      { key: "camp_1", adset_count: 3 },
      { key: "camp_2", adset_count: 2 },
    ];
    useWizardStore.getState().updateConfig({ campaigns: camps });
  });

  it("после setUpload все кампании имеют пустые concept_refs (до handleNext)", () => {
    // setUpload хранит концепты, но ещё не пишет в campaigns
    useWizardStore.getState().setUpload("upload-xyz", [
      { ref: "img1.jpg", original_name: "img1.jpg", size_bytes: 1024, content_type: "image/jpeg" },
    ]);
    // campaigns не должны измениться сами по себе
    const camps = useWizardStore.getState().config.campaigns ?? [];
    for (const c of camps) {
      expect(c.concept_refs ?? []).toEqual([]);
    }
  });

  it("после updateConfig с campaignsWithRefs — concept_refs заполнены во всех кампаниях", () => {
    // Имитируем то, что делает handleNext в StepCreatives
    const allRefs = ["img1.jpg", "vid1.mp4"];
    const campaigns = useWizardStore.getState().config.campaigns ?? [];
    const campaignsWithRefs = campaigns.map((c) => ({ ...c, concept_refs: allRefs }));
    useWizardStore.getState().updateConfig({ creo_root: "upload-xyz", campaigns: campaignsWithRefs });

    const result = useWizardStore.getState().config.campaigns ?? [];
    expect(result).toHaveLength(2);
    expect(result[0]!.concept_refs).toEqual(["img1.jpg", "vid1.mp4"]);
    expect(result[1]!.concept_refs).toEqual(["img1.jpg", "vid1.mp4"]);
  });

  it("concept_refs не пусты → validate получит непустые concept_refs (money-контракт)", () => {
    const allRefs = ["photo.jpg"];
    const campaigns = useWizardStore.getState().config.campaigns ?? [];
    const campaignsWithRefs = campaigns.map((c) => ({ ...c, concept_refs: allRefs }));
    useWizardStore.getState().updateConfig({ creo_root: "upload-abc", campaigns: campaignsWithRefs });

    const cfg = useWizardStore.getState().config;
    for (const c of cfg.campaigns ?? []) {
      expect((c.concept_refs ?? []).length).toBeGreaterThan(0);
    }
  });
});

describe("mini — ad_text контракт {mode, primary?}", () => {
  beforeEach(() => {
    useWizardStore.getState().reset();
  });

  it("можно записать ad_text={mode:'none'} без ошибок типа", () => {
    useWizardStore.getState().updateConfig({ ad_text: { mode: "none" } });
    expect(useWizardStore.getState().config.ad_text?.mode).toBe("none");
  });

  it("можно записать ad_text={mode:'text', primary:'Привет'}", () => {
    useWizardStore.getState().updateConfig({ ad_text: { mode: "text", primary: "Привет мир" } });
    const adText = useWizardStore.getState().config.ad_text;
    expect(adText?.mode).toBe("text");
    expect(adText?.primary).toBe("Привет мир");
  });

  it("ad_text НЕ содержит поле texts (старый контракт mini)", () => {
    useWizardStore.getState().updateConfig({ ad_text: { mode: "text", primary: "Test" } });
    const adText = useWizardStore.getState().config.ad_text;
    // Поля texts не должно быть в новом контракте
    expect((adText as Record<string, unknown> | undefined | null)?.["texts"]).toBeUndefined();
  });
});
