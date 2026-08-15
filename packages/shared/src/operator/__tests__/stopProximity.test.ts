import { describe, expect, it } from "vitest";

import type { OperatorAdRow } from "../contracts";
import {
  describeStopProximity,
  rankAdsByStopProximity,
  stopProximityBarWidth,
  type OperatorRuleContext,
} from "../stopProximity";

function context(
  overrides: Partial<OperatorRuleContext> = {},
): OperatorRuleContext {
  return {
    offer_code: "GH_CR2",
    rule_code: "cpr_stop",
    rule_title: "Дорогая рега",
    value: "0.41",
    threshold: "0.48",
    percent_to_stop: "85.41",
    stage: "warning",
    ...overrides,
  };
}

describe("describeStopProximity", () => {
  it("держит warning и stop различимыми не только цветом", () => {
    const warning = describeStopProximity(context({ stage: "warning" }));
    const stop = describeStopProximity(
      context({ stage: "stop", percent_to_stop: "104.00" }),
    );

    expect(warning.tone).not.toBe(stop.tone);
    // Форма, символ и лейбл обязаны отличаться сами по себе: цвет может быть
    // недоступен в ч/б, forced-colors и при дальтонизме.
    expect(warning.shape).not.toBe(stop.shape);
    expect(warning.mark).not.toBe(stop.mark);
    expect(warning.label).not.toBe(stop.label);
    expect(warning.label).toBe("Подходит к стопу");
    expect(stop.label).toBe("Порог пройден");
  });

  it("отдаёт прочерк, а не ноль, когда стадия не подтверждена", () => {
    const unknown = describeStopProximity(context({ stage: null }));

    expect(unknown.kind).toBe("unknown");
    expect(unknown.percentText).toBe("—");
    expect(unknown.percent).toBeNull();
    expect(unknown.tone).toBe("neutral");
    expect(unknown.percentText).not.toBe("0%");
  });

  it("считает пустой rule_context неизвестностью, а не безопасностью", () => {
    const empty = describeStopProximity({
      offer_code: null,
      rule_code: null,
      rule_title: null,
      value: null,
      threshold: null,
      percent_to_stop: null,
      stage: null,
    });

    expect(empty.kind).toBe("unknown");
    expect(empty.label).toBe("Не подтверждено");
    expect(describeStopProximity(null).kind).toBe("unknown");
  });

  it("помечает объявление без оффера как не защищённое правилом", () => {
    const notMonitored = describeStopProximity(
      context({
        offer_code: null,
        rule_code: null,
        rule_title: null,
        value: null,
        threshold: null,
        percent_to_stop: null,
        stage: "none",
      }),
    );

    expect(notMonitored.kind).toBe("not_monitored");
    expect(notMonitored.label).toBe("Правило не применяется");
    expect(notMonitored.percentText).toBe("—");
    expect(notMonitored.hint).toContain("авто-стоп его не остановит");
  });

  it("различает сопоставленный оффер без подтверждённого порога", () => {
    const unevaluated = describeStopProximity(
      context({
        rule_code: null,
        rule_title: null,
        value: null,
        threshold: null,
        percent_to_stop: null,
        stage: "none",
      }),
    );

    expect(unevaluated.kind).toBe("unevaluated");
    expect(unevaluated.label).toBe("Порог не рассчитан");
    expect(unevaluated.offerCode).toBe("GH_CR2");
  });

  it("форматирует денежное правило валютой, а долю — усечением", () => {
    const money = describeStopProximity(context(), { currency: "USD" });

    expect(money.detail).toBe("$0.41 из $0.48");
    expect(money.percentText).toBe("85.4%");
    expect(money.ruleText).toBe("Дорогая рега");
  });

  it("не показывает денежный порог без подтверждённой валюты", () => {
    const hidden = describeStopProximity(context(), { currency: null });

    expect(hidden.detail).toBeNull();
    expect(hidden.percentText).toBe("85.4%");
  });

  it("форматирует неденежное правило без валюты", () => {
    const frequency = describeStopProximity(
      context({
        rule_code: "frequency_anomaly",
        rule_title: null,
        value: "3.456",
        threshold: "3.000",
        percent_to_stop: "115.20",
        stage: "stop",
      }),
    );

    expect(frequency.detail).toBe("3.45 из 3");
    expect(frequency.ruleText).toBe("Выгорание аудитории");
  });

  it("никогда не округляет долю вверх до достигнутого стопа", () => {
    const almost = describeStopProximity(
      context({ percent_to_stop: "99.99", stage: "warning" }),
    );

    expect(almost.percentText).toBe("99.9%");
  });
});

describe("stopProximityBarWidth", () => {
  it("ограничивает шкалу сотней и не рисует неизвестность нулём", () => {
    expect(stopProximityBarWidth(describeStopProximity(context()))).toBe(
      "85.4",
    );
    expect(
      stopProximityBarWidth(
        describeStopProximity(
          context({ stage: "stop", percent_to_stop: "260.00" }),
        ),
      ),
    ).toBe("100");
    expect(
      stopProximityBarWidth(describeStopProximity(context({ stage: null }))),
    ).toBeNull();
  });
});

describe("rankAdsByStopProximity", () => {
  function row(
    id: string,
    rule: OperatorRuleContext,
  ): Pick<OperatorAdRow, "rule_context"> & {
    id: string;
  } {
    return { id, rule_context: rule };
  }

  it("ставит ближайших к стопу первыми и не путает порядок на хвостах", () => {
    const ranked = rankAdsByStopProximity([
      row("low", context({ percent_to_stop: "72.00" })),
      row("highest", context({ percent_to_stop: "99.999999999999996" })),
      row("middle", context({ percent_to_stop: "84.00" })),
      row("above", context({ percent_to_stop: "100", stage: "stop" })),
    ]);

    expect(ranked.map((item) => item.id)).toEqual([
      "above",
      "highest",
      "middle",
      "low",
    ]);
  });

  it("опускает строки без подтверждённой доли вниз, сохраняя их порядок", () => {
    const ranked = rankAdsByStopProximity([
      row("unknown-first", context({ stage: null })),
      row("tracked", context({ percent_to_stop: "12.00", stage: "none" })),
      row("unknown-second", context({ offer_code: null, stage: "none" })),
    ]);

    expect(ranked.map((item) => item.id)).toEqual([
      "tracked",
      "unknown-first",
      "unknown-second",
    ]);
  });

  it("не мутирует исходный список", () => {
    const rows = [
      row("a", context({ percent_to_stop: "10.00" })),
      row("b", context({ percent_to_stop: "90.00" })),
    ];

    rankAdsByStopProximity(rows);

    expect(rows.map((item) => item.id)).toEqual(["a", "b"]);
  });
});
