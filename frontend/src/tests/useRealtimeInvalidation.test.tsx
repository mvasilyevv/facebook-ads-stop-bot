// Тест: useRealtimeInvalidation — инвалидация TanStack Query по типам WS-событий.
// Мокаем useDashboardSocket, чтобы дёргать onMessage напрямую, без эмуляции WebSocket-транспорта
// (тот уже покрыт websocket.test.ts).

import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

const mockUseDashboardSocket = vi.fn();

vi.mock("@/lib/websocket/useDashboardSocket", () => ({
  useDashboardSocket: (opts: { onMessage?: (data: unknown) => void }) => {
    mockUseDashboardSocket(opts);
    return { status: "open", pollingFallback: false, reconnectAttempt: 0 };
  },
}));

import { useRealtimeInvalidation } from "@/lib/websocket/useRealtimeInvalidation";

describe("useRealtimeInvalidation", () => {
  let qc: QueryClient;
  let invalidateSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    invalidateSpy = vi.spyOn(qc, "invalidateQueries");
    mockUseDashboardSocket.mockClear();
  });

  function renderWithClient() {
    function wrapper({ children }: { children: ReactNode }) {
      return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
    }
    return renderHook(() => useRealtimeInvalidation(), { wrapper });
  }

  // H-8: task_changed (pause_ad/activate_ad через meta_api_worker) должен инвалидировать ["ads"],
  // иначе таблица /ads и AdDrawer показывают устаревший FSM-статус до ручного рефреша.
  it("task_changed инвалидирует ads", () => {
    renderWithClient();
    const onMessage = mockUseDashboardSocket.mock.calls[0]![0].onMessage as (d: unknown) => void;

    onMessage({ type: "task_changed" });

    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["ads"] });
  });

  // task_changed по-прежнему должен инвалидировать dashboard/tasks/campaigns runs — регресс
  // существовавшего поведения при добавлении ads-инвалидации.
  it("task_changed инвалидирует dashboard, tasks и campaigns runs", () => {
    renderWithClient();
    const onMessage = mockUseDashboardSocket.mock.calls[0]![0].onMessage as (d: unknown) => void;

    onMessage({ type: "task_changed" });

    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["dashboard"] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["tasks"] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["campaigns", "runs"] });
  });

  // scan_finished и alert_created не должны сломаться — существующее поведение не затронуто.
  it("scan_finished инвалидирует dashboard, ads, observer", () => {
    renderWithClient();
    const onMessage = mockUseDashboardSocket.mock.calls[0]![0].onMessage as (d: unknown) => void;

    onMessage({ type: "scan_finished" });

    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["dashboard"] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["ads"] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["observer"] });
  });

  // Статистика залива (воронка/трекер) зависит от свежих метрик скана — scan_finished
  // должен инвалидировать её кэш, иначе /stats и compact-строка на Dashboard замирают.
  it("scan_finished инвалидирует stats", () => {
    renderWithClient();
    const onMessage = mockUseDashboardSocket.mock.calls[0]![0].onMessage as (d: unknown) => void;

    onMessage({ type: "scan_finished" });

    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["stats"] });
  });

  it("tracker_changed сразу обновляет dashboard, stats, ads и history", () => {
    renderWithClient();
    const onMessage = mockUseDashboardSocket.mock.calls[0]![0].onMessage as (d: unknown) => void;

    onMessage({ type: "tracker_changed", fb_ad_id: "1201" });

    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["dashboard"] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["stats"] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["ads"] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["history"] });
  });

  // Неизвестный/пустой type не должен ничего инвалидировать и не должен падать.
  it("неизвестный type не вызывает инвалидацию", () => {
    renderWithClient();
    const onMessage = mockUseDashboardSocket.mock.calls[0]![0].onMessage as (d: unknown) => void;

    onMessage({ type: "unknown_event" });

    expect(invalidateSpy).not.toHaveBeenCalled();
  });
});
