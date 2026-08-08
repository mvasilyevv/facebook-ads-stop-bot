import { describe, expect, it } from "vitest";

import {
  adsManagerCampaignUrl,
  campaignMetaIdGroups,
  campaignRunCommandLifecycle,
  campaignRunControlReason,
  campaignRunFailurePresentation,
  campaignRunRequiresManualReview,
  campaignRunTaskLifecycle,
} from "../campaignRunReview";

describe("campaignRunReview", () => {
  it("marks persisted partial and UNKNOWN failures for manual review", () => {
    expect(
      campaignRunRequiresManualReview({
        status: "failed",
        failure_class: "manual_review",
        task: { state: "unknown", outcome: "UNKNOWN" },
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
        task: { state: "failed", outcome: "REJECTED" },
        controls: {
          resume: {
            available: true,
            reason: "pre_external_checkpoint_available",
          },
        },
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
        task: { state: "failed", outcome: "REJECTED" },
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

  it.each([
    {
      expected: "manual_review",
      run: {
        status: "failed",
        task: { state: "unknown", outcome: "UNKNOWN" },
        controls: {
          resume: { available: false, reason: "external_boundary_crossed" },
        },
      },
    },
    {
      expected: "safe_retry",
      run: {
        status: "failed",
        failure_class: "safe_retry",
        task: { state: "failed", outcome: "REJECTED" },
        controls: {
          resume: {
            available: true,
            reason: "pre_external_checkpoint_available",
          },
        },
      },
    },
    {
      expected: "invalid_config",
      run: {
        status: "failed",
        failure_class: "invalid_config",
        task: { state: "failed", outcome: "REJECTED" },
        controls: {
          resume: { available: false, reason: "invalid_config_checkpoint" },
        },
      },
    },
    {
      expected: "invalid_media",
      run: {
        status: "failed",
        failure_class: "invalid_media",
        task: { state: "failed", outcome: "REJECTED" },
        controls: {
          resume: { available: false, reason: "media_checkpoint_missing" },
        },
      },
    },
    {
      expected: "unavailable",
      run: {
        status: "failed",
        failure_class: "unavailable",
        task: { state: "failed", outcome: "REJECTED" },
        controls: {
          resume: {
            available: false,
            reason: "checkpoint_reason_not_resumable",
          },
        },
      },
    },
  ])(
    "classifies $expected with a concrete localized action",
    ({ run, expected }) => {
      const presentation = campaignRunFailurePresentation(run);

      expect(presentation?.category).toBe(expected);
      expect(presentation?.reason.length).toBeGreaterThan(20);
      expect(presentation?.action.label.length).toBeGreaterThan(5);
      expect(presentation?.action.available).toBe(true);
    },
  );

  it("ignores raw error, progress and task-result shaped diagnostics", () => {
    const presentation = campaignRunFailurePresentation({
      status: "failed",
      failure_class: "safe_retry",
      task: { state: "failed", outcome: "REJECTED" },
      controls: {
        resume: {
          available: true,
          reason: "pre_external_checkpoint_available",
        },
      },
      error: "Traceback: token=secret",
      progress: {
        reason: "internal_reason",
        task_result: { exception: "database password" },
      },
    });

    expect(JSON.stringify(presentation)).not.toMatch(
      /Traceback|token=secret|internal_reason|database password/,
    );
    expect(presentation?.category).toBe("safe_retry");
  });
});
