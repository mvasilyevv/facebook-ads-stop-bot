import { expect, test } from "@playwright/test";

import { installOperatorHarness } from "./operatorTestHarness";

test.beforeEach(async ({ page }) => {
  await installOperatorHarness(page, { reloginRequired: true });
});

test("360px reflows without horizontal page scroll", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "360px");

  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Сейчас" })).toBeVisible();
  await expect(page.getByText("Есть отклонения, требующие решения")).toBeVisible();
  await expect(
    page.getByRole("navigation", { name: "Основная мобильная навигация" }),
  ).toBeVisible();
  await expectNoHorizontalPageScroll(page);
});

test("200% layout scale keeps the critical operator surface usable", async ({
  browserName,
  page,
}, testInfo) => {
  test.skip(browserName !== "chromium" || testInfo.project.name !== "1280px");

  // At 200% browser zoom a 1280px physical viewport exposes roughly 640 CSS
  // pixels. Device metrics exercise the same responsive layout boundary while
  // preserving a 2x device scale for deterministic headless acceptance.
  const cdp = await page.context().newCDPSession(page);
  await cdp.send("Emulation.setDeviceMetricsOverride", {
    width: 640,
    height: 900,
    screenWidth: 1280,
    screenHeight: 1800,
    deviceScaleFactor: 2,
    mobile: false,
  });

  await page.goto("/");
  expect(await page.evaluate(() => window.innerWidth)).toBe(640);
  await expect(page.getByRole("button", { name: "Повторить скан" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Открыть объявление" })).toBeVisible();
  await expectNoHorizontalPageScroll(page);
});

test("reduced-motion removes non-essential animation and transitions", async ({
  page,
}, testInfo) => {
  test.skip(testInfo.project.name !== "1280px");

  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/");

  const motion = await page
    .locator(".ledger-action-item__progress")
    .first()
    .evaluate((node) => {
      const style = getComputedStyle(node);
      return {
        animationDuration: style.animationDuration,
        animationIterations: style.animationIterationCount,
      };
    });
  expect(toMilliseconds(motion.animationDuration)).toBeLessThanOrEqual(0.011);
  expect(motion.animationIterations).toBe("1");

  const transitionDuration = await page
    .getByRole("button", { name: "Повторить скан" })
    .evaluate((node) => getComputedStyle(node).transitionDuration);
  expect(toMilliseconds(transitionDuration)).toBeLessThanOrEqual(0.011);
});

test("forced-colors retains text status and a visible keyboard focus indicator", async ({
  browserName,
  page,
}, testInfo) => {
  test.skip(browserName !== "chromium" || testInfo.project.name !== "1280px");

  await page.emulateMedia({ forcedColors: "active" });
  await page.goto("/");

  const scan = page.getByRole("button", { name: "Повторить скан" });
  await scan.focus();
  const focus = await scan.evaluate((node) => {
    const style = getComputedStyle(node);
    return {
      outlineStyle: style.outlineStyle,
      outlineWidth: Number.parseFloat(style.outlineWidth),
    };
  });
  expect(focus.outlineStyle).not.toBe("none");
  expect(focus.outlineWidth).toBeGreaterThanOrEqual(2);

  // Status remains icon + label; color is never its sole carrier.
  await expect(page.getByText("Требует внимания", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("Есть отклонения, требующие решения")).toBeVisible();
});

test("critical ad command is operable by keyboard with trapped and restored focus", async ({
  page,
}, testInfo) => {
  test.skip(testInfo.project.name !== "1280px");

  await page.goto("/ads");

  const skipLink = page.getByRole("link", { name: "Перейти к содержимому" });
  await page
    .getByRole("navigation", { name: "Основная навигация" })
    .getByRole("link", { name: "Сейчас" })
    .focus();
  await page.keyboard.press("Shift+Tab");
  await expect(skipLink).toBeFocused();
  await expect(skipLink).toBeVisible();
  await page.keyboard.press("Enter");
  await expect(page.locator("#main-content")).toBeFocused();

  const trigger = page.getByRole("button", { name: "Отключить" });
  await trigger.focus();
  await page.keyboard.press("Enter");

  const dialog = page.getByRole("dialog", { name: "Отключить объявление?" });
  await expect(dialog).toBeVisible();
  await expect(dialog.locator(":focus")).toHaveCount(1);
  for (let index = 0; index < 4; index += 1) {
    await page.keyboard.press("Tab");
    expect(await dialog.locator(":focus").count()).toBe(1);
  }

  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await expect(trigger).toBeFocused();

  const commandRequest = page.waitForRequest(
    (request) =>
      request.method() === "POST" && request.url().endsWith("/api/operator/ads/ad-1/pause"),
  );
  await page.keyboard.press("Enter");
  await expect(dialog).toBeVisible();
  const confirm = dialog.getByRole("button", { name: "Отключить" });
  await confirm.focus();
  await page.keyboard.press("Enter");

  expect((await commandRequest).headers()["idempotency-key"]).toBeTruthy();
  await expect(page).toHaveURL(/\/actions\/1842$/);
});

async function expectNoHorizontalPageScroll(page: import("@playwright/test").Page) {
  await expect
    .poll(() =>
      page.evaluate(() =>
        Math.max(0, document.documentElement.scrollWidth - document.documentElement.clientWidth),
      ),
    )
    .toBeLessThanOrEqual(1);
}

function toMilliseconds(durationList: string): number {
  return Math.max(
    ...durationList.split(",").map((raw) => {
      const value = raw.trim();
      if (value.endsWith("ms")) return Number.parseFloat(value);
      if (value.endsWith("s")) return Number.parseFloat(value) * 1_000;
      return Number.POSITIVE_INFINITY;
    }),
  );
}
