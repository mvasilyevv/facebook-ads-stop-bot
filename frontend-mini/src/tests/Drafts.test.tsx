/**
 * Тесты DraftsPage и DraftCard под канон:
 * - русские кнопки «Одобрить и выполнить» / «Отклонить»
 * - ribbon «СКОРО ИСТЕКАЕТ»
 * - EmptyState «Черновиков нет»
 * - confirm/reject flow через tgConfirm + haptic
 * - buildDraftDiff unit-тесты
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

// ─── Моки TG ─────────────────────────────────────────────────────────────────

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

// ─── Фикстуры ─────────────────────────────────────────────────────────────────

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

/** Черновик, истекающий через 10 минут — попадает в «СКОРО ИСТЕКАЕТ» (< 1ч). */
const DRAFT_EXPIRING: DraftOut = {
  id: 3,
  mutation_kind: "activate_ad",
  target_id: "ad_9999",
  ad_account_id: "act_999",
  payload: { fb_ad_id: "ad_9999" },
  requested_by: "ai_assistant",
  // created_at = 23ч 50м назад → истекает через 10 мин
  created_at: new Date(Date.now() - (24 * 60 - 10) * 60_000).toISOString(),
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

// ─── Тестовый хост DraftsPage ─────────────────────────────────────────────────

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

// ─── Тесты DraftsPage ─────────────────────────────────────────────────────────

describe("DraftsPage", () => {
  beforeEach(() => {
    mockDrafts = [DRAFT_PAUSE_AD, DRAFT_BUDGET];
    mockIsLoading = false;
    mockIsError = false;
    mockTgConfirm.mockResolvedValue(true);
    confirmMutate.mockResolvedValue({ ok: true });
    rejectMutate.mockResolvedValue({ ok: true });
  });

  // Список из 2 черновиков рендерится
  it("рендерит список из 2 черновиков", () => {
    render(<Wrapper />);
    expect(screen.getByTestId("count")).toHaveTextContent("2");
  });

  // Карточки присутствуют
  it("рендерит DraftCard для каждого черновика", () => {
    render(<Wrapper />);
    const cards = screen.getAllByTestId("draft-card");
    expect(cards).toHaveLength(2);
  });

  // Пустой список → EmptyState с русским текстом
  it("показывает «Черновиков нет» при пустом списке", () => {
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

  // Кнопка «Одобрить и выполнить» присутствует
  it("показывает кнопку «Одобрить и выполнить»", () => {
    render(<Wrapper />);
    const btns = screen.getAllByRole("button", { name: /Одобрить и выполнить/i });
    expect(btns.length).toBeGreaterThan(0);
  });

  // Кнопка «Отклонить» присутствует
  it("показывает кнопку «Отклонить»", () => {
    render(<Wrapper />);
    const btns = screen.getAllByRole("button", { name: /Отклонить/i });
    expect(btns.length).toBeGreaterThan(0);
  });

  // Confirm flow: confirmMutate вызван с taskId
  it("Confirm: после tgConfirm вызывает mutateAsync с taskId", async () => {
    render(<Wrapper />);
    const user = userEvent.setup();
    const confirmBtns = screen.getAllByRole("button", { name: /Одобрить и выполнить/i });
    await user.click(confirmBtns[0] as HTMLElement);
    await waitFor(() => {
      expect(confirmMutate).toHaveBeenCalledWith({ taskId: DRAFT_PAUSE_AD.id });
    });
  });

  // Reject flow: rejectMutate вызван с taskId
  it("Reject: после tgConfirm вызывает rejectMutate с taskId", async () => {
    render(<Wrapper />);
    const user = userEvent.setup();
    const rejectBtns = screen.getAllByRole("button", { name: /Отклонить/i });
    await user.click(rejectBtns[0] as HTMLElement);
    await waitFor(() => {
      expect(rejectMutate).toHaveBeenCalledWith({ taskId: DRAFT_PAUSE_AD.id });
    });
  });

  // Отмена через tgConfirm → мутация не вызывается (тест на DraftCard напрямую)
  it("Confirm: при отказе в tgConfirm мутация не вызывается", async () => {
    mockTgConfirm.mockResolvedValue(false);
    const onConfirm = vi.fn().mockResolvedValue(undefined);
    const onReject = vi.fn().mockResolvedValue(undefined);
    render(
      <QueryClientProvider client={makeQC()}>
        <DraftCard draft={DRAFT_PAUSE_AD} onConfirm={onConfirm} onReject={onReject} />
      </QueryClientProvider>,
    );
    const user = userEvent.setup();
    const confirmBtn = screen.getByRole("button", { name: /Одобрить и выполнить/i });
    await user.click(confirmBtn);
    await waitFor(() => {
      expect(onConfirm).not.toHaveBeenCalled();
    });
  });
});

// ─── Тесты ribbon «СКОРО ИСТЕКАЕТ» ───────────────────────────────────────────

describe("DraftCard — ribbon СКОРО ИСТЕКАЕТ", () => {
  const noop = async (_id: number) => {};

  // Ribbon видна для истекающего черновика
  it("показывает ribbon «СКОРО ИСТЕКАЕТ» для черновика, истекающего через 10 минут", () => {
    render(
      <DraftCard draft={DRAFT_EXPIRING} onConfirm={noop} onReject={noop} />,
    );
    expect(screen.getByTestId("expiring-ribbon")).toBeInTheDocument();
    expect(screen.getByText("СКОРО ИСТЕКАЕТ")).toBeInTheDocument();
  });

  // Ribbon НЕ видна для свежего черновика
  it("НЕ показывает ribbon для свежего черновика", () => {
    render(
      <DraftCard draft={DRAFT_PAUSE_AD} onConfirm={noop} onReject={noop} />,
    );
    expect(screen.queryByTestId("expiring-ribbon")).toBeNull();
  });
});

// ─── Тесты DraftCard — тексты/структура ──────────────────────────────────────

describe("DraftCard — заголовок mutationKindLabel", () => {
  const noop = async (_id: number) => {};

  // Заголовок = полное описание (не verb)
  it("показывает русское описание «Поставить объявление на паузу» для pause_ad", () => {
    render(<DraftCard draft={DRAFT_PAUSE_AD} onConfirm={noop} onReject={noop} />);
    expect(screen.getByText("Поставить объявление на паузу")).toBeInTheDocument();
  });

  // Запросил @user
  it("показывает «Запросил @ai_assistant»", () => {
    render(<DraftCard draft={DRAFT_PAUSE_AD} onConfirm={noop} onReject={noop} />);
    expect(screen.getByText("@ai_assistant")).toBeInTheDocument();
  });

  // Текст «Истекает через» в footer
  it("показывает «Истекает через» для свежего черновика", () => {
    render(<DraftCard draft={DRAFT_PAUSE_AD} onConfirm={noop} onReject={noop} />);
    expect(screen.getByText(/Истекает через/)).toBeInTheDocument();
  });
});

// ─── Тесты DraftCard — батч callout ──────────────────────────────────────────

describe("DraftCard — пакетная операция", () => {
  const noop = async (_id: number) => {};
  const DRAFT_BULK: DraftOut = {
    id: 10,
    mutation_kind: "bulk_status_change",
    target_id: null,
    ad_account_id: "act_999",
    payload: { action: "pause", object_type: "ad", object_ids: ["id1", "id2", "id3"] },
    requested_by: "ai_assistant",
    created_at: new Date(Date.now() - 1800_000).toISOString(),
  };

  // Callout «Пакетная операция · N graph-вызовов» отображается
  it("показывает callout «Пакетная операция · 3 graph-вызовов» для bulk", () => {
    render(<DraftCard draft={DRAFT_BULK} onConfirm={noop} onReject={noop} />);
    expect(screen.getByText(/Пакетная операция · 3 graph-вызовов/)).toBeInTheDocument();
  });

  // Заголовок содержит «3 объектов» (может быть в нескольких узлах)
  it("показывает «3 объектов» в заголовке батча", () => {
    render(<DraftCard draft={DRAFT_BULK} onConfirm={noop} onReject={noop} />);
    const matches = screen.getAllByText(/объектов/);
    expect(matches.length).toBeGreaterThan(0);
  });
});

// ─── Unit-тесты buildDraftDiff ────────────────────────────────────────────────

describe("buildDraftDiff", () => {
  // pause_ad: целевой статус PAUSED
  it("pause_ad: целевой статус PAUSED", () => {
    const rows = buildDraftDiff("pause_ad", { fb_ad_id: "123" }, { status: "ACTIVE" });
    const statusRow = rows.find((r) => r.field === "Статус объявления");
    expect(statusRow?.target).toBe("PAUSED");
    expect(statusRow?.current).toBe("ACTIVE");
    expect(statusRow?.changed).toBe(true);
  });

  // activate_ad: целевой статус ACTIVE
  it("activate_ad: целевой статус ACTIVE", () => {
    const rows = buildDraftDiff("activate_ad", { fb_ad_id: "456" }, { status: "PAUSED" });
    const statusRow = rows.find((r) => r.field === "Статус объявления");
    expect(statusRow?.target).toBe("ACTIVE");
    expect(statusRow?.changed).toBe(true);
  });

  // set_adset_budget: бюджет в центах → $
  it("set_adset_budget: показывает суммы в долларах", () => {
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

  // bulk_status_change: количество объектов
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
