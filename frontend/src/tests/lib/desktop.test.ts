/**
 * #345 QW10 — /remote-desktop обещает «страница обновится сама», но канал не
 * поллился, пока устройство ещё не опубликовало ID. Регресс фиксирует, что
 * refetchInterval остаётся включённым до появления device_id и выключается
 * после — бесконечный опрос уже стабильного канала не нужен.
 */
import { describe, expect, it } from "vitest";

import { desktopNativeRefetchInterval } from "@/lib/api/desktop";

describe("desktopNativeRefetchInterval", () => {
  it("продолжает поллить, пока ответа ещё не было", () => {
    expect(desktopNativeRefetchInterval(undefined)).toBe(15_000);
  });

  it("продолжает поллить, пока канал не опубликовал device_id", () => {
    expect(
      desktopNativeRefetchInterval({
        available: false,
        device_id: null,
        server: null,
        key: null,
      }),
    ).toBe(15_000);
  });

  it("останавливает опрос, как только устройство появилось", () => {
    expect(
      desktopNativeRefetchInterval({
        available: true,
        device_id: "253474910",
        server: "100.73.162.127",
        key: "secret",
      }),
    ).toBe(false);
  });
});
