/**
 * Пороги операторских CSS не должны быть случайными числами: расхождение с
 * шеллом давало реальный баг — на 800px шелл уже десктопный (Tailwind `md:`
 * активен с 768), а дашборд ещё мобильный на своём пороге 820px.
 *
 * Единственное осознанное исключение — сетка дашборда на 768px включительно.
 * 768px это планшет в портрете, где две колонки по 300px+ перестают быть
 * читаемыми, а «требует внимания» обязано идти первым. Инвариант закреплён
 * e2e (operator-responsive.spec.ts, проект 768px), и он важнее симметрии с
 * брейкпоинтом навигации.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

const TAILWIND_MAX_WIDTH_BREAKPOINTS = new Set([
  639, // sm 640
  767, // md 768
  1023, // lg 1024
  1279, // xl 1280
  1535, // 2xl 1536
]);

/** Ширины, разрешённые сверх границ Tailwind, с причиной существования. */
const DELIBERATE_EXCEPTIONS = new Map<number, string>([
  [768, "сетка дашборда: планшет в портрете остаётся одноколоночным, action-first"],
]);

const LEDGER_CSS = join(process.cwd(), "src/features/operator/operator-ledger.css");
const FILES = [LEDGER_CSS, join(process.cwd(), "../packages/operator-ui/src/styles.css")];

function maxWidthBreakpoints(css: string): number[] {
  return [...css.matchAll(/@media \(max-width:\s*(\d+)px\)/g)].map((match) =>
    Number(match[1]),
  );
}

describe("operator layout breakpoints", () => {
  it.each(FILES)("%s switches on Tailwind boundaries or a documented exception", (file) => {
    const found = maxWidthBreakpoints(readFileSync(file, "utf8"));
    expect(found.length).toBeGreaterThan(0);
    // Пороги ниже sm (мелкие телефоны) допустимы: они не спорят с шеллом.
    const shellRelevant = found.filter((width) => width >= 600);
    for (const width of shellRelevant) {
      expect(
        TAILWIND_MAX_WIDTH_BREAKPOINTS.has(width) || DELIBERATE_EXCEPTIONS.has(width),
        `${width}px не совпадает ни с дефолтами Tailwind v4, ни с задокументированным исключением`,
      ).toBe(true);
    }
  });

  it("no longer leaves the 768…820px band with a mismatched layout", () => {
    const css = readFileSync(LEDGER_CSS, "utf8");
    expect(css).not.toContain("max-width: 820px");
    expect(css).not.toContain("max-width: 1120px");
    expect(css).toContain("max-width: 1023px");
  });

  it("keeps the dashboard single-column at 768px so attention stays first", () => {
    const css = readFileSync(LEDGER_CSS, "utf8");
    // Порог обязан включать 768px: на 767px планшет в портрете уезжает в две
    // колонки и «требует внимания» перестаёт быть первым блоком.
    expect(css).toContain("max-width: 768px");
    const gridBreakpoints = maxWidthBreakpoints(css).filter((width) => width >= 600);
    expect(gridBreakpoints).toContain(768);
  });
});
