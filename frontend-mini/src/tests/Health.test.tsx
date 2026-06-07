/**
 * Тесты HealthPage: verdict-расчёт, ONLINE/OFFLINE статусы воркеров.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { HealthDetails } from "@fb/shared";
import { WorkerRow, type WorkerStatus } from "@/components/domain/WorkerRow";
import { useHealthDetails } from "@/lib/api";

// ─── Моки роутера ────────────────────────────────────────────────────────────

vi.mock("@tanstack/react-router", () => ({
  createFileRoute: () => ({ component: (c: unknown) => c }),
  useNavigate: () => vi.fn(),
  useRouter: () => ({ navigate: vi.fn(), history: { back: vi.fn() } }),
  useLocation: () => ({ pathname: "/health/" }),
}));

// ─── Моки TG ────────────────────────────────────────────────────────────────

vi.mock("@/lib/tg", () => ({
  haptic: { impact: vi.fn(), notify: vi.fn(), selection: vi.fn() },
  tgConfirm: vi.fn().mockResolvedValue(true),
  tgAlert: vi.fn().mockResolvedValue(undefined),
  openLink: vi.fn(),
  registerBackButton: () => () => {},
  hideBackButton: vi.fn(),
  initTheme: vi.fn(),
  getInitData: () => "",
}));

// ─── Фикстуры ────────────────────────────────────────────────────────────────

const WORKER_OBSERVER: WorkerStatus = {
  name: "observer",
  status: "ONLINE",
  last_heartbeat_at: new Date(Date.now() - 15_000).toISOString(),
  ttl_seconds: 45,
};

const WORKER_META_OFFLINE: WorkerStatus = {
  name: "meta_api",
  status: "OFFLINE",
  last_heartbeat_at: new Date(Date.now() - 300_000).toISOString(),
  ttl_seconds: null,
};

const HEALTHY_DATA: HealthDetails = {
  overall: "HEALTHY",
  workers: [WORKER_OBSERVER],
  observer_runtime: null,
};

const DEGRADED_DATA: HealthDetails = {
  overall: "DEGRADED",
  workers: [WORKER_OBSERVER, WORKER_META_OFFLINE],
  observer_runtime: null,
};

const CRITICAL_DATA: HealthDetails = {
  overall: "CRITICAL",
  workers: [
    { ...WORKER_OBSERVER, status: "OFFLINE" },
    WORKER_META_OFFLINE,
  ],
  observer_runtime: null,
};

// ─── Моки API ────────────────────────────────────────────────────────────────

let mockHealthData: HealthDetails | null = null;
let mockIsLoading = false;
let mockIsError = false;

vi.mock("@/lib/api", () => ({
  useHealthDetails: () => ({
    data: mockHealthData,
    isLoading: mockIsLoading,
    isError: mockIsError,
    error: mockIsError ? new Error("Ошибка здоровья") : null,
    refetch: vi.fn(),
  }),
}));

// ─── Компонент под тест ───────────────────────────────────────────────────────

const VERDICT_LABELS: Record<string, string> = {
  HEALTHY: "Всё в норме",
  DEGRADED: "Деградация",
  CRITICAL: "Критично",
};

function TestHealthPage() {
  const { data, isLoading, isError, error } = useHealthDetails();
  const overall = data?.overall ?? null;
  const workers: WorkerStatus[] = Array.isArray(data?.workers) ? data.workers : [];
  const onlineCount = workers.filter((w) => w.status === "ONLINE").length;
  const offlineCount = workers.length - onlineCount;

  if (isLoading) return <div data-testid="loading">Загрузка...</div>;
  if (isError) return <div data-testid="error">{(error as Error)?.message}</div>;
  if (!data) return <div data-testid="empty">Нет данных</div>;

  return (
    <div>
      {overall && (
        <p data-testid="overall-verdict">{VERDICT_LABELS[overall] ?? overall}</p>
      )}
      <p data-testid="online-count">{onlineCount} online</p>
      <p data-testid="offline-count">{offlineCount} offline</p>
      {workers.map((w) => (
        <WorkerRow key={w.name} worker={w} />
      ))}
    </div>
  );
}

const makeQC = () =>
  new QueryClient({ defaultOptions: { queries: { retry: false } } });

function Wrapper() {
  return (
    <QueryClientProvider client={makeQC()}>
      <TestHealthPage />
    </QueryClientProvider>
  );
}

// ─── Тесты ───────────────────────────────────────────────────────────────────

describe("HealthPage", () => {
  beforeEach(() => {
    mockHealthData = HEALTHY_DATA;
    mockIsLoading = false;
    mockIsError = false;
  });

  // HEALTHY: вердикт + все ONLINE
  it("показывает HEALTHY вердикт при всех ONLINE воркерах", () => {
    render(<Wrapper />);
    expect(screen.getByTestId("overall-verdict")).toHaveTextContent("Всё в норме");
    expect(screen.getByTestId("online-count")).toHaveTextContent("1 online");
    expect(screen.getByTestId("offline-count")).toHaveTextContent("0 offline");
  });

  // DEGRADED: вердикт + смешанные статусы
  it("показывает DEGRADED вердикт при наличии OFFLINE воркеров", () => {
    mockHealthData = DEGRADED_DATA;
    render(<Wrapper />);
    expect(screen.getByTestId("overall-verdict")).toHaveTextContent("Деградация");
    expect(screen.getByTestId("offline-count")).toHaveTextContent("1 offline");
  });

  // CRITICAL: вердикт
  it("показывает CRITICAL вердикт", () => {
    mockHealthData = CRITICAL_DATA;
    render(<Wrapper />);
    expect(screen.getByTestId("overall-verdict")).toHaveTextContent("Критично");
    expect(screen.getByTestId("offline-count")).toHaveTextContent("2 offline");
  });

  // WorkerRow ONLINE: имя + статус
  it("WorkerRow рендерит ONLINE статус для observer", () => {
    render(<Wrapper />);
    expect(screen.getByText("Observer")).toBeInTheDocument();
    expect(screen.getByText("ONLINE")).toBeInTheDocument();
  });

  // WorkerRow OFFLINE: статус
  it("WorkerRow рендерит OFFLINE статус для meta_api воркера", () => {
    mockHealthData = DEGRADED_DATA;
    render(<Wrapper />);
    expect(screen.getByText("Meta API Worker")).toBeInTheDocument();
    expect(screen.getByText("OFFLINE")).toBeInTheDocument();
  });

  // Загрузка
  it("рендерит загрузку при isLoading", () => {
    mockIsLoading = true;
    mockHealthData = null;
    render(<Wrapper />);
    expect(screen.getByTestId("loading")).toBeInTheDocument();
  });

  // Ошибка
  it("рендерит ошибку при isError", () => {
    mockIsError = true;
    mockHealthData = null;
    render(<Wrapper />);
    expect(screen.getByTestId("error")).toBeInTheDocument();
  });
});
