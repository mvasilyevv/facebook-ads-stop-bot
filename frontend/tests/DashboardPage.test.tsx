import { screen } from "@testing-library/react";
import DashboardPage from "../src/pages/DashboardPage";
import { server } from "./msw/server";
import { buildDashboardHandlers } from "./msw/handlers";
import { renderWithRouter } from "./test-utils";
import { afterEach, vi } from "vitest";

afterEach(() => {
  vi.restoreAllMocks();
});

// Проверяет, что dashboard загружается через API-моки и показывает ключевые статусы и карточки.
test("DashboardPage renders loaded dashboard data and summary states", async () => {
  vi.spyOn(Date, "now").mockReturnValue(new Date("2026-03-22T10:20:00Z").getTime());

  server.use(
    ...buildDashboardHandlers({
      health: {
        status: "healthy",
        service: "frontend",
        environment: "test",
        database_status: "ok",
        timestamp: "2026-03-22T10:00:00Z",
      },
      ads: [
        {
          fb_ad_id: "120241420867510176",
          campaign_name: "CR2 | DRC | MV | NEW | pwa.partners | 15.03",
          adset_name: "2",
          ad_name: "DRC_CR2_CR014",
          delivery_status: "ACTIVE",
          tracking_mode: "TRACKED",
          scope_presence: "IN_SCOPE",
          last_decision: "NO_ACTION",
          spend: "0.50",
          clicks: 7,
          cpc: "0.07",
          leads: 1,
          cost_per_lead: "0.50",
          registrations: 0,
          cost_per_registration: "0.00",
          deposits: 0,
          resolved_cpa_usd: "5.00",
          last_seen_at: "2026-03-22T10:05:00Z",
        },
        {
          fb_ad_id: "120241420867520176",
          campaign_name: "CR2 | DRC | MV | NEW | pwa.partners | 15.03",
          adset_name: "1",
          ad_name: "DRC_CR2_CR015",
          delivery_status: "PAUSED",
          tracking_mode: "TRACKED",
          scope_presence: "IN_SCOPE",
          last_decision: "WOULD_PAUSE",
          last_decision_reason: "CPA выше порога",
          last_execution_state: "SKIPPED_BY_MODE",
          spend: "1.50",
          clicks: 17,
          cpc: "0.09",
          leads: 2,
          cost_per_lead: "0.75",
          registrations: 1,
          cost_per_registration: "1.50",
          deposits: 0,
          resolved_cpa_usd: "5.00",
          last_seen_at: "2026-03-22T10:10:00Z",
        },
      ],
      decisions: [],
      rules: [],
      offers: [],
      sessions: [
        {
          profile_id: "profile-1",
          browser_host_id: "vision-3030",
          status: "ACTIVE",
          cdp_url: "http://127.0.0.1:17288",
          webdriver_url: null,
          last_started_at: "2026-03-22T09:55:00Z",
          last_stopped_at: null,
          last_message: "Сессия активна",
        },
      ],
      serviceSettings: {
        auto_pause_enabled: true,
        auto_resume_enabled: false,
        auto_resume_available: false,
        observe_only_enabled: false,
        scan_interval_seconds: 60,
        vision_local_api_url: "http://127.0.0.1:3030",
        vision_cloud_api_url: "https://v1.empr.cloud/api/v1",
        telegram_chat_id: "777000",
        vision_api_token_masked: "••••oken",
        telegram_bot_token_masked: "••••oken",
        vision_api_token_configured: true,
        telegram_bot_token_configured: true,
        updated_at: "2026-03-22T10:15:00Z",
      },
      scanRuns: [
        {
          id: "scan-run-1",
          browser_host_id: "vision-3030",
          profile_id: "profile-1",
          status: "SUCCEEDED",
          rows_seen: 42,
          rows_parsed: 42,
          scope_summary: {
            rows_in_scope: 42,
            rows_not_seen_this_scan: 0,
          },
          error_message: null,
          started_at: "2026-03-22T10:19:20Z",
          finished_at: "2026-03-22T10:19:30Z",
        },
      ],
    }),
  );

  renderWithRouter(
    <DashboardPage />,
  );

  expect(await screen.findByRole("heading", { name: "Обзор системы" })).toBeInTheDocument();
  expect(screen.getByText("боевой")).toBeInTheDocument();
  expect(screen.getByText("60 секунд")).toBeInTheDocument();
  expect(screen.getByText("00:30")).toBeInTheDocument();
  expect(screen.getByText("включена")).toBeInTheDocument();
  expect(screen.getByText("frontend")).toBeInTheDocument();
  expect(screen.getByText("healthy")).toBeInTheDocument();
  expect(
    screen.getByRole("heading", { name: "CR2 | DRC | MV | NEW | pwa.partners | 15.03" }),
  ).toBeInTheDocument();
  expect(screen.getByText("действие не выполнено")).toBeInTheDocument();
  expect(screen.getByText("DRC_CR2_CR014")).toBeInTheDocument();
});
