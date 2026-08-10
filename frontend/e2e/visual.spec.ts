import { expect, test } from "@playwright/test";

import { installOperatorHarness } from "./operatorTestHarness";

test.beforeEach(async ({ page }) => {
  test.skip(
    process.platform !== "linux" || process.env.VISUAL_BASELINE_PLATFORM !== "linux",
    "Pixel baselines are created and reviewed only on self-hosted Linux Chromium.",
  );
  await installOperatorHarness(page);
});

test("@visual operator dashboard Linux Chromium baseline", async ({ page }) => {
  await page.goto("/");
  await page.evaluate(() => document.fonts.ready);
  await expect(page.getByRole("heading", { name: "Сейчас" })).toBeVisible();
  await expect(page.getByText("Есть отклонения, требующие решения")).toBeVisible();

  await expect(page).toHaveScreenshot("operator-dashboard.png", {
    animations: "disabled",
    caret: "hide",
    fullPage: true,
    scale: "css",
  });
});
