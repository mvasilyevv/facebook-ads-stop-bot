/**
 * Полоса прогресса money-команды не имеет измеримой доли выполнения:
 * очередь и выполнение не отдают процент готовности. Любая фиксированная
 * ширина у ::after — это выдуманное «выполнено на N%», которое оператор
 * читает как факт. Тест запрещает такую ширину и требует indeterminate.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

const LEDGER_CSS = join(process.cwd(), "src/features/operator/operator-ledger.css");

function progressAfterRule(css: string): string {
  const start = css.indexOf(".ledger-action-item__progress::after {");
  expect(start, "правило .ledger-action-item__progress::after не найдено").toBeGreaterThan(-1);
  const end = css.indexOf("}", start);
  return css.slice(start, end);
}

describe("ledger action progress bar", () => {
  const rule = progressAfterRule(readFileSync(LEDGER_CSS, "utf8"));

  it("never encodes a fixed completion percentage", () => {
    expect(rule).not.toMatch(/width:\s*\d/);
    expect(rule).not.toContain("44%");
  });

  it("renders an indeterminate sweep instead", () => {
    expect(rule).toContain("fbBarSweep");
    expect(rule).toContain("infinite");
  });
});
