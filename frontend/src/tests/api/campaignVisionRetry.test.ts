import { describe, expect, it } from "vitest";

import { GeneratedApiError } from "@fb/operator-api";
import {
  shouldRetryVisionMetadata,
  visionMetadataRetryDelay,
} from "@/lib/api/campaigns";

describe("campaign Vision metadata retry", () => {
  it("повторяет только транзиентный 503 и не больше трёх раз", () => {
    const unavailable = new GeneratedApiError(503, null);
    expect(shouldRetryVisionMetadata(0, unavailable)).toBe(true);
    expect(shouldRetryVisionMetadata(2, unavailable)).toBe(true);
    expect(shouldRetryVisionMetadata(3, unavailable)).toBe(false);
    expect(shouldRetryVisionMetadata(0, new GeneratedApiError(422, null))).toBe(false);
    expect(shouldRetryVisionMetadata(0, new Error("network"))).toBe(false);
  });

  it("использует ограниченную экспоненциальную задержку", () => {
    expect([0, 1, 2, 3].map(visionMetadataRetryDelay)).toEqual([1_000, 2_000, 4_000, 4_000]);
  });
});
