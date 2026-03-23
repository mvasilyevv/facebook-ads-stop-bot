import { render, screen, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { Layout } from "../../src/components/Layout";
import AdsPage from "../../src/pages/AdsPage";
import DashboardPage from "../../src/pages/DashboardPage";
import type { AdSummary, ProfileItem, ProfileLaunchDashboard, ProfileLaunchItem, ServiceSettingsResponse } from "../../src/types";
import { server } from "../test-server";

function buildAdSummary(overrides: Partial<AdSummary>): AdSummary {
  return {
    fb_ad_id: "120241420000010001",
    campaign_name: "CR2 | DRC | MV | NEW | pwa.partners | 15.03",
    adset_name: "1",
    ad_name: "DRC_CR2_CR001",
    delivery_status: "ACTIVE",
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

function buildProfile(overrides: Partial<ProfileItem>): ProfileItem {
  return {
    profile_id: "profile-1",
    display_name: "Профиль 1",
    browser_host_id: "vision-3030",
    is_active: true,
    scan_suspended: false,
    last_launch_at: "2026-03-22T11:00:00.000Z",
    ...overrides,
  };
}

function buildLaunch(overrides: Partial<ProfileLaunchItem>): ProfileLaunchItem {
  return {
    id: "launch-active",
    profile_id: "profile-1",
    display_name: "Профиль 1",
    browser_host_id: "vision-3030",
    name: "Запуск 23.03 12:00",
    is_active: true,
    started_at: "2026-03-23T12:00:00.000Z",
    ended_at: null,
    created_at: "2026-03-23T12:00:00.000Z",
    updated_at: "2026-03-23T12:00:00.000Z",
    ...overrides,
  };
}

function mockScopeEndpoints({
  profiles,
  launches,
  ads,
  launchDashboard,
}: {
  profiles: ProfileItem[];
  launches: ProfileLaunchItem[];
  ads: AdSummary[];
  launchDashboard: ProfileLaunchDashboard;
}) {
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
    updated_at: "2026-03-23T12:00:00.000Z",
  };

  server.use(
    http.get("*/profiles", () => HttpResponse.json(profiles)),
    http.get("*/profile-launches", ({ request }) => {
      const url = new URL(request.url);
      const profileId = url.searchParams.get("profile_id");
      if (profileId && profileId !== profiles[0]?.profile_id) {
        return HttpResponse.json([]);
      }
      return HttpResponse.json(launches);
    }),
    http.get("*/profile-launches/:launchId/dashboard", () => HttpResponse.json(launchDashboard)),
    http.get("*/health", () =>
      HttpResponse.json({
        status: "ok",
        service: "facebook-ads-stop-bot",
        environment: "test",
        database_status: "healthy",
        timestamp: "2026-03-23T12:00:00.000Z",
      }),
    ),
    http.get("*/ads", ({ request }) => {
      const url = new URL(request.url);
      const profileId = url.searchParams.get("profile_id");
      const launchId = url.searchParams.get("profile_launch_id");
      if (profileId && profileId !== profiles[0]?.profile_id) {
        return HttpResponse.json([]);
      }
      if (launchId && launchId !== launches[0]?.id) {
        return HttpResponse.json([]);
      }
      return HttpResponse.json(ads);
    }),
    http.get("*/decisions", () => HttpResponse.json([])),
    http.get("*/rules", () => HttpResponse.json([])),
    http.get("*/offers", () => HttpResponse.json([])),
    http.get("*/sessions", () => HttpResponse.json([])),
    http.get("*/scan-runs", () => HttpResponse.json([])),
    http.get("*/settings/service", () => HttpResponse.json(serviceSettings)),
  );
}

function createMockStorage(initial: Record<string, string> = {}) {
  const values = new Map(Object.entries(initial));
  return {
    get length() {
      return values.size;
    },
    getItem(key: string) {
      return values.has(key) ? values.get(key)! : null;
    },
    setItem(key: string, value: string) {
      values.set(key, value);
    },
    removeItem(key: string) {
      values.delete(key);
    },
    clear() {
      values.clear();
    },
    key(index: number) {
      return Array.from(values.keys())[index] ?? null;
    },
  } as Storage;
}

function installMockLocalStorage(initial: Record<string, string> = {}) {
  Object.defineProperty(window, "localStorage", {
    value: createMockStorage(initial),
    configurable: true,
  });
}

beforeEach(() => {
  installMockLocalStorage();
});

afterEach(() => {
  installMockLocalStorage();
});

describe("Launch scope", () => {
  // Проверяет, что при нескольких профилях контекст не выбирается молча и оператор видит пустое состояние до явного выбора.
  it("требует явный выбор профиля, если профилей несколько", async () => {
    mockScopeEndpoints({
      profiles: [
        buildProfile({ profile_id: "profile-1", display_name: "Профиль 1" }),
        buildProfile({ profile_id: "profile-2", display_name: "Профиль 2" }),
      ],
      launches: [buildLaunch({ id: "launch-active", is_active: true, name: "Активный запуск" })],
      ads: [],
      launchDashboard: {
        launch: buildLaunch({ id: "launch-active", is_active: true, name: "Активный запуск" }),
        previous_launch: null,
        current: {
          total_ads: 0,
          active_ads: 0,
          paused_ads: 0,
          attention_ads: 0,
          spend_total: "0.00",
          scans_count: 0,
          last_scan_at: null,
        },
        previous: null,
        spend_series: [],
        attention_series: [],
        action_series: [],
      },
    });

    render(
      <MemoryRouter initialEntries={["/"]}>
        <Routes>
          <Route element={<Layout />}>
            <Route path="/" element={<DashboardPage />} />
          </Route>
        </Routes>
      </MemoryRouter>,
    );

    await screen.findByText("Профиль не выбран");
    expect(screen.getByRole("combobox", { name: /профиль/i })).toHaveValue("");
    expect(screen.getByText("Выберите профиль в верхней панели, чтобы открыть активный запуск и его историю.")).toBeInTheDocument();
  });

  // Проверяет, что один профиль и один активный запуск подхватываются автоматически без ручного выбора.
  it("автоматически выбирает единственный профиль и активный запуск", async () => {
    mockScopeEndpoints({
      profiles: [buildProfile({ profile_id: "profile-1" })],
      launches: [buildLaunch({ id: "launch-active", is_active: true, name: "Активный запуск" })],
      ads: [buildAdSummary({ ad_name: "DRC_CR2_CR001" })],
      launchDashboard: {
        launch: buildLaunch({ id: "launch-active", is_active: true, name: "Активный запуск" }),
        previous_launch: null,
        current: {
          total_ads: 1,
          active_ads: 1,
          paused_ads: 0,
          attention_ads: 0,
          spend_total: "0.00",
          scans_count: 1,
          last_scan_at: "2026-03-23T12:05:00.000Z",
        },
        previous: null,
        spend_series: [],
        attention_series: [],
        action_series: [],
      },
    });

    render(
      <MemoryRouter initialEntries={["/"]}>
        <Routes>
          <Route element={<Layout />}>
            <Route path="/" element={<DashboardPage />} />
          </Route>
        </Routes>
      </MemoryRouter>,
    );

    await waitFor(() => expect(screen.getByRole("combobox", { name: /профиль/i })).toHaveValue("profile-1"));
    await waitFor(() => expect(screen.getByRole("combobox", { name: /запуск/i })).toHaveValue("launch-active"));
    await screen.findByText("Обзор запуска");
    expect(screen.getByRole("button", { name: /новый запуск/i })).toBeInTheDocument();
    expect(screen.getByText("Активный запуск")).toBeInTheDocument();
    expect(screen.getByText("Объявления запуска")).toBeInTheDocument();
    expect(screen.getByText("Сводка запуска")).toBeInTheDocument();
    expect(screen.getByText("Объявления запуска (1)")).toBeInTheDocument();
  });

  // Проверяет, что архивный запуск открывается только для просмотра и скрывает ручные действия на странице объявлений.
  it("показывает архивный запуск как только для чтения на странице объявлений", async () => {
    installMockLocalStorage({
      fb_agent_selected_profile_id: "profile-1",
      fb_agent_selected_launch_id: "launch-archived",
    });

    mockScopeEndpoints({
      profiles: [buildProfile({ profile_id: "profile-1" })],
      launches: [
        buildLaunch({ id: "launch-active", is_active: true, name: "Активный запуск" }),
        buildLaunch({
          id: "launch-archived",
          is_active: false,
          ended_at: "2026-03-23T10:00:00.000Z",
          name: "Архивный запуск",
        }),
      ],
      ads: [buildAdSummary({ ad_name: "DRC_CR2_CR002", delivery_status: "PAUSED" })],
      launchDashboard: {
        launch: buildLaunch({
          id: "launch-archived",
          is_active: false,
          ended_at: "2026-03-23T10:00:00.000Z",
          name: "Архивный запуск",
        }),
        previous_launch: buildLaunch({ id: "launch-active", is_active: true, name: "Активный запуск" }),
        current: {
          total_ads: 1,
          active_ads: 0,
          paused_ads: 1,
          attention_ads: 0,
          spend_total: "0.00",
          scans_count: 2,
          last_scan_at: "2026-03-23T10:00:00.000Z",
        },
        previous: {
          total_ads: 1,
          active_ads: 1,
          paused_ads: 0,
          attention_ads: 0,
          spend_total: "1.00",
          scans_count: 1,
          last_scan_at: "2026-03-23T09:00:00.000Z",
        },
        spend_series: [],
        attention_series: [],
        action_series: [],
      },
    });

    render(
      <MemoryRouter initialEntries={["/ads"]}>
        <Routes>
          <Route element={<Layout />}>
            <Route path="/ads" element={<AdsPage />} />
          </Route>
        </Routes>
      </MemoryRouter>,
    );

    await waitFor(() =>
      expect(screen.getByRole("combobox", { name: /запуск/i })).toHaveValue("launch-archived"),
    );
    expect(screen.getByText("Архивный запуск · архивный просмотр")).toBeInTheDocument();
    expect(screen.getByText("Открыт архивный запуск. Карточки доступны только для просмотра, ручные действия отключены.")).toBeInTheDocument();
    expect(screen.queryByText("Причина ручной блокировки")).not.toBeInTheDocument();
    await waitFor(() => {
      expect(screen.queryByRole("button", { name: /Блокировать/i })).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: /Разблокировать/i })).not.toBeInTheDocument();
    });
  });
});
