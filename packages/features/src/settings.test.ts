import { describe, expect, it } from "vitest";

import {
  isSafeTelegramWebAppUrl,
  normalizeOwnerCampaignTags,
  telegramPreferenceDraftFromResponse,
  telegramPreferencePayload,
  validateObserverInterval,
  validateTelegramPreferenceDraft,
} from "./settings";

describe("settings feature model", () => {
  it("validates the bounded observer interval", () => {
    expect(validateObserverInterval("30")).toBeNull();
    expect(validateObserverInterval(600)).toBeNull();
    expect(validateObserverInterval("29")).toMatch(/от 30 до 600/);
    expect(validateObserverInterval("60.5")).toMatch(/целое число/);
  });

  it("normalizes and deduplicates owner campaign tags", () => {
    expect(normalizeOwnerCampaignTags(" MV, abc, mv, ABC ")).toBe("MV,abc");
    expect(normalizeOwnerCampaignTags(" , ")).toBeNull();
  });

  it("rejects unsafe Telegram Mini App URLs", () => {
    expect(isSafeTelegramWebAppUrl("https://agent.example/app")).toBe(true);
    expect(isSafeTelegramWebAppUrl("")).toBe(true);
    expect(isSafeTelegramWebAppUrl("http://agent.example/app")).toBe(false);
    expect(
      isSafeTelegramWebAppUrl("https://owner:secret@agent.example/app"),
    ).toBe(false);
  });

  it("validates and serializes recipient preferences without recipient identity", () => {
    const draft = telegramPreferenceDraftFromResponse({
      recipient_id: "00000000-0000-0000-0000-000000000001",
      timezone: "Europe/Kaliningrad",
      min_severity: "warning",
      quiet_hours_start: "23:00",
      quiet_hours_end: "07:00",
      digest_local_time: "10:30",
      categories: { recommendations: "off" },
      is_enabled: true,
      updated_at: null,
    });
    expect(validateTelegramPreferenceDraft(draft)).toEqual({});
    expect(telegramPreferencePayload(draft)).toEqual({
      timezone: "Europe/Kaliningrad",
      min_severity: "warning",
      quiet_hours_start: "23:00",
      quiet_hours_end: "07:00",
      digest_local_time: "10:30",
      categories: { recommendations: "off" },
      is_enabled: true,
    });
  });

  it("requires both quiet-hour boundaries and a valid timezone", () => {
    const errors = validateTelegramPreferenceDraft({
      timezone: "Mars/Olympus",
      minSeverity: "critical",
      quietHoursStart: "23:00",
      quietHoursEnd: "",
      digestLocalTime: "25:30",
      categories: {},
      isEnabled: true,
    });
    expect(errors.timezone).toBeDefined();
    expect(errors.quietHours).toBeDefined();
    expect(errors.digestLocalTime).toBeDefined();
  });
});
