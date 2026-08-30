/** Временный обход экранов TMA ради скриншотов: не проверка, а инвентарь. */
import { test } from "@playwright/test";

import { installOperatorHarness } from "./operatorTestHarness";

const SCREENS: Array<{ slug: string; path: string }> = [
  { slug: "01-dashboard", path: "./" },
  { slug: "02-ads", path: "./ads" },
  { slug: "03-ad-card", path: "./ads/ad-1" },
  { slug: "04-actions", path: "./actions" },
  { slug: "05-action-card", path: "./actions/action-1" },
  { slug: "06-campaigns", path: "./campaigns" },
  { slug: "07-campaign-create", path: "./campaigns/create" },
  { slug: "08-campaign-presets", path: "./campaigns/presets" },
  { slug: "09-offers", path: "./offers" },
  { slug: "10-analytics", path: "./analytics" },
  { slug: "11-cabinet-card", path: "./cabinets/123" },
  { slug: "12-incidents", path: "./incidents" },
  { slug: "13-system-sources", path: "./system/sources" },
  { slug: "14-desktop", path: "./desktop" },
  { slug: "15-settings", path: "./settings" },
];

test.beforeEach(async ({ page }) => {
  await installOperatorHarness(page, { telegram: true });
});

for (const screen of SCREENS) {
  test(`@tma @audit ${screen.slug}`, async ({ page }, testInfo) => {
    await page.goto(screen.path);
    await page.evaluate(() => document.fonts.ready);
    await page.waitForTimeout(1200);
    await page.screenshot({
      path: `e2e/__audit__/${testInfo.project.name}/${screen.slug}.png`,
      fullPage: true,
      animations: "disabled",
      caret: "hide",
    });
  });
}
