/**
 * Тесты карточки «Канал авто-стопа» в HealthTab (H-9).
 *
 * GET /health/details отдаёт meta_api_channel (ONLINE/DEGRADED/UNKNOWN — статус
 * сетевого канала Marketing API, probe health_watchdog). Инцидент 01.07: канал
 * авто-стопа был мёртв, а остальной Health оставался зелёным — карточка должна
 * явно показывать состояние канала независимо от вердикта workers.
 */

import { render, screen } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

// ─── Моки ─────────────────────────────────────────────────────────────────────

vi.mock("@/lib/api/settings", () => ({
  useHealthDetails: vi.fn(),
  useObserverStatus: vi.fn(() => ({
    data: { status: "running", last_scan_at: null, interval_seconds: null },
    isLoading: false,
    error: null,
  })),
}));

import { useHealthDetails } from "@/lib/api/settings";
import { HealthTab } from "@/components/settings/HealthTab";

// ─── Хелперы ──────────────────────────────────────────────────────────────────

function wrap(ui: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{ui}</QueryClientProvider>;
}

const BASE_WORKERS = [
  { name: "observer", status: "ONLINE" as const, last_heartbeat_at: new Date().toISOString() },
];

function mockHealth(metaApiChannel: unknown) {
  vi.mocked(useHealthDetails).mockReturnValue({
    data: {
      overall: "HEALTHY",
      workers: BASE_WORKERS,
      observer_runtime: null,
      meta_api_channel: metaApiChannel,
    },
    isLoading: false,
    error: null,
    refetch: vi.fn(),
    isFetching: false,
  } as unknown as ReturnType<typeof useHealthDetails>);
}

describe("HealthTab — карточка «Канал авто-стопа»", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  // ONLINE — канал жив, реальный GET /me прошёл: карточка зелёная, как здоровые воркеры.
  it("рендерит ONLINE как здоровый статус", () => {
    mockHealth({ status: "ONLINE", checked_at: new Date().toISOString() });
    render(wrap(<HealthTab />));

    const badge = screen.getByLabelText("Канал авто-стопа: ONLINE");
    expect(badge).toBeInTheDocument();
    expect(badge).toHaveTextContent("ONLINE");
  });

  // DEGRADED — канал мёртв (сеть/токен): критичный акцент, инцидент 01.07 не должен повториться незамеченным.
  it("рендерит DEGRADED с критичным акцентом", () => {
    mockHealth({ status: "DEGRADED", checked_at: new Date().toISOString() });
    render(wrap(<HealthTab />));

    const badge = screen.getByLabelText("Канал авто-стопа: DEGRADED");
    expect(badge).toBeInTheDocument();
    expect(badge).toHaveTextContent("DEGRADED");
    // Пояснение про риск авто-стопа должно быть на экране.
    expect(screen.getByText(/Авто-стоп объявлений может не сработать/)).toBeInTheDocument();
  });

  // UNKNOWN — прободер молчит: приглушённый статус с пояснением "нет данных", не паника.
  it("рендерит UNKNOWN приглушённо с пояснением об отсутствии данных", () => {
    mockHealth({ status: "UNKNOWN", checked_at: null });
    render(wrap(<HealthTab />));

    const badge = screen.getByLabelText("Канал авто-стопа: UNKNOWN");
    expect(badge).toBeInTheDocument();
    expect(badge).toHaveTextContent("UNKNOWN");
    expect(screen.getByText(/Нет данных прободера/)).toBeInTheDocument();
  });

  // Выключенное сканирование — намеренный пропуск probe, а не протухший ключ.
  it("показывает фактическую причину UNKNOWN при выключенном сканировании", () => {
    mockHealth({
      status: "UNKNOWN",
      healthy: null,
      reason: "сканирование выключено",
      detail: "сканирование выключено — канал авто-стопа не проверяется",
      checked_at: new Date().toISOString(),
    });
    render(wrap(<HealthTab />));

    expect(
      screen.getByText(
        "Сканирование выключено — health_watchdog намеренно не проверяет канал авто-стопа.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/ключ протух/)).not.toBeInTheDocument();
  });

  // Старый бэк без поля meta_api_channel в ответе — компонент не должен падать,
  // должен показать явное "нет данных" вместо статус-бейджа.
  it("не падает при отсутствии meta_api_channel в ответе (старый бэк)", () => {
    mockHealth(undefined);
    render(wrap(<HealthTab />));

    expect(screen.getByText("Канал авто-стопа")).toBeInTheDocument();
    expect(
      screen.getByText("Нет данных прободера — health_watchdog ещё не проверял канал."),
    ).toBeInTheDocument();
    expect(screen.queryByLabelText(/Канал авто-стопа: /)).not.toBeInTheDocument();
  });

  // null — тот же случай, что undefined (API-тип допускает meta_api_channel?: ... | null).
  it("не падает при meta_api_channel: null", () => {
    mockHealth(null);
    render(wrap(<HealthTab />));

    expect(
      screen.getByText("Нет данных прободера — health_watchdog ещё не проверял канал."),
    ).toBeInTheDocument();
  });
});
