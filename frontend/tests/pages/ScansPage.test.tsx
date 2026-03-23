import { screen } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import ScansPage from "../../src/pages/ScansPage";
import type { ScanRunItem } from "../../src/types";
import { server } from "../test-server";
import { renderWithRouter } from "../test-utils";

describe("ScansPage", () => {
  // Проверяет, что идентификаторы сканов и профилей отображаются компактно, а сводка скана видна в таблице.
  it("показывает компактные id и summary последнего скана", async () => {
    const scans: ScanRunItem[] = [
      {
        id: "695f55e9-f20e-42d1-9cfc-015bba157996",
        browser_host_id: "vision-3030",
        profile_id: "2a39059a-f41e-4bad-a081-216733388913",
        status: "SUCCEEDED",
        rows_seen: 42,
        rows_parsed: 42,
        scope_summary: {
          rows_in_scope: 42,
          rows_not_seen_this_scan: 0,
          active_rows: 0,
          paused_rows: 42,
        },
        error_message: null,
        started_at: "2026-03-22T13:20:09.577311Z",
        finished_at: "2026-03-22T13:20:11.577311Z",
      },
    ];

    server.use(
      http.get("*/scan-runs", () => HttpResponse.json(scans)),
    );

    renderWithRouter(<ScansPage />);

    expect(await screen.findByRole("heading", { name: "Сканирование объявлений" })).toBeInTheDocument();
    expect(screen.getByTitle("695f55e9-f20e-42d1-9cfc-015bba157996")).toHaveTextContent("695f55e9...157996");
    expect(screen.getByTitle("2a39059a-f41e-4bad-a081-216733388913")).toHaveTextContent("2a39059a...388913");
    expect(screen.getByText("В охвате")).toBeInTheDocument();
    expect(screen.getByText("В охвате").parentElement).toHaveTextContent("42");
    expect(screen.getByText("succeeded")).toBeInTheDocument();
  });

  // Проверяет, что ошибка загрузки сканов показывается пользователю и не скрывает пустое состояние.
  it("показывает ошибку загрузки сканов", async () => {
    server.use(
      http.get("*/scan-runs", () =>
        HttpResponse.json({ message: "Журнал сканов временно недоступен" }, { status: 503 }),
      ),
    );

    renderWithRouter(<ScansPage />);

    expect(await screen.findByRole("heading", { name: "Сканирование объявлений" })).toBeInTheDocument();
    expect(screen.getByText("Журнал сканов временно недоступен")).toBeInTheDocument();
    expect(screen.getByText("Сканов не загружено")).toBeInTheDocument();
  });
});
