/**
 * Пороги операторских CSS обязаны совпадать с дефолтами Tailwind v4
 * (md 768, lg 1024). Расхождение давало реальный баг: на 800px шелл ещё
 * десктопный (Tailwind `md:` активен с 768), а дашборд уже переключился
 * в мобильную раскладку на своём пороге 820px.
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

const FILES = [
  join(process.cwd(), "src/features/operator/operator-ledger.css"),
  join(process.cwd(), "../packages/operator-ui/src/styles.css"),
];

function maxWidthBreakpoints(css: string): number[] {
  return [...css.matchAll(/@media \(max-width:\s*(\d+)px\)/g)].map((match) =>
    Number(match[1]),
  );
}

describe("operator layout breakpoints", () => {
  it.each(FILES)("%s only switches on Tailwind boundaries", (file) => {
    const found = maxWidthBreakpoints(readFileSync(file, "utf8"));
    expect(found.length).toBeGreaterThan(0);
    // Пороги ниже sm (мелкие телефоны) допустимы: они не спорят с шеллом.
    const shellRelevant = found.filter((width) => width >= 600);
    for (const width of shellRelevant) {
      expect(
        TAILWIND_MAX_WIDTH_BREAKPOINTS.has(width),
        `${width}px не совпадает с дефолтами Tailwind v4`,
      ).toBe(true);
    }
  });

  it("no longer leaves the 768…820px band with a mismatched layout", () => {
    const css = readFileSync(FILES[0]!, "utf8");
    expect(css).not.toContain("max-width: 820px");
    expect(css).not.toContain("max-width: 1120px");
    expect(css).toContain("max-width: 767px");
    expect(css).toContain("max-width: 1023px");
  });
});
