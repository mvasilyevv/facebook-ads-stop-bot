import { expect, test } from "@playwright/test";

import { campaignRunId, installOperatorHarness } from "./operatorTestHarness";

test.beforeEach(async ({ page }) => {
  await installOperatorHarness(page);
});

test("campaign abort is a two-tap pending command with an accessible touch target", async ({
  page,
}) => {
  await page.goto("/campaigns/create");

  // Compact/mobile is action-first and opens run history directly; desktop
  // keeps creation and history as explicit tabs. Exercise the real route in
  // both shells before asserting the shared run controls.
  const historyTab = page.getByRole("button", { name: "История запусков" });
  if (await historyTab.isVisible()) await historyTab.click();

  await page.getByRole("button", { name: "Развернуть детали" }).click();
  await expect(page.getByText("Выполняется", { exact: true })).toBeVisible();
  await expect(
    page.getByText("Задача выполняется. Это ещё не подтверждённый результат."),
  ).toBeVisible();

  const abort = page.getByRole("button", { name: "Запросить остановку" });
  const abortBox = await abort.boundingBox();
  if (!abortBox) throw new Error("campaign abort control geometry is unavailable");
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
    page
      .locator("#main-content")
      .getByRole("status")
      .getByText("Остановка поставлена в очередь. Завершение ещё не подтверждено."),
  ).toBeVisible();
  await expect(page.getByText("Повтор доступен только после")).toBeVisible();

  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    ),
  ).toBeLessThanOrEqual(1);
});
