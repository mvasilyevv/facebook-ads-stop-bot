import { describe, expect, it } from "vitest";

import { BADGE_VARIANT_CLASSES, BADGE_VARIANTS } from "./badgeVariants";

describe("BADGE_VARIANT_CLASSES", () => {
  it("покрывает все объявленные варианты", () => {
    for (const variant of BADGE_VARIANTS) {
      expect(BADGE_VARIANT_CLASSES[variant].surface).toBeTruthy();
      expect(BADGE_VARIANT_CLASSES[variant].dot).toBeTruthy();
    }
  });

  // pending — «поставлено в очередь», то есть ожидание, а не что-то
  // выделенное вниманием оператора. Раньше frontend-mini красил его в
  // accent (bg-accent-bg/text-accent-muted), из-за чего очередь читалась
  // как выделенная. Канон — нейтральный тон, как у "neutral"/"normal".
  it("pending — нейтральный тон, а не акцент", () => {
    expect(BADGE_VARIANT_CLASSES.pending.surface).not.toMatch(/accent/);
    expect(BADGE_VARIANT_CLASSES.pending.dot).not.toMatch(/accent/);
  });
});
