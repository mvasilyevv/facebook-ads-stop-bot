import { describe, it, expect } from "vitest";
import { deriveGeoFromNames } from "../geo";
import { alertStateCssVar } from "../../constants/states";

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

describe("alertStateCssVar — state → FSM-токен", () => {
  // Токены названы по СТАДИИ: warning_sent → --fsm-warning (НЕ --fsm-warning_sent).
  // Регресс бага mini: невидимая точка для warning/stop из-за несуществующего токена.
  it("warning_sent/stop_sent маппятся на токены стадий", () => {
    expect(alertStateCssVar("warning_sent")).toBe("var(--fsm-warning)");
    expect(alertStateCssVar("stop_sent")).toBe("var(--fsm-stop)");
  });

  // UPPERCASE из TMA-API нормализуется.
  it("нормализует UPPERCASE и null", () => {
    expect(alertStateCssVar("STOP_SENT")).toBe("var(--fsm-stop)");
    expect(alertStateCssVar(null)).toBe("var(--fsm-normal)");
  });
});
