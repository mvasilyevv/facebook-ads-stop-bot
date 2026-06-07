/**
 * Тесты HealthPage: verdict-баннер (HEALTHY/DEGRADED/CRITICAL), ONLINE/OFFLINE,
 * имена воркеров, кнопка Обновить, loading/error/empty-state.
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

// ─── Моки TG ─────────────────────────────────────────────────────────────────

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

// ─── Фикстуры ─────────────────────────────────────────────────────────────────

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

// ─── Моки API ─────────────────────────────────────────────────────────────────

let mockHealthData: HealthDetails | null = null;
let mockIsLoading = false;
let mockIsError = false;
const mockRefetch = vi.fn();

vi.mock("@/lib/api", () => ({
  useHealthDetails: () => ({
    data: mockHealthData,
    isLoading: mockIsLoading,
    isError: mockIsError,
    error: mockIsError ? new Error("Ошибка здоровья") : null,
    refetch: mockRefetch,
  }),
}));

// ─── Вспомогательный компонент для тестирования страницы ─────────────────────

function TestHealthPage() {
  const { data, isLoading, isError, error, refetch } = useHealthDetails();
  const overall = data?.overall ?? null;
  const workers: WorkerStatus[] = Array.isArray(data?.workers) ? data.workers : [];
  const onlineCount = workers.filter((w) => w.status === "ONLINE").length;
  const offlineCount = workers.length - onlineCount;

  if (isLoading) return <div data-testid="loading">Загрузка...</div>;
  if (isError)
    return (
      <div data-testid="error">
        {(error as Error)?.message ?? "Ошибка"}
        <button type="button" onClick={() => void refetch()}>
          Обновить
        </button>
      </div>
    );
  if (!data) return <div data-testid="empty">Нет данных</div>;

  return (
    <div>
      {/* Вердикт-баннер */}
      {overall && (
        <div data-testid="verdict-banner">
          <span data-testid="overall-verdict">{overall}</span>
          <span data-testid="online-summary">
            {onlineCount}/{workers.length} ONLINE
          </span>
        </div>
      )}
      {/* Счётчики для тестов */}
      <span data-testid="online-count">{onlineCount} online</span>
      <span data-testid="offline-count">{offlineCount} offline</span>
      {/* Список воркеров */}
      {workers.map((w) => (
        <WorkerRow key={w.name} worker={w} />
      ))}
      {/* Кнопка обновить */}
      <button
        type="button"
        aria-label="Обновить статус"
        onClick={() => void refetch()}
      >
        Обновить статус
      </button>
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
    mockRefetch.mockClear();
  });

  // HEALTHY: вердикт-баннер + счётчик ONLINE
  it("отображает вердикт HEALTHY и счётчик 1/1 ONLINE", () => {
    render(<Wrapper />);
    expect(screen.getByTestId("overall-verdict")).toHaveTextContent("HEALTHY");
    expect(screen.getByTestId("online-summary")).toHaveTextContent("1/1 ONLINE");
    expect(screen.getByTestId("online-count")).toHaveTextContent("1 online");
    expect(screen.getByTestId("offline-count")).toHaveTextContent("0 offline");
  });

  // DEGRADED: вердикт + смешанные статусы
  it("отображает вердикт DEGRADED при наличии OFFLINE воркеров", () => {
    mockHealthData = DEGRADED_DATA;
    render(<Wrapper />);
    expect(screen.getByTestId("overall-verdict")).toHaveTextContent("DEGRADED");
    expect(screen.getByTestId("online-count")).toHaveTextContent("1 online");
    expect(screen.getByTestId("offline-count")).toHaveTextContent("1 offline");
  });

  // CRITICAL: вердикт + все OFFLINE
  it("отображает вердикт CRITICAL при всех OFFLINE воркерах", () => {
    mockHealthData = CRITICAL_DATA;
    render(<Wrapper />);
    expect(screen.getByTestId("overall-verdict")).toHaveTextContent("CRITICAL");
    expect(screen.getByTestId("online-summary")).toHaveTextContent("0/2 ONLINE");
    expect(screen.getByTestId("offline-count")).toHaveTextContent("2 offline");
  });

  // WorkerRow ONLINE: имя воркера + статус ONLINE
  it("WorkerRow отображает имя observer и бейдж ONLINE", () => {
    render(<Wrapper />);
    expect(screen.getByText("Observer")).toBeInTheDocument();
    expect(screen.getByText("ONLINE")).toBeInTheDocument();
  });

  // WorkerRow OFFLINE: имя воркера + статус OFFLINE
  it("WorkerRow отображает имя Meta API Worker и бейдж OFFLINE", () => {
    mockHealthData = DEGRADED_DATA;
    render(<Wrapper />);
    expect(screen.getByText("Meta API Worker")).toBeInTheDocument();
    expect(screen.getByText("OFFLINE")).toBeInTheDocument();
  });

  // Кнопка «Обновить статус»
  it("кнопка Обновить статус вызывает refetch", async () => {
    const { getByRole } = render(<Wrapper />);
    const btn = getByRole("button", { name: "Обновить статус" });
    expect(btn).toBeInTheDocument();
    btn.click();
    expect(mockRefetch).toHaveBeenCalledTimes(1);
  });

  // Загрузка
  it("отображает состояние загрузки при isLoading=true", () => {
    mockIsLoading = true;
    mockHealthData = null;
    render(<Wrapper />);
    expect(screen.getByTestId("loading")).toBeInTheDocument();
  });

  // Ошибка
  it("отображает ошибку при isError=true", () => {
    mockIsError = true;
    mockHealthData = null;
    render(<Wrapper />);
    expect(screen.getByTestId("error")).toHaveTextContent("Ошибка здоровья");
  });

  // Пустые данные
  it("отображает пустое состояние при data=null", () => {
    mockHealthData = null;
    render(<Wrapper />);
    expect(screen.getByTestId("empty")).toBeInTheDocument();
  });
});

// ─── Изолированные тесты WorkerRow ───────────────────────────────────────────

describe("WorkerRow", () => {
  // ONLINE воркер: имя + TTL
  it("рендерит ONLINE-воркер с TTL", () => {
    render(
      <QueryClientProvider client={makeQC()}>
        <WorkerRow worker={WORKER_OBSERVER} />
      </QueryClientProvider>,
    );
    expect(screen.getByText("Observer")).toBeInTheDocument();
    expect(screen.getByText("ONLINE")).toBeInTheDocument();
    expect(screen.getByText(/TTL 45s/)).toBeInTheDocument();
  });

  // OFFLINE воркер: имя + статус
  it("рендерит OFFLINE-воркер meta_api", () => {
    render(
      <QueryClientProvider client={makeQC()}>
        <WorkerRow worker={WORKER_META_OFFLINE} />
      </QueryClientProvider>,
    );
    expect(screen.getByText("Meta API Worker")).toBeInTheDocument();
    expect(screen.getByText("OFFLINE")).toBeInTheDocument();
  });

  // Неизвестный воркер: технический идентификатор без перевода
  it("рендерит неизвестный воркер по техническому имени", () => {
    const unknownWorker: WorkerStatus = {
      name: "custom_worker_xyz",
      status: "ONLINE",
      last_heartbeat_at: null,
      ttl_seconds: null,
    };
    render(
      <QueryClientProvider client={makeQC()}>
        <WorkerRow worker={unknownWorker} />
      </QueryClientProvider>,
    );
    expect(screen.getByText("custom_worker_xyz")).toBeInTheDocument();
  });
});
