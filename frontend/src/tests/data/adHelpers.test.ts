/**
 * Тесты хелперов adHelpers — парсинг метрик, пороги-флаги, гео, money1.
 */

import { describe, it, expect } from "vitest";
import {
  num,
  readAdMetrics,
  isCplBad,
  isFreqBad,
  isRoasBad,
  money1,
  deriveGeo,
} from "@/components/domain/ads/adHelpers";
import type { AdSnapshot } from "@fb/shared";

function ad(overrides: Partial<AdSnapshot> = {}): AdSnapshot {
  return {
    fb_ad_id: "1",
    internal_id: "u1",
    ad_name: "X",
    alert_state: "normal",
    is_active: true,
    ...overrides,
  } as AdSnapshot;
}

describe("adHelpers.num", () => {
  // Парс строки/числа, пустое/NaN → null.
  it("парсит строку и число, пустое/NaN → null", () => {
    expect(num("12.5")).toBe(12.5);
    expect(num(7)).toBe(7);
    expect(num("")).toBeNull();
    expect(num(null)).toBeNull();
    expect(num("abc")).toBeNull();
  });
});

describe("adHelpers.readAdMetrics", () => {
  // ROAS всегда null (нет в API-схеме).
  it("читает метрики из snapshot, ROAS всегда null", () => {
    const m = readAdMetrics(
      ad({
        metrics: {
          cycle_ts: "t",
          spend: "100.5",
          cost_per_lead: "33.0",
          cpm: "9.1",
          ctr: "1.4",
          frequency: "5.2",
          leads: 12,
          deposits: 2,
        },
      }),
    );
    expect(m.spend).toBe(100.5);
    expect(m.cpl).toBe(33);
    expect(m.cpm).toBe(9.1);
    expect(m.ctr).toBe(1.4);
    expect(m.freq).toBe(5.2);
    expect(m.leads).toBe(12);
    expect(m.roas).toBeNull();
  });

  // Без metrics — все null.
  it("без metrics возвращает null-значения", () => {
    const m = readAdMetrics(ad({ metrics: null }));
    expect(m.spend).toBeNull();
    expect(m.cpl).toBeNull();
    expect(m.roas).toBeNull();
  });
});

describe("adHelpers пороги-флаги", () => {
  // CPL>30, FREQ>4, ROAS<1.
  it("CPL>30 → danger", () => {
    expect(isCplBad(31)).toBe(true);
    expect(isCplBad(30)).toBe(false);
    expect(isCplBad(null)).toBe(false);
  });
  it("FREQ>4 → danger", () => {
    expect(isFreqBad(4.1)).toBe(true);
    expect(isFreqBad(4)).toBe(false);
  });
  it("ROAS<1 → danger (только если значение есть)", () => {
    expect(isRoasBad(0.9)).toBe(true);
    expect(isRoasBad(1)).toBe(false);
    expect(isRoasBad(null)).toBe(false);
  });
});

describe("adHelpers.money1", () => {
  // Один знак после запятой + null → «—».
  it("форматирует деньги с одним знаком, null → «—»", () => {
    expect(money1(1234.56)).toBe("$1,234.6");
    expect(money1(0)).toBe("$0.0");
    expect(money1(null)).toBe("—");
  });
});

describe("adHelpers.deriveGeo", () => {
  // ISO-2 токен в имени.
  it("находит явный ISO-2 токен", () => {
    expect(deriveGeo(ad({ ad_name: "CR2 | DRC | MV | GH | 25.03" }))).toBe("GH");
  });
  // Гео-код с числом (GH12 → GH).
  it("находит гео-код с приклеенным числом", () => {
    expect(deriveGeo(ad({ ad_name: "GH12 | CR2 | MV" }))).toBe("GH");
  });
  // Фолбэк — первые 2 буквы первого токена.
  it("фолбэк на первые 2 буквы первого токена", () => {
    expect(deriveGeo(ad({ ad_name: "Zztop campaign" }))).toBe("ZZ");
  });
  // Пусто → «—».
  it("пустое имя → «—»", () => {
    expect(deriveGeo(ad({ ad_name: "", campaign_name: null }))).toBe("—");
  });
});
