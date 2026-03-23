import { fireEvent, screen, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";
import SettingsPage from "../../src/pages/SettingsPage";
import { server } from "../test-server";
import { renderWithRouter } from "../test-utils";

function setupRuntimeSettingsPage(options?: {
  autoResumeAvailable?: boolean;
  brokenEndpoint?: "scan-runs" | "settings/service";
  onServiceSettingsRead?: () => {
    auto_pause_enabled: boolean;
    auto_resume_enabled: boolean;
    auto_resume_available: boolean;
    observe_only_enabled: boolean;
    full_scan_interval_seconds: number;
    recheck_interval_seconds: number;
    full_scan_profile_concurrency: number;
    action_worker_concurrency: number;
    vision_local_api_url: string;
    vision_cloud_api_url: string;
    telegram_chat_id: string;
    vision_api_token_masked: string | null;
    telegram_bot_token_masked: string | null;
    vision_api_token_configured: boolean;
    telegram_bot_token_configured: boolean;
    updated_at: string | null;
  };
}) {
  const updateSpy = vi.fn();

  server.use(
    http.get("*/health", () =>
      HttpResponse.json({
        status: "ok",
        service: "frontend",
        environment: "test",
        database_status: "healthy",
        timestamp: "2026-03-22T11:30:00.000Z",
      }),
    ),
    http.get("*/ads", () => HttpResponse.json([])),
    http.get("*/decisions", () => HttpResponse.json([])),
    http.get("*/watchlist", () => HttpResponse.json([])),
    http.get("*/action-jobs", () => HttpResponse.json([])),
    http.get("*/rules", () => HttpResponse.json([])),
    http.get("*/offers", () => HttpResponse.json([])),
    http.get("*/sessions", () => HttpResponse.json([])),
    http.get("*/settings/suspended-profiles", () => HttpResponse.json([])),
    http.get("*/scan-runs", () => {
      if (options?.brokenEndpoint === "scan-runs") {
        return HttpResponse.json(
          { message: "Журнал сканов временно недоступен" },
          { status: 503 },
        );
      }
      return HttpResponse.json([
        {
          id: "scan-run-1",
          browser_host_id: "vision-3030",
          profile_id: "profile-1",
          status: "SUCCEEDED",
          pipeline_kind: "FULL_SCAN",
          trigger_source: "scheduler",
          target_fb_ad_ids: [],
          rows_seen: 51,
          rows_parsed: 51,
          collect_ms: 800,
          evaluate_ms: 200,
          persist_ms: 100,
          queue_ms: 50,
          action_jobs_enqueued: 0,
          scope_summary: {
            rows_in_scope: 51,
            rows_not_seen_this_scan: 0,
            active_rows: 10,
            paused_rows: 41,
          },
          error_message: null,
          started_at: "2026-03-22T11:20:09.577311Z",
          finished_at: "2026-03-22T11:20:11.577311Z",
        },
      ]);
    }),
    http.get("*/settings/service", () => {
      if (options?.brokenEndpoint === "settings/service") {
        return HttpResponse.json(
          { message: "Настройки сервиса временно недоступны" },
          { status: 503 },
        );
      }
      return HttpResponse.json(options?.onServiceSettingsRead?.() ?? {
        auto_pause_enabled: true,
        auto_resume_enabled: false,
        auto_resume_available: options?.autoResumeAvailable ?? false,
        observe_only_enabled: false,
        full_scan_interval_seconds: 60,
        recheck_interval_seconds: 15,
        full_scan_profile_concurrency: 2,
        action_worker_concurrency: 2,
        vision_local_api_url: "http://127.0.0.1:3030",
        vision_cloud_api_url: "https://vision.example/api",
        telegram_chat_id: "777000",
        vision_api_token_masked: "••••oken",
        telegram_bot_token_masked: "••••oken",
        vision_api_token_configured: true,
        telegram_bot_token_configured: true,
        updated_at: "2026-03-22T11:30:00.000Z",
      });
    }),
    http.put("*/settings/service", async ({ request }) => {
      updateSpy(await request.json());
      return HttpResponse.json({
        auto_pause_enabled: true,
        auto_resume_enabled: false,
        auto_resume_available: options?.autoResumeAvailable ?? false,
        observe_only_enabled: false,
        full_scan_interval_seconds: 60,
        recheck_interval_seconds: 15,
        full_scan_profile_concurrency: 2,
        action_worker_concurrency: 2,
        vision_local_api_url: "http://127.0.0.1:3030",
        vision_cloud_api_url: "https://vision.example/api",
        telegram_chat_id: "777000",
        vision_api_token_masked: "••••oken",
        telegram_bot_token_masked: "••••oken",
        vision_api_token_configured: true,
        telegram_bot_token_configured: true,
        updated_at: "2026-03-22T11:35:00.000Z",
      });
    }),
  );

  return { updateSpy };
}

describe("SettingsPage runtime", () => {
  // Проверяет, что переключатель авторезюма блокируется без feature flag и не даёт отправить обновление.
  it("блокирует авторезюм без feature flag", async () => {
    const { updateSpy } = setupRuntimeSettingsPage({ autoResumeAvailable: false });

    renderWithRouter(<SettingsPage />);

    const autoResume = await screen.findByRole("checkbox", { name: /Авторезюм/i });
    expect(autoResume).toBeDisabled();
    expect(screen.getByText("Недоступно без feature flag")).toBeInTheDocument();

    fireEvent.click(autoResume);

    await waitFor(() => {
      expect(updateSpy).not.toHaveBeenCalled();
    });
  });

  // Проверяет, что страница переживает частичную деградацию API и показывает понятную aggregate-ошибку.
  it("показывает aggregate ошибку при частичной загрузке данных", async () => {
    setupRuntimeSettingsPage({ brokenEndpoint: "scan-runs" });

    renderWithRouter(<SettingsPage />);

    expect(await screen.findByRole("heading", { name: "Настройки" })).toBeInTheDocument();
    expect(screen.getByText("Журнал сканов временно недоступен")).toBeInTheDocument();
    expect(screen.getByText("Автоматизация")).toBeInTheDocument();
    expect(screen.getByText("Интеграции")).toBeInTheDocument();
  });

  // Проверяет, что страница ставит автообновление на паузу, пока есть несохраненный черновик.
  it("не запускает автообновление поверх несохраненного черновика", async () => {
    let serviceReads = 0;
    setupRuntimeSettingsPage({
      onServiceSettingsRead: () => {
        serviceReads += 1;
        return {
          auto_pause_enabled: true,
          auto_resume_enabled: false,
          auto_resume_available: false,
          observe_only_enabled: false,
          full_scan_interval_seconds: 60,
          recheck_interval_seconds: 15,
          full_scan_profile_concurrency: 2,
          action_worker_concurrency: 2,
          vision_local_api_url: "http://127.0.0.1:3030",
          vision_cloud_api_url: "https://vision.example/api",
          telegram_chat_id: serviceReads > 1 ? "999000" : "777000",
          vision_api_token_masked: "••••oken",
          telegram_bot_token_masked: "••••oken",
          vision_api_token_configured: true,
          telegram_bot_token_configured: true,
          updated_at: "2026-03-22T11:35:00.000Z",
        };
      },
    });

    renderWithRouter(<SettingsPage />);

    const chatIdInput = await screen.findByLabelText("Telegram chat id");
    fireEvent.change(chatIdInput, { target: { value: "123123" } });

    window.dispatchEvent(new Event("focus"));

    await waitFor(() => {
      expect(chatIdInput).toHaveValue("123123");
    });

    expect(serviceReads).toBe(1);
    expect(chatIdInput).toHaveValue("123123");
  });
});
