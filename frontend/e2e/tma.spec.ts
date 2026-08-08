import { expect, test } from "@playwright/test";

import { campaignRunId, installOperatorHarness } from "./operatorTestHarness";

test.beforeEach(async ({ page }) => {
  await installOperatorHarness(page, { telegram: true });
});

test("@tma initializes always-dark shell and applies every Telegram safe area", async ({
  page,
}) => {
  await page.goto("./");

  await expect(page.getByRole("heading", { name: "Сейчас" })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Навигация" })).toBeVisible();
  await expect(page.getByText("FB Agent · оператор")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Портфель" })).toBeVisible();
  await expect(page.getByText("$47.80")).toBeVisible();
  await expect(
    page.getByRole("group", {
      name: "Интерактивный график «Накопительный расход»",
    }),
  ).toHaveCount(0);

  const sectionOrder = await page.locator(".mini-ledger-section > header h2").allTextContents();
  expect(sectionOrder).toEqual(["Требует внимания", "Портфель", "Действия", "Воронка"]);

  const shell = await page.locator("main").evaluate((node) => {
    const style = getComputedStyle(node);
    return {
      minHeight: style.minHeight,
      paddingTop: style.paddingTop,
      paddingRight: style.paddingRight,
      paddingBottom: style.paddingBottom,
      paddingLeft: style.paddingLeft,
    };
  });
  expect(shell).toEqual({
    minHeight: "700px",
    paddingTop: "20px",
    paddingRight: "21px",
    paddingBottom: "78px",
    paddingLeft: "23px",
  });

  const telegramState = await page.evaluate(() => {
    const root = document.documentElement;
    const harness = (
      window as unknown as {
        __tmaHarness: { readyCalls: number; expandCalls: number };
      }
    ).__tmaHarness;
    return {
      theme: root.dataset.theme,
      colorScheme: root.style.colorScheme,
      readyCalls: harness.readyCalls,
      expandCalls: harness.expandCalls,
    };
  });
  expect(telegramState).toEqual({
    theme: "dark",
    colorScheme: "dark",
    readyCalls: 1,
    expandCalls: 1,
  });

  const tabTargets = await page
    .getByRole("navigation", { name: "Навигация" })
    .getByRole("button")
    .evaluateAll((buttons) =>
      buttons.map((button) => {
        const box = button.getBoundingClientRect();
        return { width: box.width, height: box.height };
      }),
    );
  expect(tabTargets.every(({ width, height }) => width >= 44 && height >= 44)).toBe(true);
  await expectNoHorizontalPageScroll(page);
});

test("@tma activation refreshes stable viewport and content safe-area insets", async ({ page }) => {
  await page.goto("./");

  // React registers Telegram listeners from AuthGuard's mount effect. Wait for
  // that public onEvent contract instead of racing the effect after page load.
  await expect
    .poll(() =>
      page.evaluate(
        () =>
          typeof (
            window as unknown as {
              __tmaHarness: {
                events: Partial<Record<string, () => void>>;
              };
            }
          ).__tmaHarness.events.activated,
      ),
    )
    .toBe("function");

  const updated = await page.evaluate(() => {
    const scopedWindow = window as unknown as {
      __tmaHarness: {
        events: Partial<Record<string, () => void>>;
      };
      Telegram: {
        WebApp: {
          viewportStableHeight: number;
          contentSafeAreaInset: { top: number; right: number; bottom: number; left: number };
        };
      };
    };
    const activate = scopedWindow.__tmaHarness.events.activated;
    if (!activate) throw new Error("Telegram activated listener was not registered");
    scopedWindow.Telegram.WebApp.viewportStableHeight = 640;
    scopedWindow.Telegram.WebApp.contentSafeAreaInset = {
      top: 30,
      right: 31,
      bottom: 32,
      left: 33,
    };
    activate();
    const root = document.documentElement.style;
    return {
      height: root.getPropertyValue("--tg-viewport-stable-height"),
      top: root.getPropertyValue("--tg-content-safe-top"),
      right: root.getPropertyValue("--tg-content-safe-right"),
      bottom: root.getPropertyValue("--tg-content-safe-bottom"),
      left: root.getPropertyValue("--tg-content-safe-left"),
    };
  });

  expect(updated).toEqual({
    height: "640px",
    top: "30px",
    right: "31px",
    bottom: "32px",
    left: "33px",
  });
});

test("@tma action route registers native BackButton and returns to dashboard", async ({ page }) => {
  await page.goto("./");
  await page.getByRole("button", { name: "Открыть объявление" }).click();

  // The raw Meta ID stays out of the URL; /open renders the transient target.
  await expect(page).toHaveURL(/\/tma\/open$/);
  await expect(page.getByRole("heading", { name: "GH_CR2 · Основное объявление" })).toBeVisible();
  const navigation = page.getByRole("navigation", { name: "Навигация" });
  await expect(navigation).toBeVisible();
  await expect(navigation.getByRole("button", { name: "Сейчас" })).toBeVisible();
  expect(
    await page.evaluate(
      () =>
        (
          window as unknown as {
            __tmaHarness: { backShowCalls: number };
          }
        ).__tmaHarness.backShowCalls,
    ),
  ).toBeGreaterThan(0);

  await page.evaluate(() => {
    (
      window as unknown as {
        __tmaHarness: { backHandler: (() => void) | null };
      }
    ).__tmaHarness.backHandler?.();
  });
  await expect(page).toHaveURL(/\/tma\/$/);
  await expect(page.getByRole("heading", { name: "Сейчас" })).toBeVisible();
});

test("@tma opens the typed cabinet ledger and uses native back navigation", async ({ page }) => {
  await page.goto("./");

  const cabinetRequest = page.waitForRequest((request) =>
    request.url().includes("/api/operator/cabinets/123/snapshot"),
  );
  await page.getByRole("button", { name: /GH_CR2/ }).click();
  await cabinetRequest;

  await expect(page).toHaveURL(/\/tma\/cabinets\/123$/);
  await expect(page.getByRole("heading", { name: "GH_CR2" })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Навигация" })).toHaveCount(0);
  expect(
    await page.evaluate(
      () =>
        (
          window as unknown as {
            __tmaHarness: { backShowCalls: number };
          }
        ).__tmaHarness.backShowCalls,
    ),
  ).toBeGreaterThan(0);
  await expectNoHorizontalPageScroll(page);
});

test("@tma campaign abort stays pending and uses the same two-tap lifecycle", async ({ page }) => {
  await page.goto("./campaigns");

  await page.getByRole("button", { name: "Открыть запуск #11111111" }).click();
  await expect(page.getByText("Выполняется", { exact: true })).toBeVisible();
  const abort = page.getByRole("button", { name: "Запросить остановку" });
  const abortBox = await abort.boundingBox();
  if (!abortBox) throw new Error("TMA campaign abort geometry is unavailable");
  expect(abortBox.height).toBeGreaterThanOrEqual(44);
  expect(abortBox.width).toBeGreaterThanOrEqual(44);

  const requestPromise = page.waitForRequest(
    (request) =>
      request.method() === "POST" &&
      request.url().endsWith(`/api/tools/campaigns/runs/${campaignRunId}/abort`),
  );
  await abort.click();
  const request = await requestPromise;
  expect(request.headers()["idempotency-key"]).toMatch(/^[0-9a-f-]{36}$/i);
  await expect(
    page.getByText("Остановка поставлена в очередь. Завершение ещё не подтверждено."),
  ).toBeVisible();
  await expectNoHorizontalPageScroll(page);
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
