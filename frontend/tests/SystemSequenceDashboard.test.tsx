import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import DashboardPage from "../src/pages/DashboardPage";
import { buildDashboardHandlers } from "./msw/handlers";
import { server } from "./test-server";
import { renderWithRouter } from "./test-utils";
import systemSequenceContract from "../../tests/fixtures/system_sequence_dashboard.json";

describe("System sequence dashboard", () => {
  // Проверяет, что дашборд показывает тот же контракт данных, который backend отдает после полного worker-сценария.
  it("рендерит сквозной контракт backend сценария без ручного UI прогона", async () => {
    const scanRuns = systemSequenceContract.scanRuns.map((scanRun) => ({
      ...scanRun,
      started_at: "2026-03-20T12:00:00Z",
      finished_at: "2026-03-20T12:00:01Z",
    }));

    server.use(
      ...buildDashboardHandlers({
        health: systemSequenceContract.health,
        ads: systemSequenceContract.ads,
        decisions: systemSequenceContract.decisions,
        watchlist: [],
        actionJobs: [],
        rules: systemSequenceContract.rules,
        offers: systemSequenceContract.offers,
        sessions: systemSequenceContract.sessions,
        serviceSettings: systemSequenceContract.serviceSettings,
        scanRuns,
      }),
    );

    renderWithRouter(<DashboardPage />);

    expect(await screen.findByRole("heading", { name: "Обзор запуска" })).toBeInTheDocument();
    expect(screen.getByText("наблюдение")).toBeInTheDocument();
    expect(screen.getByText("ожидаем запуск")).toBeInTheDocument();
    expect(screen.getByText("DRC_CR2_CR001")).toBeInTheDocument();
    expect(screen.getByText("пауза рекомендована")).toBeInTheDocument();
    expect(screen.getAllByText(/0,38/).length).toBeGreaterThan(0);
    expect(screen.getByRole("heading", { name: "Кампания 1" })).toBeInTheDocument();
    expect(screen.getByText("Сводка запуска")).toBeInTheDocument();
  });
});
