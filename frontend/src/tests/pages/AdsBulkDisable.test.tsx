/**
 * Тест bulk-disable money-flow в AdsPage (канон ads-web.jsx).
 *
 * Проверяем:
 *   1. Рендер строк из useAds (виртуализация замокана — показывает все).
 *   2. Выбор строк (per-row checkbox) → BulkActionBar (toolbar) с «N выбрано».
 *   3. Клик Disable → ConfirmDialog с confirm-with-typing (требует ввод DISABLE).
 *   4. MONEY: ввод DISABLE + confirm → useBulkDisable вызван с idempotency_token
 *      (UUID v4) в ОТДЕЛЬНОМ поле (не в reason) и корректным набором fb_ad_ids.
 *   5. Empty state при отсутствии объявлений.
 */

import { render, screen, within, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { AdSnapshot } from "@fb/shared";

// ─── Моки ─────────────────────────────────────────────────────────────────────

vi.mock("@tanstack/react-router", () => ({
  // Route.useSearch() читается компонентом — возвращаем пустой search (без deep-link).
  createFileRoute: (_path: string) => (opts: { component: unknown }) => ({
    ...opts,
    useSearch: () => ({}),
  }),
  useNavigate: () => vi.fn(),
  useRouter: () => ({ navigate: vi.fn() }),
  useParams: () => ({}),
  useSearch: () => ({}),
}));

// Виртуализация jsdom не имеет layout — мокаем чтобы показать все строки.
vi.mock("@tanstack/react-virtual", () => ({
  useVirtualizer: ({ count }: { count: number }) => ({
    getVirtualItems: () =>
      Array.from({ length: count }, (_, i) => ({ index: i, start: i * 40, size: 40 })),
    getTotalSize: () => count * 40,
  }),
}));

// Реалистичный shape ответа BulkDisableResultOut: created/skipped/failed — массивы объектов.
const mockBulkDisable = vi
  .fn()
  .mockResolvedValue({ created: [{ id: "1" }, { id: "2" }], skipped: [], failed: [] });

vi.mock("@/lib/api/ads", () => ({
  useAds: vi.fn(),
  useBulkDisable: vi.fn(() => ({
    mutateAsync: mockBulkDisable,
    isPending: false,
  })),
  useDeleteAds: vi.fn(() => ({
    mutateAsync: vi.fn().mockResolvedValue({ deleted: [], count: 0 }),
    isPending: false,
  })),
  useAdTimeline: vi.fn(() => ({ data: null, isLoading: false, isError: false })),
  useDisableTasks: vi.fn(() => ({ data: [], isLoading: false, isError: false, refetch: vi.fn() })),
  useEnableTasks: vi.fn(() => ({ data: [], isLoading: false, isError: false, refetch: vi.fn() })),
}));

vi.mock("@/lib/api/dashboard", () => ({
  useDashboardStats: vi.fn(() => ({ data: undefined, isLoading: false, isError: false })),
}));

vi.mock("@/lib/websocket/useRealtimeInvalidation", () => ({
  useRealtimeInvalidation: vi.fn(() => ({
    status: "connected",
    pollingFallback: false,
    reconnectAttempt: 0,
    forceReconnect: vi.fn(),
  })),
}));

// ─── Импорты после моков ──────────────────────────────────────────────────────

import { useAds } from "@/lib/api/ads";

// ─── Фабрика мок-объявлений ───────────────────────────────────────────────────

function makeAd(id: string, overrides: Partial<AdSnapshot> = {}): AdSnapshot {
  return {
    fb_ad_id: id,
    internal_id: `uuid-${id}`,
    ad_name: `Объявление ${id}`,
    alert_state: "normal",
    is_active: true,
    ...overrides,
  } as AdSnapshot;
}

const MOCK_ADS = [makeAd("111"), makeAd("222")];

// ─── Хелпер рендера ──────────────────────────────────────────────────────────

async function renderAdsPage() {
  const { AdsPage } = await import("../../routes/ads/index").then((m) => {
    const route = m.Route as unknown as { component: React.FC };
    return { AdsPage: route.component };
  });

  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <AdsPage />
    </QueryClientProvider>,
  );
}

/** Выбирает все мок-строки кликом по их toggle-кнопке. */
async function selectAllRows(user: ReturnType<typeof userEvent.setup>) {
  for (const ad of MOCK_ADS) {
    const cb = screen.getByRole("button", { name: `Выбрать ${ad.ad_name}` });
    await user.click(cb);
  }
}

// ─── Тесты ────────────────────────────────────────────────────────────────────

describe("AdsPage — bulk disable money-flow", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(useAds).mockReturnValue({
      data: { data: MOCK_ADS, total: 2 },
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useAds>);
  });

  // Таблица рендерит объявления из useAds.
  it("рендерит объявления из useAds", async () => {
    await renderAdsPage();
    expect(screen.getByText("Объявление 111")).toBeInTheDocument();
    expect(screen.getByText("Объявление 222")).toBeInTheDocument();
  });

  // Выбор строк → BulkActionBar появляется.
  it("выбор строк показывает BulkActionBar", async () => {
    const user = userEvent.setup();
    await renderAdsPage();

    await selectAllRows(user);

    const bar = screen.getByRole("toolbar");
    expect(bar).toBeInTheDocument();
    // Счётчик: «2 выбрано».
    expect(within(bar).getByText("2")).toBeInTheDocument();
  });

  // Клик Disable → ConfirmDialog с правильным счётчиком + поле ввода.
  it("кнопка Disable открывает ConfirmDialog с confirm-with-typing", async () => {
    const user = userEvent.setup();
    await renderAdsPage();

    await selectAllRows(user);

    const bar = screen.getByRole("toolbar");
    await user.click(within(bar).getByRole("button", { name: /Отключить 2 объявлений/i }));

    // ConfirmDialog открылся (заголовок).
    expect(screen.getByText(/Отключить 2 объявлений\?/i)).toBeInTheDocument();
    // Поле ввода DISABLE присутствует (по placeholder).
    expect(screen.getByPlaceholderText("DISABLE")).toBeInTheDocument();
  });

  // MONEY: ввод DISABLE + confirm → useBulkDisable c idempotency_token отдельным полем.
  it("MONEY: ввод DISABLE + confirm вызывает useBulkDisable с idempotency_token отдельным полем", async () => {
    const user = userEvent.setup();
    await renderAdsPage();

    await selectAllRows(user);

    const bar = screen.getByRole("toolbar");
    await user.click(within(bar).getByRole("button", { name: /Отключить 2 объявлений/i }));

    // До ввода DISABLE кнопка подтверждения disabled — мутация не должна вызваться.
    const confirmBtn = screen.getByRole("button", { name: /^Отключить 2$/i });
    expect(confirmBtn).toBeDisabled();

    // Печатаем DISABLE → кнопка активируется.
    const input = screen.getByPlaceholderText("DISABLE");
    await user.type(input, "DISABLE");
    expect(confirmBtn).toBeEnabled();

    await user.click(confirmBtn);

    // useBulkDisable вызван ровно один раз.
    expect(mockBulkDisable).toHaveBeenCalledOnce();

    const callArg = mockBulkDisable.mock.calls[0]?.[0] as {
      fb_ad_ids: string[];
      idempotency_token: string;
      reason: string;
    };

    // fb_ad_ids содержит оба объявления.
    expect(callArg.fb_ad_ids).toEqual(expect.arrayContaining(["111", "222"]));
    expect(callArg.fb_ad_ids).toHaveLength(2);

    // MONEY: idempotency_token передан ОТДЕЛЬНЫМ полем (backend требует min_length=1),
    // это UUID v4 (формат 8-4-4-4-12). Раньше UUID прятался в reason → backend 422.
    const uuidRegex =
      /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
    expect(callArg.idempotency_token).toMatch(uuidRegex);
    // reason больше НЕ содержит idempotency: — токен ушёл в своё поле.
    expect(callArg.reason).not.toMatch(/idempotency:/);
  });

  // Empty state при отсутствии объявлений.
  it("рендерит empty state когда нет объявлений", async () => {
    vi.mocked(useAds).mockReturnValue({
      data: { data: [], total: 0 },
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useAds>);

    await renderAdsPage();

    expect(screen.getByText(/Объявлений нет/i)).toBeInTheDocument();
  });
});

// ─── Partial failure (аудит 2026-07-12, H-8) ─────────────────────────────────
// Бэк возвращает HTTP 200 с {created, skipped, failed}; молчание про failed
// означало «все остановятся», пока часть адов продолжала жечь бюджет.

describe("AdsPage — bulk disable partial failure (H-8)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(useAds).mockReturnValue({
      data: { data: MOCK_ADS, total: 2 },
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useAds>);
  });

  // MONEY: непустой failed → оператор видит toast.error со списком провалившихся адов.
  it("MONEY: failed в ответе bulk-disable показывает toast.error", async () => {
    const { toast } = await import("../../components/ui/Toast");
    const errorSpy = vi.spyOn(toast, "error");
    const successSpy = vi.spyOn(toast, "success");
    mockBulkDisable.mockResolvedValueOnce({
      created: [{ id: "1" }],
      skipped: [],
      failed: [{ fb_ad_id: "222", reason: "ad не найден в каталоге" }],
    });

    const user = userEvent.setup();
    await renderAdsPage();
    await selectAllRows(user);
    const bar = screen.getByRole("toolbar");
    await user.click(within(bar).getByRole("button", { name: /Отключить 2 объявлений/i }));
    const input = screen.getByPlaceholderText("DISABLE");
    await user.type(input, "DISABLE");
    await user.click(screen.getByRole("button", { name: /^Отключить 2$/i }));

    await waitFor(() => {
      expect(errorSpy).toHaveBeenCalledWith(
        expect.stringContaining("задач на отключение: 1"),
        expect.stringContaining("222"),
      );
    });
    // Успешная часть тоже отражена (создана 1 задача).
    expect(successSpy).toHaveBeenCalledWith(
      expect.stringContaining("задач на отключение: 1"),
    );
  });

  // Все провалились → только error-toast, success не показываем.
  it("MONEY: полный провал bulk-disable не показывает success-toast", async () => {
    const { toast } = await import("../../components/ui/Toast");
    const errorSpy = vi.spyOn(toast, "error");
    const successSpy = vi.spyOn(toast, "success");
    mockBulkDisable.mockResolvedValueOnce({
      created: [],
      skipped: [],
      failed: [
        { fb_ad_id: "111", reason: "гонка" },
        { fb_ad_id: "222", reason: "гонка" },
      ],
    });

    const user = userEvent.setup();
    await renderAdsPage();
    await selectAllRows(user);
    const bar = screen.getByRole("toolbar");
    await user.click(within(bar).getByRole("button", { name: /Отключить 2 объявлений/i }));
    const input = screen.getByPlaceholderText("DISABLE");
    await user.type(input, "DISABLE");
    await user.click(screen.getByRole("button", { name: /^Отключить 2$/i }));

    await waitFor(() => {
      expect(errorSpy).toHaveBeenCalled();
    });
    expect(successSpy).not.toHaveBeenCalled();
  });
});
