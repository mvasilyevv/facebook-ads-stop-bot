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
import { useChatWidget } from "@/stores/chatWidget";

describe("useRealtimeInvalidation", () => {
  let qc: QueryClient;
  let invalidateSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    invalidateSpy = vi.spyOn(qc, "invalidateQueries");
    mockUseDashboardSocket.mockClear();
    useChatWidget.setState({ open: false, unread: 0, messages: [], pending: false, lastModel: null });
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

  // Неизвестный/пустой type не должен ничего инвалидировать и не должен падать.
  it("неизвестный type не вызывает инвалидацию", () => {
    renderWithClient();
    const onMessage = mockUseDashboardSocket.mock.calls[0]![0].onMessage as (d: unknown) => void;

    onMessage({ type: "unknown_event" });

    expect(invalidateSpy).not.toHaveBeenCalled();
  });

  // alert_created должен пушить нотификацию в AI-виджет (плавающий чат), чтобы
  // пользователь видел 🔔 STOP/WARNING без ручного открытия страницы алертов.
  it("alert_created пушит нотификацию в чат-стор AI-виджета", () => {
    renderWithClient();
    const onMessage = mockUseDashboardSocket.mock.calls[0]![0].onMessage as (d: unknown) => void;

    onMessage({
      type: "alert_created",
      payload: {
        fb_ad_id: "123",
        ad_name: "GH_CR2 | test",
        offer_code: "GH_CR2",
        stage: "stop",
        matched_rule_codes: ["cpa_stop"],
      },
    });

    const messages = useChatWidget.getState().messages;
    expect(messages).toHaveLength(1);
    expect(messages[0]!.kind).toBe("notification");
    expect(messages[0]!.content).toContain("STOP: GH_CR2 | test [GH_CR2] — cpa_stop");
    expect(useChatWidget.getState().unread).toBe(1);
  });

  // payload без stage (защита от мусора/неполного контракта) не должен ничего пушить.
  it("alert_created без валидного payload.stage не пушит нотификацию", () => {
    renderWithClient();
    const onMessage = mockUseDashboardSocket.mock.calls[0]![0].onMessage as (d: unknown) => void;

    onMessage({ type: "alert_created", payload: {} });

    expect(useChatWidget.getState().messages).toHaveLength(0);
  });
});
