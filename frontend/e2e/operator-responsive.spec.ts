import { expect, test } from "@playwright/test";

import { installOperatorHarness } from "./operatorTestHarness";

test.beforeEach(async ({ page }) => {
  await installOperatorHarness(page);
});

test("operator snapshot stays action-first and responsive", async ({ page }, testInfo) => {
  await page.goto("/");

  await expect(
    page.getByRole("heading", { name: "Есть отклонения, требующие решения" }),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "Сканировать" })).toBeVisible();
  const spendChart = page.getByRole("group", {
    name: "Интерактивный график «Накопительный расход»",
  });
  await expect(spendChart).toBeVisible();
  // The chart renderer is code-split on the start route. A visible Suspense
  // placeholder is not acceptance: prove that the real SVG chunk mounted.
  await expect(spendChart.locator("svg")).toBeVisible();
  const renderedLineWidths = await spendChart
    .locator(".recharts-line-curve")
    .evaluateAll((paths) => paths.map((path) => (path as SVGGraphicsElement).getBBox().width));
  expect(renderedLineWidths).toHaveLength(3);
  expect(renderedLineWidths.slice(0, 2).every((width) => width > 100)).toBe(true);
  // The fixture deliberately has a missing actual point between two known
  // values. The line must remain broken, while both confirmed samples stay
  // visible instead of disappearing because they are isolated.
  await expect(spendChart.locator(".recharts-line-dots circle")).toHaveCount(2);
  const markerLabel = spendChart.locator(".operator-current-marker-label");
  await expect(markerLabel).toBeVisible();
  const [chartBox, markerLabelBox] = await Promise.all([
    spendChart.boundingBox(),
    markerLabel.boundingBox(),
  ]);
  if (!chartBox || !markerLabelBox) throw new Error("current marker geometry is unavailable");
  expect(markerLabelBox.x).toBeGreaterThanOrEqual(chartBox.x);
  expect(markerLabelBox.x + markerLabelBox.width).toBeLessThanOrEqual(chartBox.x + chartBox.width);

  await page.getByText("Данные графика", { exact: true }).click();
  await expect(page.getByRole("table", { name: "Накопительный расход по времени" })).toBeVisible();

  const overflow = await page.evaluate(() =>
    Math.max(0, document.documentElement.scrollWidth - document.documentElement.clientWidth),
  );
  expect(overflow).toBeLessThanOrEqual(1);

  if (
    testInfo.project.name === "360px" ||
    testInfo.project.name === "390px" ||
    testInfo.project.name === "430px"
  ) {
    const mobileNav = page.getByRole("navigation", { name: "Основная мобильная навигация" });
    await expect(mobileNav).toBeVisible();
    await expect(mobileNav).toContainText("Сейчас");
    await expect(mobileNav).toContainText("Действия");

    await page.goto("/analytics");
    const sevenDay = page.getByRole("button", { name: "7d" });
    const sevenDayBox = await sevenDay.boundingBox();
    if (!sevenDayBox) throw new Error("7d period control geometry is unavailable");
    expect(sevenDayBox.width).toBeGreaterThanOrEqual(44);
    expect(sevenDayBox.height).toBeGreaterThanOrEqual(44);
    await expect(
      page
        .getByRole("navigation", { name: "Основная мобильная навигация" })
        .getByRole("link", { name: "Ещё", current: "page" }),
    ).toBeVisible();
  }
});

test("scan uses the canonical settings route", async ({ page }) => {
  const requestPromise = page.waitForRequest((request) =>
    request.url().endsWith("/api/settings/observer/scan-now"),
  );
  await page.goto("/");
  await page.getByRole("button", { name: "Сканировать" }).click();
  const request = await requestPromise;
  expect(request.method()).toBe("POST");
});

test("attention CTA lands on a real typed ad detail", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("link", { name: "Открыть объявление" }).click();

  await expect(page).toHaveURL(/\/ads\/ad-1$/);
  await expect(page.getByRole("heading", { name: "GH_CR2 · Основное объявление" })).toBeVisible();
  await expect(page.getByText("Meta ID ad-1")).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    ),
  ).toBeLessThanOrEqual(1);
});

test("actions list opens the exact lifecycle instead of a truncated snapshot", async ({ page }) => {
  await page.goto("/actions");
  await expect(page.getByRole("heading", { name: "Действия" })).toBeVisible();
  await page.getByRole("link", { name: /Отключение объявления/ }).click();

  await expect(page).toHaveURL(/\/actions\/1842$/);
  await expect(page.getByRole("heading", { name: "Отключение объявления" })).toBeVisible();
  await expect(page.getByText("Выполняется")).toBeVisible();
});

test("pause command is queued and redirects to its lifecycle", async ({ page }) => {
  const request = page.waitForRequest(
    (candidate) =>
      candidate.method() === "POST" && candidate.url().endsWith("/api/operator/ads/ad-1/pause"),
  );
  await page.goto("/ads");
  await expect(page.getByRole("heading", { name: "Объявления" })).toBeVisible();
  await page.getByRole("button", { name: "Отключить" }).click();
  const dialog = page.getByRole("dialog", { name: "Отключить объявление?" });
  await expect(dialog).toBeVisible();
  await dialog.getByRole("button", { name: "Отключить" }).click();

  const command = await request;
  expect(command.headers()["idempotency-key"]).toBeTruthy();
  await expect(page).toHaveURL(/\/actions\/1842$/);
});
