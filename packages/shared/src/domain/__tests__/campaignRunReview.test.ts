import { describe, expect, it } from "vitest";

import {
  adsManagerCampaignUrl,
  campaignMetaIdGroups,
  campaignRunCommandLifecycle,
  campaignRunControlReason,
  campaignRunRequiresManualReview,
  campaignRunTaskLifecycle,
} from "../campaignRunReview";

describe("campaignRunReview", () => {
  it("marks persisted partial and UNKNOWN failures for manual review", () => {
    expect(
      campaignRunRequiresManualReview({
        status: "failed",
        progress: { outcome: "UNKNOWN", reason: "partial_or_ack_lost" },
        created_meta_ids: { campaigns: [], adsets: [], ads: [], creatives: [] },
      }),
    ).toBe(true);
    expect(
      campaignRunRequiresManualReview({
        status: "failed",
        created_meta_ids: { campaigns: ["101"] },
      }),
    ).toBe(true);
    expect(
      campaignRunRequiresManualReview({
        status: "failed",
        progress: { outcome: "REJECTED" },
        created_meta_ids: {
          campaigns: [],
          adsets: [],
          ads: [],
          creatives: [],
        },
      }),
    ).toBe(false);
    expect(
      campaignRunRequiresManualReview({
        status: "failed",
        progress: { outcome: "REJECTED" },
        created_meta_ids: { campaigns: { unexpected: "101" } },
      }),
    ).toBe(true);
    expect(
      campaignRunRequiresManualReview({
        status: "succeeded",
        created_meta_ids: { campaigns: ["101"] },
      }),
    ).toBe(false);
  });

  it("preserves created IDs for display and deduplicates them", () => {
    const groups = campaignMetaIdGroups({
      campaigns: ["101", "101"],
      adsets: ["201", 202],
      ads: [],
    });

    expect(groups.find((group) => group.key === "campaigns")?.ids).toEqual([
      "101",
    ]);
    expect(groups.find((group) => group.key === "adsets")?.ids).toEqual([
      "201",
      "202",
    ]);
  });

  it("builds the canonical Ads Manager URL only from numeric campaign IDs", () => {
    expect(
      adsManagerCampaignUrl({
        campaigns: ["101", "javascript:alert(1)", "102"],
      }),
    ).toBe(
      "https://www.facebook.com/adsmanager/manage/campaigns?ids=101%2C102",
    );
    expect(adsManagerCampaignUrl({ campaigns: ["not-an-id"] })).toBeNull();
  });

  it("keeps queued and unknown task lifecycle distinct from confirmed success", () => {
    expect(campaignRunTaskLifecycle("queued")).toMatchObject({
      label: "В очереди",
      tone: "pending",
    });
    expect(campaignRunTaskLifecycle("unknown")).toMatchObject({
      label: "Результат неизвестен",
      tone: "unknown",
    });
    expect(
      campaignRunCommandLifecycle("abort", "queued").description,
    ).toContain("ещё не подтверждено");
    expect(
      campaignRunCommandLifecycle("resume", "unknown").description,
    ).toContain("Не повторяйте");
  });

  it("turns control reason codes into stable human guidance", () => {
    expect(
      campaignRunControlReason("resume", "external_boundary_crossed", false),
    ).toBe("Повтор заблокирован: задача могла начать изменения в Meta.");
    expect(
      campaignRunControlReason("abort", "future_backend_reason", false),
    ).toBe("Остановка пока недоступна. Обновите данные запуска.");
    expect(
      campaignRunControlReason(
        "resume",
        "pre_external_checkpoint_available",
        true,
      ),
    ).toContain("Можно безопасно повторить");
  });
});
