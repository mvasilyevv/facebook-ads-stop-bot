import { describe, it, expect } from "vitest";
import {
  deriveGeoFromNames,
  countryNameRu,
  countryFlagEmoji,
  isValidCountryCode,
  searchCountries,
} from "../geo";

describe("deriveGeoFromNames — единый гео-парсер web/mini", () => {
  // Классический формат имени кампании: гео отдельным токеном через « | ».
  it("находит ISO-2 токен в pipe-формате", () => {
    expect(deriveGeoFromNames("CR2 | GH | MV | 25.03", null)).toBe("GH");
  });

  // Формат с подчёркиванием («CR2_GH») — старый mini-regex его НЕ находил.
  it("находит гео в underscore-формате (регресс mini extractGeo)", () => {
    expect(deriveGeoFromNames("CR2_GH", null)).toBe("GH");
  });

  // Гео с приклеенным числом: «UA7» → UA.
  it("гео с приклеенным числом", () => {
    expect(deriveGeoFromNames("Promo UA7 test", null)).toBe("UA");
  });

  // Кампания приоритетнее имени объявления (порядок аргументов).
  it("кампания приоритетнее ad_name", () => {
    expect(deriveGeoFromNames("X | DE |", "Y | BR |")).toBe("DE");
  });

  // Ничего не нашли → фолбэк по первым буквам, совсем пусто → «—».
  it("пустой вход → «—»", () => {
    expect(deriveGeoFromNames(null, undefined)).toBe("—");
  });
});

describe("countryNameRu / countryFlagEmoji — русские имена стран", () => {
  // Корень бага: GH (Гана) путали с GE (Грузия) в криптичном коде. Фиксируем имена.
  it("GH → Гана, GE → Грузия (НЕ путаются)", () => {
    expect(countryNameRu("GH")).toBe("Гана");
    expect(countryNameRu("GE")).toBe("Грузия");
    expect(countryNameRu("GH")).not.toBe(countryNameRu("GE"));
  });

  // Регистронезависимость + фолбэк на сам код для мусора.
  it("нормализует регистр, мусор → сам код", () => {
    expect(countryNameRu("gh")).toBe("Гана");
    expect(countryNameRu("ZZ")).toBe("ZZ");
  });

  // Флаг-emoji из кода.
  it("флаг страны из ISO-2", () => {
    expect(countryFlagEmoji("GH")).toBe("🇬🇭");
    expect(countryFlagEmoji("xx-bad")).toBe("🏳️");
  });

  it("валидация ISO-2 кода", () => {
    expect(isValidCountryCode("GH")).toBe(true);
    expect(isValidCountryCode("gh")).toBe(true);
    expect(isValidCountryCode("ZZ")).toBe(false);
  });
});

describe("searchCountries — поиск по имени/коду, хранит код", () => {
  // По русскому имени.
  it("находит «Гана» по подстроке имени, отдаёт код GH", () => {
    const res = searchCountries("ган");
    expect(res.some((c) => c.code === "GH" && c.name === "Гана")).toBe(true);
  });

  // По ISO-2 коду.
  it("находит по коду", () => {
    const res = searchCountries("gh");
    expect(res[0]?.code).toBe("GH");
  });

  // exclude убирает уже выбранные.
  it("исключает выбранные коды", () => {
    const res = searchCountries("", { exclude: ["GH"], limit: 300 });
    expect(res.some((c) => c.code === "GH")).toBe(false);
  });
});
