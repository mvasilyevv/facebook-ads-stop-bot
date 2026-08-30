import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

/**
 * Badge на web и на mini — разные компоненты (cva vs ручной cn, touch-target
 * и layout остаются за каждым фронтом), но цвет варианта должен быть один.
 * Раньше у каждого фронта была своя копия таблицы variant→класс, и они
 * разошлись: `pending` был нейтральным на web и акцентным на mini.
 *
 * Гард не запрещает Badge.tsx существовать в двух местах — запрещает второй
 * независимый источник цвета: оба файла обязаны читать
 * BADGE_VARIANT_CLASSES из @fb/shared/tokens/badgeVariants, а не объявлять
 * собственный Record<BadgeVariant, string>.
 */
const ROOT = resolve(__dirname, "../../../..");
const BADGE_FILES = [
  "frontend/src/components/ui/Badge.tsx",
  "frontend-mini/src/components/ui/Badge.tsx",
];

describe("Badge variant→класс читается из общего модуля", () => {
  it.each(BADGE_FILES)("%s импортирует таблицу из @fb/shared", (relativePath) => {
    const contents = readFileSync(resolve(ROOT, relativePath), "utf8");

    expect(contents).toMatch(/@fb\/shared\/tokens\/badgeVariants/);
    expect(contents).toMatch(/BADGE_VARIANT_CLASSES/);
  });

  it.each(BADGE_FILES)(
    "%s не объявляет собственную таблицу variant→класс (VARIANT_STYLES)",
    (relativePath) => {
      const contents = readFileSync(resolve(ROOT, relativePath), "utf8");

      // Раньше здесь был локальный `const VARIANT_STYLES: Record<BadgeVariant, string> = {...}`.
      expect(contents).not.toMatch(/VARIANT_STYLES\s*[:=]/);
    },
  );
});
