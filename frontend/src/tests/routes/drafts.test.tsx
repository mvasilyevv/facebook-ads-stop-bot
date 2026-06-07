/**
 * Тесты страницы Drafts (routes/drafts/index.tsx).
 *
 * Что проверяем:
 *   - Skeleton при загрузке
 *   - Список DraftCard отображается по данным хука
 *   - Filter-pills по mutation_kind фильтруют список
 *   - Sort expiring-first: истекающий черновик отображается первым
 *   - Approve-flow: ConfirmDialog → useConfirmDraft вызывается
 *   - Cancel-flow: ConfirmDialog → useRejectDraft вызывается
 *   - EmptyState при пустом списке
 *   - ErrorState при ошибке
 */

import { render, screen, within } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import { describe, it, expect, vi, beforeEach } from "vitest";
import type { DraftOut } from "@fb/shared";

// ─── Мок хуков ───────────────────────────────────────────────────────────────

// Мокаем useMetaDrafts, useConfirmDraft, useRejectDraft
const mockUseMetaDrafts = vi.fn();
const mockConfirmMutateAsync = vi.fn().mockResolvedValue({ ok: true });
const mockRejectMutateAsync = vi.fn().mockResolvedValue({ ok: true });

vi.mock("@/lib/api/drafts", () => ({
  useMetaDrafts: () => mockUseMetaDrafts(),
  useConfirmDraft: () => ({
    mutateAsync: mockConfirmMutateAsync,
    isPending: false,
    variables: undefined,
  }),
  useRejectDraft: () => ({
    mutateAsync: mockRejectMutateAsync,
    isPending: false,
    variables: undefined,
  }),
}));

// Мокаем TanStack Router createFileRoute — возвращаем обёртку для прямого рендера компонента
vi.mock("@tanstack/react-router", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@tanstack/react-router")>();
  return {
    ...actual,
    createFileRoute: (_path: string) => (opts: { component: React.FC }) => opts,
  };
});

import { Route } from "@/routes/drafts/index";

// createFileRoute мок возвращает сам объект опций, компонент в .component
const DraftsPage = (Route as unknown as { component: React.FC }).component;

// ─── Фабрики тестовых данных ──────────────────────────────────────────────────

function makeDraft(overrides: Partial<DraftOut> = {}): DraftOut {
  return {
    id: Math.floor(Math.random() * 10000),
    mutation_kind: "pause_ad",
    requested_by: "test_user",
    created_at: new Date().toISOString(),
    payload: {},
    ...overrides,
  };
}

/** Черновик, истекающий скоро (created 23ч 15м назад). */
function makeExpiringDraft(overrides: Partial<DraftOut> = {}): DraftOut {
  return makeDraft({
    created_at: new Date(Date.now() - 23 * 60 * 60 * 1000 - 15 * 60 * 1000).toISOString(),
    ...overrides,
  });
}

// ─── Хелпер рендера ──────────────────────────────────────────────────────────

function renderDrafts() {
  // DraftsPage — прямой функциональный компонент
  return render(<DraftsPage />);
}

// ─── Тесты ────────────────────────────────────────────────────────────────────

describe("DraftsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockConfirmMutateAsync.mockResolvedValue({ ok: true });
    mockRejectMutateAsync.mockResolvedValue({ ok: true });
  });

  // Skeleton при загрузке
  it("отображает skeleton при isLoading=true", () => {
    mockUseMetaDrafts.mockReturnValue({ data: undefined, isLoading: true, isError: false });
    renderDrafts();
    // Skeleton рендерит role="status"
    expect(screen.getAllByRole("status").length).toBeGreaterThan(0);
  });

  // ErrorState при ошибке
  it("отображает ErrorState при isError=true", () => {
    mockUseMetaDrafts.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error("network fail"),
      refetch: vi.fn(),
    });
    renderDrafts();
    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(screen.getByText(/network fail/i)).toBeInTheDocument();
  });

  // EmptyState при пустом списке
  it("отображает EmptyState при пустом списке черновиков", () => {
    mockUseMetaDrafts.mockReturnValue({ data: [], isLoading: false, isError: false });
    renderDrafts();
    expect(screen.getByText(/черновиков нет/i)).toBeInTheDocument();
  });

  // Список DraftCard по данным хука
  it("отображает DraftCard для каждого черновика", () => {
    const drafts = [
      makeDraft({ id: 1, mutation_kind: "pause_ad" }),
      makeDraft({ id: 2, mutation_kind: "set_adset_budget", payload: { budget_cents: 5000 } }),
    ];
    mockUseMetaDrafts.mockReturnValue({ data: drafts, isLoading: false, isError: false });
    renderDrafts();
    // Два listitem
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
  });

  // Filter-pills по mutation_kind
  it("filter pill по mutation_kind фильтрует список", async () => {
    const drafts = [
      makeDraft({ id: 1, mutation_kind: "pause_ad" }),
      makeDraft({ id: 2, mutation_kind: "set_adset_budget", payload: { budget_cents: 3000 } }),
    ];
    mockUseMetaDrafts.mockReturnValue({ data: drafts, isLoading: false, isError: false });
    renderDrafts();

    // Изначально 2 элемента
    expect(screen.getAllByRole("listitem")).toHaveLength(2);

    // Нажимаем pill "Изменить бюджет адсета"
    const toolbar = screen.getByRole("toolbar");
    const budgetPill = within(toolbar).getByText(/изменить бюджет/i);
    await userEvent.click(budgetPill);

    // Теперь только 1
    expect(screen.getAllByRole("listitem")).toHaveLength(1);
  });

  // Повторный клик на активный pill сбрасывает фильтр
  it("повторный клик на активный pill сбрасывает фильтр", async () => {
    const drafts = [
      makeDraft({ id: 1, mutation_kind: "pause_ad" }),
      makeDraft({ id: 2, mutation_kind: "set_adset_budget", payload: { budget_cents: 3000 } }),
    ];
    mockUseMetaDrafts.mockReturnValue({ data: drafts, isLoading: false, isError: false });
    renderDrafts();

    const toolbar = screen.getByRole("toolbar");
    const budgetPill = within(toolbar).getByText(/изменить бюджет/i);
    await userEvent.click(budgetPill);
    expect(screen.getAllByRole("listitem")).toHaveLength(1);

    // Кликаем снова — сброс на "all"
    await userEvent.click(budgetPill);
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
  });

  // Sort expiring-first: истекающий черновик отображается первым
  it("истекающий черновик отображается первым в списке", () => {
    const normalDraft = makeDraft({ id: 100, mutation_kind: "activate_ad" });
    const expiringDraft = makeExpiringDraft({ id: 200, mutation_kind: "pause_ad" });

    // Передаём normal первым, expiring вторым
    mockUseMetaDrafts.mockReturnValue({
      data: [normalDraft, expiringDraft],
      isLoading: false,
      isError: false,
    });
    renderDrafts();

    const items = screen.getAllByRole("listitem");
    // Первый элемент должен содержать ribbon "EXPIRING SOON"
    expect(within(items[0]!).getByText("СКОРО ИСТЕКАЕТ")).toBeInTheDocument();
  });

  // Approve flow: кнопка Approve открывает ConfirmDialog
  it("кнопка «Одобрить и выполнить» открывает ConfirmDialog подтверждения", async () => {
    const draft = makeDraft({ id: 42, mutation_kind: "pause_ad" });
    mockUseMetaDrafts.mockReturnValue({ data: [draft], isLoading: false, isError: false });
    renderDrafts();

    // Нажимаем Approve & execute
    await userEvent.click(screen.getByRole("button", { name: /одобрить и выполнить/i }));

    // ConfirmDialog открылся — есть заголовок "Подтвердить и выполнить?"
    expect(screen.getByText(/подтвердить и выполнить/i)).toBeInTheDocument();
  });

  // Approve flow: подтверждение вызывает useConfirmDraft
  it("подтверждение в ConfirmDialog вызывает confirmMutation с id черновика", async () => {
    const draft = makeDraft({ id: 42, mutation_kind: "pause_ad" });
    mockUseMetaDrafts.mockReturnValue({ data: [draft], isLoading: false, isError: false });
    renderDrafts();

    await userEvent.click(screen.getByRole("button", { name: /одобрить и выполнить/i }));

    // Нажимаем кнопку "Approve & execute" в диалоге
    const confirmBtn = screen.getByRole("button", { name: /^одобрить и выполнить$/i });
    await userEvent.click(confirmBtn);

    expect(mockConfirmMutateAsync).toHaveBeenCalledWith("42");
  });

  // Cancel flow: кнопка Cancel открывает ConfirmDialog
  it("кнопка «Отклонить» открывает ConfirmDialog отмены", async () => {
    const draft = makeDraft({ id: 55, mutation_kind: "pause_ad" });
    mockUseMetaDrafts.mockReturnValue({ data: [draft], isLoading: false, isError: false });
    renderDrafts();

    await userEvent.click(screen.getByRole("button", { name: /^отклонить$/i }));

    // ConfirmDialog открылся — есть заголовок (h2) и кнопка подтверждения (h2 role=heading)
    expect(screen.getByRole("heading", { name: /отменить черновик/i })).toBeInTheDocument();
  });

  // Cancel flow: подтверждение вызывает useRejectDraft
  it("подтверждение отмены вызывает rejectMutation с id черновика", async () => {
    const draft = makeDraft({ id: 55, mutation_kind: "pause_ad" });
    mockUseMetaDrafts.mockReturnValue({ data: [draft], isLoading: false, isError: false });
    renderDrafts();

    await userEvent.click(screen.getByRole("button", { name: /^отклонить$/i }));

    // В диалоге нажимаем "Отменить черновик"
    const confirmBtn = screen.getByRole("button", { name: /^отменить черновик$/i });
    await userEvent.click(confirmBtn);

    expect(mockRejectMutateAsync).toHaveBeenCalledWith("55");
  });
});
