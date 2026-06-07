/**
 * Тесты DraftCard — карточка черновика AI-мутации.
 */

import { render, screen } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import { describe, it, expect, vi } from "vitest";
import { DraftCard } from "@/components/domain/drafts/DraftCard";
import { mutationKindVerb } from "@fb/shared";
import type { DraftOut } from "@fb/shared";

// Базовый черновик (не истекающий — created_at только что)
function makeDraft(overrides: Partial<DraftOut> = {}): DraftOut {
  return {
    id: 42,
    mutation_kind: "pause_ad",
    requested_by: "markvasilev",
    created_at: new Date().toISOString(),
    payload: { fb_ad_id: "120211984573_8761" },
    ...overrides,
  };
}

// Черновик истекающий: created_at = 23ч 15 мин назад (до порога 1ч)
function makeSoonDraft(): DraftOut {
  return makeDraft({
    created_at: new Date(Date.now() - 23 * 60 * 60 * 1000 - 15 * 60 * 1000).toISOString(),
  });
}

describe("DraftCard", () => {
  // Проверяем наличие verb из MUTATION_KIND_VERBS
  it("показывает verb из MUTATION_KIND_VERBS для pause_ad", () => {
    render(<DraftCard draft={makeDraft({ mutation_kind: "pause_ad" })} />);
    const verb = mutationKindVerb("pause_ad");
    expect(screen.getByText(verb, { exact: false })).toBeInTheDocument();
  });

  it("показывает verb ИЗМЕНИТЬ БЮДЖЕТ для set_adset_budget", () => {
    render(
      <DraftCard
        draft={makeDraft({ mutation_kind: "set_adset_budget", payload: { budget_cents: 35000, budget_type: "daily" } })}
      />,
    );
    const verb = mutationKindVerb("set_adset_budget");
    expect(screen.getByText(verb, { exact: false })).toBeInTheDocument();
  });

  // expiring-soon ribbon "EXPIRING SOON" появляется при просроченном черновике
  it("ribbon EXPIRING SOON появляется при isExpiringSoon=true", () => {
    render(<DraftCard draft={makeSoonDraft()} />);
    expect(screen.getByText("EXPIRING SOON")).toBeInTheDocument();
  });

  // Обычный черновик — ribbon НЕ показывается
  it("ribbon отсутствует при нормальном сроке", () => {
    render(<DraftCard draft={makeDraft()} />);
    expect(screen.queryByText("EXPIRING SOON")).not.toBeInTheDocument();
  });

  // canApprove=false → кнопка Approve задизейблена
  it("кнопка Approve задизейблена при canApprove=false (blocked)", () => {
    render(
      <DraftCard
        draft={makeDraft()}
        canApprove={false}
        approveBlockedReason="Owner-only — created by @anna_buyer"
      />,
    );
    const approveBtn = screen.getByRole("button", { name: /approve & execute/i });
    expect(approveBtn).toBeDisabled();
  });

  // canApprove=true → кнопка активна
  it("кнопка Approve активна при canApprove=true", () => {
    const onApprove = vi.fn();
    render(<DraftCard draft={makeDraft()} canApprove onApprove={onApprove} />);
    const approveBtn = screen.getByRole("button", { name: /approve & execute/i });
    expect(approveBtn).not.toBeDisabled();
  });

  // Клик по Approve вызывает onApprove
  it("клик Approve вызывает onApprove", async () => {
    const onApprove = vi.fn();
    render(<DraftCard draft={makeDraft()} canApprove onApprove={onApprove} />);
    await userEvent.click(screen.getByRole("button", { name: /approve & execute/i }));
    expect(onApprove).toHaveBeenCalledTimes(1);
  });

  // Клик Cancel вызывает onCancel
  it("клик Cancel вызывает onCancel", async () => {
    const onCancel = vi.fn();
    render(<DraftCard draft={makeDraft()} canApprove onCancel={onCancel} />);
    await userEvent.click(screen.getByRole("button", { name: /cancel/i }));
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  // Blocked — показывается lock-note с причиной
  it("при blocked показывает approveBlockedReason в lock-note", () => {
    render(
      <DraftCard
        draft={makeDraft()}
        canApprove={false}
        approveBlockedReason="Owner-only — created by @anna_buyer"
      />,
    );
    expect(
      screen.getByText("Owner-only — created by @anna_buyer"),
    ).toBeInTheDocument();
  });

  // batch-warning при batchCallCount > 1
  it("показывает batch-warning callout при batchCallCount=4", () => {
    render(<DraftCard draft={makeDraft()} batchCallCount={4} />);
    expect(screen.getByText(/Batch operation · 4 graph calls/i)).toBeInTheDocument();
  });

  // batch-warning НЕ показывается при batchCallCount=1
  it("НЕ показывает batch-warning при batchCallCount=1", () => {
    render(<DraftCard draft={makeDraft()} batchCallCount={1} />);
    expect(screen.queryByText(/batch operation/i)).not.toBeInTheDocument();
  });

  // requested_by отображается
  it("показывает requested_by в header", () => {
    render(<DraftCard draft={makeDraft({ requested_by: "anna_buyer" })} />);
    expect(screen.getByText(/@anna_buyer/i)).toBeInTheDocument();
  });
});
