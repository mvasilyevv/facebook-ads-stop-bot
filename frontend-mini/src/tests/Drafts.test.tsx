/**
 * Тесты DraftsPage: список черновиков, diff через buildDraftDiff, confirm/reject flow.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { buildDraftDiff } from "@fb/shared";
import type { DraftOut } from "@fb/shared";
import { EmptyState } from "@/components/ui";
import { DraftCard } from "@/components/domain/DraftCard";
import { useTmaDrafts, useTmaConfirmDraft, useTmaRejectDraft } from "@/lib/api";

// ─── Моки роутера ────────────────────────────────────────────────────────────

vi.mock("@tanstack/react-router", () => ({
  createFileRoute: () => ({ component: (c: unknown) => c }),
  useNavigate: () => vi.fn(),
  useRouter: () => ({ navigate: vi.fn(), history: { back: vi.fn() } }),
  useLocation: () => ({ pathname: "/drafts/" }),
}));

// ─── Моки TG ────────────────────────────────────────────────────────────────

const mockTgConfirm = vi.fn();

vi.mock("@/lib/tg", () => ({
  haptic: { impact: vi.fn(), notify: vi.fn(), selection: vi.fn() },
  tgConfirm: () => mockTgConfirm(),
  tgAlert: vi.fn().mockResolvedValue(undefined),
  openLink: vi.fn(),
  registerBackButton: () => () => {},
  hideBackButton: vi.fn(),
  initTheme: vi.fn(),
  getInitData: () => "",
}));

// ─── Фикстуры ────────────────────────────────────────────────────────────────

const DRAFT_PAUSE_AD: DraftOut = {
  id: 1,
  mutation_kind: "pause_ad",
  target_id: "120215511234",
  ad_account_id: "act_999",
  payload: { fb_ad_id: "120215511234" },
  requested_by: "ai_assistant",
  created_at: new Date(Date.now() - 3600_000).toISOString(),
};

const DRAFT_BUDGET: DraftOut = {
  id: 2,
  mutation_kind: "set_adset_budget",
  target_id: "adset_5678",
  ad_account_id: "act_999",
  payload: { budget_cents: 5000, budget_type: "daily" },
  requested_by: "user",
  created_at: new Date(Date.now() - 600_000).toISOString(),
};

// ─── Моки API ────────────────────────────────────────────────────────────────

const confirmMutate = vi.fn().mockResolvedValue({ ok: true });
const rejectMutate = vi.fn().mockResolvedValue({ ok: true });

let mockDrafts: DraftOut[] = [];
let mockIsLoading = false;
let mockIsError = false;

vi.mock("@/lib/api", () => ({
  useTmaDrafts: () => ({
    data: mockDrafts,
    isLoading: mockIsLoading,
    isError: mockIsError,
    error: mockIsError ? new Error("Ошибка загрузки черновиков") : null,
    refetch: vi.fn(),
  }),
  useTmaConfirmDraft: () => ({
    mutateAsync: confirmMutate,
    isPending: false,
  }),
  useTmaRejectDraft: () => ({
    mutateAsync: rejectMutate,
    isPending: false,
  }),
}));

// ─── Компонент под тест ───────────────────────────────────────────────────────

function TestDraftsPage() {
  const { data: drafts = [], isLoading, isError, error } = useTmaDrafts();
  const confirmDraft = useTmaConfirmDraft();
  const rejectDraft = useTmaRejectDraft();

  if (isLoading) return <div data-testid="loading">Загрузка...</div>;
  if (isError) return <div data-testid="error">{(error as Error)?.message}</div>;
  if (drafts.length === 0) return <EmptyState title="Черновиков нет" />;

  return (
    <div>
      <p data-testid="count">{drafts.length} черновика(ов)</p>
      {drafts.map((draft) => (
        <DraftCard
          key={draft.id}
          draft={draft}
          onConfirm={async (id) => { await confirmDraft.mutateAsync({ taskId: id }); }}
          onReject={async (id) => { await rejectDraft.mutateAsync({ taskId: id }); }}
        />
      ))}
    </div>
  );
}

const makeQC = () =>
  new QueryClient({ defaultOptions: { queries: { retry: false } } });

function Wrapper() {
  return (
    <QueryClientProvider client={makeQC()}>
      <TestDraftsPage />
    </QueryClientProvider>
  );
}

// ─── Тесты ───────────────────────────────────────────────────────────────────

describe("DraftsPage", () => {
  beforeEach(() => {
    mockDrafts = [DRAFT_PAUSE_AD, DRAFT_BUDGET];
    mockIsLoading = false;
    mockIsError = false;
    mockTgConfirm.mockResolvedValue(true);
    confirmMutate.mockResolvedValue({ ok: true });
    rejectMutate.mockResolvedValue({ ok: true });
  });

  // Список черновиков рендерится
  it("рендерит список из 2 черновиков", () => {
    render(<Wrapper />);
    expect(screen.getByTestId("count")).toHaveTextContent("2");
  });

  // PAUSE verb показывается
  it("показывает verb ПАУЗА для pause_ad", () => {
    render(<Wrapper />);
    expect(screen.getByText("ПАУЗА")).toBeInTheDocument();
  });

  // Бюджет verb
  it("показывает verb ИЗМЕНИТЬ БЮДЖЕТ для set_adset_budget", () => {
    render(<Wrapper />);
    expect(screen.getByText("ИЗМЕНИТЬ БЮДЖЕТ")).toBeInTheDocument();
  });

  // Пустой список → EmptyState
  it("показывает EmptyState при пустом списке", () => {
    mockDrafts = [];
    render(<Wrapper />);
    expect(screen.getByText("Черновиков нет")).toBeInTheDocument();
  });

  // Ошибка загрузки
  it("показывает ошибку при isError", () => {
    mockIsError = true;
    mockDrafts = [];
    render(<Wrapper />);
    expect(screen.getByTestId("error")).toBeInTheDocument();
  });

  // Confirm flow: confirm → mutateAsync вызван
  it("Confirm: после подтверждения вызывает mutateAsync с taskId", async () => {
    render(<Wrapper />);
    const user = userEvent.setup();
    const confirmBtns = screen.getAllByRole("button", { name: /Подтвердить/i });
    expect(confirmBtns.length).toBeGreaterThan(0);
    await user.click(confirmBtns[0] as HTMLElement);
    await waitFor(() => {
      expect(confirmMutate).toHaveBeenCalledWith({ taskId: DRAFT_PAUSE_AD.id });
    });
  });

  // Reject flow: confirm → rejectMutate вызван
  it("Reject: после подтверждения отмены вызывает rejectMutate", async () => {
    render(<Wrapper />);
    const user = userEvent.setup();
    const rejectBtns = screen.getAllByRole("button", { name: /Отменить/i });
    expect(rejectBtns.length).toBeGreaterThan(0);
    await user.click(rejectBtns[0] as HTMLElement);
    await waitFor(() => {
      expect(rejectMutate).toHaveBeenCalledWith({ taskId: DRAFT_PAUSE_AD.id });
    });
  });
});

// ─── Unit-тест buildDraftDiff из @fb/shared ───────────────────────────────────

describe("buildDraftDiff", () => {
  // pause_ad: diff показывает смену статуса
  it("pause_ad: целевой статус PAUSED", () => {
    const rows = buildDraftDiff("pause_ad", { fb_ad_id: "123" }, { status: "ACTIVE" });
    const statusRow = rows.find((r) => r.field === "Статус объявления");
    expect(statusRow?.target).toBe("PAUSED");
    expect(statusRow?.current).toBe("ACTIVE");
    expect(statusRow?.changed).toBe(true);
  });

  // activate_ad: target ACTIVE
  it("activate_ad: целевой статус ACTIVE", () => {
    const rows = buildDraftDiff("activate_ad", { fb_ad_id: "456" }, { status: "PAUSED" });
    const statusRow = rows.find((r) => r.field === "Статус объявления");
    expect(statusRow?.target).toBe("ACTIVE");
    expect(statusRow?.changed).toBe(true);
  });

  // set_adset_budget: бюджет в центах
  it("set_adset_budget: показывает сумму в долларах", () => {
    const rows = buildDraftDiff(
      "set_adset_budget",
      { budget_cents: 5000, budget_type: "daily" },
      { daily_budget_cents: 3000 },
    );
    const budgetRow = rows.find((r) => r.field === "Суточный бюджет");
    expect(budgetRow?.target).toBe("$50.00");
    expect(budgetRow?.current).toBe("$30.00");
    expect(budgetRow?.changed).toBe(true);
  });

  // bulk_status_change: N объектов
  it("bulk_status_change: считает количество объектов", () => {
    const ids = ["id1", "id2", "id3"];
    const rows = buildDraftDiff("bulk_status_change", {
      action: "pause",
      object_type: "ad",
      object_ids: ids,
    });
    const countRow = rows.find((r) => r.field === "Количество объектов");
    expect(countRow?.target).toBe("3");
    expect(countRow?.changed).toBe(true);
  });
});
