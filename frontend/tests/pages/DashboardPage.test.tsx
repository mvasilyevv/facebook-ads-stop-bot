import { screen } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import DashboardPage from "../../src/pages/DashboardPage";
import type {
  AdSummary,
  BrowserSessionItem,
  DecisionItem,
  OfferItem,
  RuleItem,
  ScanRunItem,
  ServiceSettingsResponse,
} from "../../src/types";
import { server } from "../test-server";
import { renderWithRouter } from "../test-utils";

function buildAdSummary(overrides: Partial<AdSummary>): AdSummary {
  return {
    fb_ad_id: "120241420000000001",
    campaign_name: "CR2 | DRC | MV | UPD MZ | pwa.partners | 15.03",
    adset_name: "1",
    ad_name: "DRC_CR2_CR001",
    delivery_status: "PAUSED",
    tracking_mode: "TRACKED",
    scope_presence: "IN_SCOPE",
    last_seen_at: "2026-03-22T11:20:20.912202Z",
    last_decision: "NO_ACTION",
    resolved_cpa_usd: "5.00",
    spend: "0.00",
    clicks: 0,
    cpc: "0.00",
    leads: 0,
    cost_per_lead: "0.00",
    registrations: 0,
    cost_per_registration: "0.00",
    deposits: 0,
    ...overrides,
  };
}

function mockDashboardEndpoints(
  ads: AdSummary[],
  scanRuns: ScanRunItem[] = [],
  serviceSettingsOverrides?: Partial<ServiceSettingsResponse>,
) {
  const decisions: DecisionItem[] = [];
  const rules: RuleItem[] = [];
  const offers: OfferItem[] = [];
  const sessions: BrowserSessionItem[] = [];
  const serviceSettings: ServiceSettingsResponse = {
    auto_pause_enabled: true,
    auto_resume_enabled: false,
    auto_resume_available: false,
    observe_only_enabled: false,
    scan_interval_seconds: 60,
    vision_local_api_url: "http://127.0.0.1:3030",
    vision_cloud_api_url: "https://vision.example/api",
    telegram_chat_id: "777000",
    vision_api_token_masked: "••••oken",
    telegram_bot_token_masked: "••••oken",
    vision_api_token_configured: true,
    telegram_bot_token_configured: true,
    updated_at: "2026-03-22T11:30:00.000Z",
    ...serviceSettingsOverrides,
  };

  server.use(
    http.get("*/health", () =>
      HttpResponse.json({
        status: "ok",
        service: "facebook-ads-stop-bot",
        environment: "test",
        database_status: "healthy",
        timestamp: "2026-03-22T11:30:00.000Z",
      }),
    ),
    http.get("*/ads", () => HttpResponse.json(ads)),
    http.get("*/decisions", () => HttpResponse.json(decisions)),
    http.get("*/rules", () => HttpResponse.json(rules)),
    http.get("*/offers", () => HttpResponse.json(offers)),
    http.get("*/sessions", () => HttpResponse.json(sessions)),
    http.get("*/scan-runs", () => HttpResponse.json(scanRuns)),
    http.get("*/settings/service", () => HttpResponse.json(serviceSettings)),
  );
}

describe("DashboardPage", () => {
  // Проверяет, что обзор не обрезает четвертое объявление внутри одного adset.
  it("показывает все объявления adset без искусственного лимита карточек", async () => {
    mockDashboardEndpoints([
      buildAdSummary({ fb_ad_id: "120241420000000005", ad_name: "DRC_CR2_CR005", spend: "1.54" }),
      buildAdSummary({ fb_ad_id: "120241420000000006", ad_name: "DRC_CR2_CR006", spend: "0.43" }),
      buildAdSummary({ fb_ad_id: "120241420000000007", ad_name: "DRC_CR2_CR007", spend: "0.18" }),
      buildAdSummary({ fb_ad_id: "120241420000000008", ad_name: "DRC_CR2_CR008", spend: "0.00" }),
    ]);

    renderWithRouter(<DashboardPage />);

    expect(await screen.findByText("DRC_CR2_CR005")).toBeInTheDocument();
    expect(screen.getByText("DRC_CR2_CR006")).toBeInTheDocument();
    expect(screen.getByText("DRC_CR2_CR007")).toBeInTheDocument();
    expect(screen.getByText("DRC_CR2_CR008")).toBeInTheDocument();
  });

  // Проверяет, что обзор не скрывает кампании и adset после удаления лимитов превью.
  it("показывает все кампании и adset в плиточном обзоре", async () => {
    mockDashboardEndpoints([
      buildAdSummary({
        fb_ad_id: "120241420000000101",
        campaign_name: "Кампания 1",
        adset_name: "1",
        ad_name: "Объявление 1-1",
        spend: "10.00",
      }),
      buildAdSummary({
        fb_ad_id: "120241420000000102",
        campaign_name: "Кампания 1",
        adset_name: "2",
        ad_name: "Объявление 1-2",
        spend: "9.00",
      }),
      buildAdSummary({
        fb_ad_id: "120241420000000103",
        campaign_name: "Кампания 2",
        adset_name: "1",
        ad_name: "Объявление 2-1",
        spend: "8.00",
      }),
      buildAdSummary({
        fb_ad_id: "120241420000000104",
        campaign_name: "Кампания 3",
        adset_name: "1",
        ad_name: "Объявление 3-1",
        spend: "7.00",
      }),
      buildAdSummary({
        fb_ad_id: "120241420000000105",
        campaign_name: "Кампания 4",
        adset_name: "1",
        ad_name: "Объявление 4-1",
        spend: "6.00",
      }),
    ]);

    renderWithRouter(<DashboardPage />);

    expect(await screen.findByText("Объявление 1-1")).toBeInTheDocument();
    expect(screen.getByText("Объявление 1-2")).toBeInTheDocument();
    expect(screen.getByText("Объявление 2-1")).toBeInTheDocument();
    expect(screen.getByText("Объявление 3-1")).toBeInTheDocument();
    expect(screen.getByText("Объявление 4-1")).toBeInTheDocument();
    expect(screen.getAllByText("Кампания 4").length).toBeGreaterThan(0);
  });

  // Проверяет, что вместо зависшего "сейчас" дашборд честно показывает ожидание нового запуска после просроченного таймера.
  it("показывает ожидание запуска, если расчётное время следующего скана уже прошло", async () => {
    mockDashboardEndpoints(
      [buildAdSummary({ ad_name: "DRC_CR2_CR010" })],
      [
        {
          id: "scan-run-finished",
          browser_host_id: "vision-3030",
          profile_id: "profile-1",
          status: "SUCCEEDED",
          rows_seen: 51,
          rows_parsed: 51,
          scope_summary: null,
          error_message: null,
          started_at: "2026-03-22T10:00:00.000Z",
          finished_at: "2026-03-22T10:00:10.000Z",
        },
      ],
    );

    renderWithRouter(<DashboardPage />);

    expect(await screen.findByText("ожидаем запуск")).toBeInTheDocument();
  });

  // Проверяет, что активный scan run показывается как выполняющийся, а не как нулевой countdown.
  it("показывает выполняется, когда scan run уже запущен", async () => {
    mockDashboardEndpoints(
      [buildAdSummary({ ad_name: "DRC_CR2_CR011" })],
      [
        {
          id: "scan-run-running",
          browser_host_id: "vision-3030",
          profile_id: "profile-1",
          status: "RUNNING",
          rows_seen: 0,
          rows_parsed: 0,
          scope_summary: null,
          error_message: null,
          started_at: "2099-03-22T10:00:00.000Z",
          finished_at: null,
        },
      ],
    );

    renderWithRouter(<DashboardPage />);

    expect(await screen.findByText("выполняется")).toBeInTheDocument();
  });
});
