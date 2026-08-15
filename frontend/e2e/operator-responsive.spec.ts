import { expect, test } from "@playwright/test";

import { installOperatorHarness } from "./operatorTestHarness";

test.beforeEach(async ({ page }) => {
  await installOperatorHarness(page, { reloginRequired: true });
});

test("operator snapshot stays action-first and responsive", async ({ page }, testInfo) => {
  await page.goto("/");

  await expect(page.getByRole("heading", { name: "Сейчас" })).toBeVisible();
  await expect(page.getByText("Есть отклонения, требующие решения")).toBeVisible();
  await expect(page.getByRole("button", { name: "Повторить скан" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Портфель" })).toBeVisible();
  await expect(page.getByText("$47.80")).toBeVisible();
  await expect(page.getByRole("link", { name: "Открыть кабинет: GH_CR2" })).toBeVisible();

  const layout = await page.locator(".operator-ledger__grid").evaluate((grid) => {
    const rect = (selector: string) => {
      const node = grid.querySelector<HTMLElement>(selector);
      if (!node) throw new Error(`missing ${selector}`);
      const box = node.getBoundingClientRect();
      return { x: box.x, y: box.y };
    };
    return {
      portfolio: rect(".ledger-section--portfolio"),
      attention: rect(".ledger-section--attention"),
      actions: rect(".ledger-section--actions"),
      funnel: rect(".ledger-section--funnel"),
    };
  });
  if (
    testInfo.project.name === "360px" ||
    testInfo.project.name === "390px" ||
    testInfo.project.name === "430px" ||
    testInfo.project.name === "768px"
  ) {
    expect(layout.attention.y).toBeLessThan(layout.portfolio.y);
    expect(layout.portfolio.y).toBeLessThan(layout.actions.y);
    expect(layout.actions.y).toBeLessThan(layout.funnel.y);
  } else {
    expect(Math.abs(layout.portfolio.y - layout.attention.y)).toBeLessThanOrEqual(2);
    expect(layout.portfolio.x).toBeLessThan(layout.attention.x);
  }

  await page.goto("/analytics");
  const spendChart = page.getByRole("group", {
    name: "Интерактивный график «Расход, база и stop-граница»",
  });
  await expect(spendChart).toBeVisible();
  await expect(spendChart.getByRole("application")).toBeVisible();
  const renderedLineWidths = await spendChart
    .locator(".recharts-line-curve")
    .evaluateAll((paths) => paths.map((path) => (path as SVGGraphicsElement).getBBox().width));
  expect(renderedLineWidths).toHaveLength(3);
  expect(renderedLineWidths.slice(1).every((width) => width > 100)).toBe(true);
  await expect(spendChart.locator(".recharts-line-dots circle")).toHaveCount(2);
  await expect(spendChart.getByText("Сейчас", { exact: true })).toBeVisible();

  await spendChart
    .locator("xpath=ancestor::figure")
    .getByText("Данные графика", { exact: true })
    .click();
  await expect(
    page.getByRole("table", {
      name: "Почасовой расход, база, stop и доступность источников",
    }),
  ).toBeVisible();

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

test("cabinet ledger opens a typed cabinet snapshot", async ({ page }) => {
  const requestPromise = page.waitForRequest((request) =>
    request.url().includes("/api/operator/cabinets/123/snapshot"),
  );
  await page.goto("/");
  await page.getByRole("link", { name: "Открыть кабинет: GH_CR2" }).click();

  expect((await requestPromise).method()).toBe("GET");
  await expect(page).toHaveURL(/\/cabinets\/123$/);
  await expect(page.getByRole("heading", { name: "GH_CR2" })).toBeVisible();
  await expect(page.getByText("$ · Africa/Accra · контроль кабинета")).toBeVisible();
});

test("re-login recovery uses the canonical operator command", async ({ page }) => {
  const requestPromise = page.waitForRequest((request) =>
    request.url().endsWith("/api/operator/scan/retry"),
  );
  await page.goto("/");
  await page.getByRole("button", { name: "Повторить скан" }).click();
  const request = await requestPromise;
  expect(request.method()).toBe("POST");
  expect(request.headers()["idempotency-key"]).toBeTruthy();
});

test("attention CTA lands on a real typed ad detail", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("link", { name: "Открыть объявление" }).click();

  await expect(page).toHaveURL(/\/ads\/ad-1$/);
  await expect(page.getByRole("heading", { name: "GH_CR2 · Основное объявление" })).toBeVisible();
  await expect(page.getByText("Идентификатор объявления в Meta: ad-1")).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    ),
  ).toBeLessThanOrEqual(1);
});

test("actions list opens the exact lifecycle instead of a truncated snapshot", async ({ page }) => {
  await page.goto("/actions");
  await expect(page.getByRole("heading", { name: "Действия" })).toBeVisible();
  await page.getByRole("link", { name: "Открыть действие" }).click();

  await expect(page).toHaveURL(/\/actions\/1842$/);
  await expect(page.getByRole("heading", { name: "Отключение объявления" })).toBeVisible();
  await expect(page.getByText("Выполняется", { exact: true })).toBeVisible();
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
