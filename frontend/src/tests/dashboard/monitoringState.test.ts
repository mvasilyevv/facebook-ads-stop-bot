import { describe, expect, it } from "vitest";

import { resolveMonitoringState } from "@/components/dashboard/monitoringState";
import type { HealthDetails } from "@fb/shared";

function health(overrides: Partial<HealthDetails> = {}): HealthDetails {
  return {
    workers: [{ name: "observer", status: "ONLINE" }],
    observer_runtime: { status: "running" },
    meta_api_channel: { status: "ONLINE" },
    overall: "HEALTHY",
    ...overrides,
  };
}

describe("resolveMonitoringState", () => {
  it("не считает UNKNOWN watchdog-probe отказом при живом runtime", () => {
    expect(
      resolveMonitoringState({
        health: health({ meta_api_channel: { status: "UNKNOWN" } }),
        healthLoading: false,
        healthError: false,
        scanOn: true,
      }),
    ).toBe("healthy");
  });

  it("показывает паузу, а не деградацию, когда все воркеры живы", () => {
    expect(
      resolveMonitoringState({
        health: health({
          observer_runtime: { status: "paused" },
          meta_api_channel: { status: "DEGRADED" },
          overall: "DEGRADED",
        }),
        healthLoading: false,
        healthError: false,
        scanOn: false,
      }),
    ).toBe("paused");
  });

  it("не скрывает реальную деградацию воркера за паузой", () => {
    expect(
      resolveMonitoringState({
        health: health({
          workers: [
            { name: "observer", status: "ONLINE" },
            { name: "meta_api", status: "OFFLINE" },
          ],
          observer_runtime: { status: "paused" },
          overall: "DEGRADED",
        }),
        healthLoading: false,
        healthError: false,
        scanOn: false,
      }),
    ).toBe("degraded");
  });
});
