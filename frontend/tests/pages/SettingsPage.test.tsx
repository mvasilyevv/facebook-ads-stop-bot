import { fireEvent, screen, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import SettingsPage from "../../src/pages/SettingsPage";
import type { AdSummary, BrowserSessionItem, DecisionItem, OfferItem, RuleItem, ScanRunItem, ServiceSettingsResponse, SuspendedProfileItem } from "../../src/types";
import { server } from "../test-server";
import { renderWithRouter } from "../test-utils";

function buildAdSummary(overrides: Partial<AdSummary>): AdSummary {
  return {
    fb_ad_id: "120241420000000001",
    campaign_name: "CR2 | DRC | MV | NEW | pwa.partners | 15.03",
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

function setupSettingsPageFixtures(
  overrides?: Partial<ServiceSettingsResponse>,
  suspendedProfiles: SuspendedProfileItem[] = [],
  adsOverride?: AdSummary[],
) {
  const savedBodies: Array<Record<string, unknown>> = [];
  let activeSuspendedProfiles = [...suspendedProfiles];
  const serviceSettings: ServiceSettingsResponse = {
    auto_pause_enabled: true,
    auto_resume_enabled: false,
    auto_resume_available: true,
    observe_only_enabled: false,
    scan_interval_seconds: 120,
    vision_local_api_url: "http://127.0.0.1:3030",
    vision_cloud_api_url: "https://vision.example/api",
    telegram_chat_id: "777000",
    vision_api_token_masked: "••••oken",
    telegram_bot_token_masked: "••••oken",
    vision_api_token_configured: true,
    telegram_bot_token_configured: true,
    updated_at: "2026-03-22T11:30:00.000Z",
    ...overrides,
  };

  const scanRuns: ScanRunItem[] = [
    {
      id: "scan-run-1",
      browser_host_id: "vision-3030",
      profile_id: "profile-1",
      status: "SUCCEEDED",
      rows_seen: 42,
      rows_parsed: 42,
      scope_summary: {
        rows_in_scope: 42,
        rows_not_seen_this_scan: 9,
        active_rows: 0,
        paused_rows: 42,
        fb_ad_ids: [],
      },
      error_message: null,
      started_at: "2026-03-22T11:20:09.577311Z",
      finished_at: "2026-03-22T11:20:11.577311Z",
    },
  ];

  const ads: AdSummary[] = adsOverride ?? [
    buildAdSummary({
      fb_ad_id: "120241420867510176",
      ad_name: "DRC_CR2_CR014",
      adset_name: "2",
      delivery_status: "ACTIVE",
      scope_presence: "NOT_SEEN_THIS_SCAN",
      spend: "0.50",
      last_seen_at: "2026-03-22T09:20:53.318259Z",
    }),
  ];
  const decisions: DecisionItem[] = [];
  const rules: RuleItem[] = [];
  const offers: OfferItem[] = [];
  const sessions: BrowserSessionItem[] = [];

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
    http.get("*/settings/suspended-profiles", () => HttpResponse.json(activeSuspendedProfiles)),
    http.put("*/settings/service", async ({ request }) => {
      const body = (await request.json()) as Record<string, unknown>;
      savedBodies.push(body);
      serviceSettings.auto_pause_enabled = Boolean(body.auto_pause_enabled);
      serviceSettings.auto_resume_enabled = Boolean(body.auto_resume_enabled);
      serviceSettings.observe_only_enabled = Boolean(body.observe_only_enabled);
      serviceSettings.scan_interval_seconds = Number(body.scan_interval_seconds);
      serviceSettings.vision_local_api_url = String(body.vision_local_api_url);
      serviceSettings.vision_cloud_api_url = String(body.vision_cloud_api_url);
      serviceSettings.telegram_chat_id = String(body.telegram_chat_id);
      if (typeof body.vision_api_token === "string") {
        serviceSettings.vision_api_token_masked = "••••oken";
        serviceSettings.vision_api_token_configured = body.vision_api_token.trim().length > 0;
      }
      if (typeof body.telegram_bot_token === "string") {
        serviceSettings.telegram_bot_token_masked = "••••oken";
        serviceSettings.telegram_bot_token_configured = body.telegram_bot_token.trim().length > 0;
      }
      return HttpResponse.json(serviceSettings);
    }),
    http.post("*/settings/suspended-profiles/:profileId/reset", ({ params }) => {
      const profileId = String(params.profileId);
      activeSuspendedProfiles = activeSuspendedProfiles.filter((item) => item.profile_id !== profileId);
      return HttpResponse.json({
        message: "Сканирование профиля снова разрешено",
        profile: {
          profile_id: profileId,
          display_name: "Vision профиль",
          browser_host_id: "vision-3030",
          reason: "Сканирование разрешено вручную",
          suspended_at: "2026-03-22T11:40:00.000Z",
        },
      });
    }),
  );

  return { savedBodies, serviceSettings, activeSuspendedProfiles };
}

describe("SettingsPage", () => {
  // Проверяет, что сервисные настройки сохраняются через API и в запрос уходит обновлённая частота скана.
  it("сохраняет service settings через API", async () => {
    const { savedBodies } = setupSettingsPageFixtures();

    renderWithRouter(<SettingsPage />);

    expect(await screen.findByRole("heading", { name: "Настройки" })).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Vision API ключ"), {
      target: { value: "vision-secret-token" },
    });
    fireEvent.change(screen.getByLabelText("Telegram bot token"), {
      target: { value: "telegram-secret-token" },
    });
    fireEvent.change(screen.getByLabelText("Vision локальный URL"), {
      target: { value: "http://127.0.0.1:4040" },
    });
    fireEvent.change(screen.getByLabelText("Vision cloud URL"), {
      target: { value: "https://vision.example/new-api" },
    });
    fireEvent.change(screen.getByLabelText("Telegram chat id"), {
      target: { value: "888000" },
    });
    fireEvent.change(screen.getByRole("combobox"), {
      target: { value: "300" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить настройки" }));

    await waitFor(() => {
      expect(screen.getByText("Настройки сервиса сохранены")).toBeInTheDocument();
    });

    expect(savedBodies).toHaveLength(1);
    expect(savedBodies[0]).toMatchObject({
      auto_pause_enabled: true,
      auto_resume_enabled: false,
      observe_only_enabled: false,
      scan_interval_seconds: 300,
      telegram_chat_id: "888000",
      vision_local_api_url: "http://127.0.0.1:4040",
      vision_cloud_api_url: "https://vision.example/new-api",
      vision_api_token: "vision-secret-token",
      telegram_bot_token: "telegram-secret-token",
    });
  });

  // Проверяет, что ошибка загрузки настроек показывается в баннере и не ломает сам экран.
  it("показывает ошибку загрузки настроек", async () => {
    setupSettingsPageFixtures();
    server.use(
      http.get("*/settings/service", () =>
        HttpResponse.json({ message: "Сервис настроек временно недоступен" }, { status: 503 }),
      ),
    );

    renderWithRouter(<SettingsPage />);

    expect(await screen.findByRole("heading", { name: "Настройки" })).toBeInTheDocument();
    expect(screen.getByText("Сервис настроек временно недоступен")).toBeInTheDocument();
  });

  // Проверяет, что ошибка сохранения формы показывает сообщение и не маскируется под успех.
  it("показывает ошибку при сохранении настроек", async () => {
    setupSettingsPageFixtures();
    server.use(
      http.put("*/settings/service", () =>
        HttpResponse.json({ message: "Сохранение настроек временно недоступно" }, { status: 500 }),
      ),
    );

    renderWithRouter(<SettingsPage />);

    expect(await screen.findByRole("heading", { name: "Настройки" })).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Vision локальный URL"), {
      target: { value: "http://127.0.0.1:5050" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить настройки" }));

    await waitFor(() => {
      expect(screen.getByText("Сохранение настроек временно недоступно")).toBeInTheDocument();
    });
    expect(screen.queryByText("Настройки сервиса сохранены")).not.toBeInTheDocument();
  });

  // Проверяет, что проблемный профиль снимается со стопа и исчезает из списка после reset.
  it("снимает стоп с проблемного профиля", async () => {
    setupSettingsPageFixtures(undefined, [
      {
        profile_id: "vision-profile-suspended",
        display_name: "Vision профиль suspended",
        browser_host_id: "vision-3030",
        reason: "Не удалось получить полный набор строк Ads Manager после 3 попыток",
        suspended_at: "2026-03-22T11:20:00.000Z",
      },
    ]);

    renderWithRouter(<SettingsPage />);

    expect(await screen.findByText("Vision профиль suspended")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Снять стоп" }));

    await waitFor(() => {
      expect(screen.queryByText("Vision профиль suspended")).not.toBeInTheDocument();
    });
    expect(screen.getByText("Профиль vision-profile-suspended снова разрешён для сканирования")).toBeInTheDocument();
  });

  // Проверяет, что плиточный обзор в настройках показывает все кампании без лимита превью.
  it("показывает все кампании в плиточном обзоре настроек", async () => {
    setupSettingsPageFixtures(undefined, [], [
      buildAdSummary({
        fb_ad_id: "120241420000000201",
        campaign_name: "Кампания 1",
        adset_name: "1",
        ad_name: "Настройки 1-1",
        spend: "10.00",
      }),
      buildAdSummary({
        fb_ad_id: "120241420000000202",
        campaign_name: "Кампания 2",
        adset_name: "1",
        ad_name: "Настройки 2-1",
        spend: "9.00",
      }),
      buildAdSummary({
        fb_ad_id: "120241420000000203",
        campaign_name: "Кампания 3",
        adset_name: "1",
        ad_name: "Настройки 3-1",
        spend: "8.00",
      }),
      buildAdSummary({
        fb_ad_id: "120241420000000204",
        campaign_name: "Кампания 4",
        adset_name: "1",
        ad_name: "Настройки 4-1",
        spend: "7.00",
      }),
    ]);

    renderWithRouter(<SettingsPage />);

    expect(await screen.findByText("Настройки 1-1")).toBeInTheDocument();
    expect(screen.getByText("Настройки 2-1")).toBeInTheDocument();
    expect(screen.getByText("Настройки 3-1")).toBeInTheDocument();
    expect(screen.getByText("Настройки 4-1")).toBeInTheDocument();
    expect(screen.getAllByText("Кампания 4").length).toBeGreaterThan(0);
  });
});
