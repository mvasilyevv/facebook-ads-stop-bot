import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

/**
 * Моноширинный шрифт принадлежит измеренному: числам, деньгам, долям,
 * отметкам времени и идентификаторам, которые оператор копирует. Названия,
 * проза, перечисления и слова состояния набираются основным шрифтом.
 *
 * Обход интерфейса 15.08.2026 показал, чем это кончается без правила: моно
 * оказался на перечислениях источников и на словах «не подтверждено», экран
 * читался распечаткой лога, а настоящие числа перестали выделяться.
 *
 * Список ниже — исчерпывающий. Новый моноширинный селектор требует записи
 * здесь, то есть осознанного ответа «да, это измеренная величина».
 */
const MEASURED_SELECTORS = [
  ".ledger-proof-stamp__time", // отметка времени
  ".ledger-section__meta time", // отметка времени
  ".ledger-section__meta [data-numeric]", // явно помеченное число
  ".ledger-total dd", // сумма
  ".ledger-axis", // подписи шкалы: числа и время
  ".ledger-scale__value", // измеренное значение
  ".ledger-attention-item__count", // счётчик
  ".ledger-funnel__value", // число воронки
  ".ledger-funnel__meta", // CR и деньги
];

const LEDGER_CSS = resolve(process.cwd(), "src/features/operator/operator-ledger.css");

/** Селекторы, на которых стоит var(--font-numeric). */
function monospacedSelectors(css: string): string[] {
  const withoutComments = css.replace(/\/\*[\s\S]*?\*\//g, "");
  const found: string[] = [];

  for (const block of withoutComments.split("}")) {
    if (!block.includes("var(--font-numeric)")) continue;
    const head = block.slice(0, block.indexOf("{"));
    for (const selector of head.split(",")) {
      const cleaned = selector.split(/\s+/).filter(Boolean).join(" ");
      if (cleaned) found.push(cleaned);
    }
  }

  return found;
}

describe("моноширинный шрифт в операторском леджере", () => {
  const css = readFileSync(LEDGER_CSS, "utf8");

  it("стоит только на измеренных величинах", () => {
    const unexpected = monospacedSelectors(css).filter(
      (selector) => !MEASURED_SELECTORS.includes(selector),
    );

    expect(unexpected, "моноширинный шрифт на тексте, который не является величиной").toEqual([]);
  });

  it("покрывает каждую заявленную величину — список не устарел", () => {
    const actual = monospacedSelectors(css);

    for (const selector of MEASURED_SELECTORS) {
      expect(actual, `${selector} больше не набран моноширинным`).toContain(selector);
    }
  });
});
