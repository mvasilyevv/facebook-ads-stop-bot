// Тесты Drafts: чистая функция isExpiringSoon + presentation DraftCard (approve-флоу,
// ACL-blocked, reasoning). disable/enable-источник убран (H7c) — на Drafts только DRAFT
// meta_api_mutation, поэтому buildSummary/buildDiff/mutationLabel больше не существуют.

import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";

import { DraftCard } from "@/components/domain/DraftCard";
import { Skeleton } from "@/components/ui/Skeleton";
import { isExpiringSoon } from "@/routes/drafts/index";

// ─── isExpiringSoon (UTC, 24h-дедлайн) ───────────────────────────────────────

describe("isExpiringSoon", () => {
  // null → false
  it("null → false", () => {
    expect(isExpiringSoon(null)).toBe(false);
  });

  // создан 23.5 часа назад → true (< 1ч до истечения 24h-дедлайна)
  it("23.5 часа назад → true", () => {
    const d = new Date();
    d.setMinutes(d.getMinutes() - 23 * 60 - 30);
    expect(isExpiringSoon(d.toISOString())).toBe(true);
  });

  // создан 1 час назад → false
  it("1 час назад → false", () => {
    const d = new Date();
    d.setHours(d.getHours() - 1);
    expect(isExpiringSoon(d.toISOString())).toBe(false);
  });
});

// ─── DraftCard компонент ─────────────────────────────────────────────────────

describe("DraftCard", () => {
  // Рендер карточки с базовыми данными
  it("отображает taskType, summary, кнопки", () => {
    const onApprove = vi.fn();
    const onCancel = vi.fn();
    render(
      <DraftCard
        taskType="meta_api / pause_ad"
        createdAt="2026-05-29T10:00:00Z"
        requestedBy="markvasilev"
        summary="Pause ad CR2 | DRC"
        diff={[{ key: "effective_status", current: "ACTIVE", target: "PAUSED", highlight: true }]}
        onApprove={onApprove}
        onCancel={onCancel}
      />,
    );
    expect(screen.getByText("meta_api / pause_ad")).toBeInTheDocument();
    expect(screen.getByText("Pause ad CR2 | DRC")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /подтвердить/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /отмена/i })).toBeInTheDocument();
  });

  // ACL-blocked: кнопка Approve задизейблена, title содержит подсказку
  it("ACL-blocked: Approve задизейблен с подсказкой", () => {
    render(
      <DraftCard
        taskType="meta_api / pause_ad"
        createdAt="2026-05-29T10:00:00Z"
        summary="Pause ad"
        diff={[]}
        canApprove={false}
        approveDisabledReason="Only the owner can approve"
        onApprove={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    const approveBtn = screen.getByRole("button", { name: /подтвердить/i });
    expect(approveBtn).toBeDisabled();
    expect(approveBtn.title).toContain("Only the owner can approve");
  });

  // Клик Approve вызывает onApprove
  it("клик Approve вызывает onApprove", () => {
    const onApprove = vi.fn();
    render(
      <DraftCard
        taskType="meta_api / pause_ad"
        createdAt="2026-05-29T10:00:00Z"
        summary="Pause ad"
        diff={[]}
        onApprove={onApprove}
        onCancel={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /подтвердить/i }));
    expect(onApprove).toHaveBeenCalledOnce();
  });

  // Отображает reasoning-блок если reason передан
  it("отображает reasoning если передан reason", () => {
    render(
      <DraftCard
        taskType="meta_api / pause_ad"
        createdAt="2026-05-29T10:00:00Z"
        summary="Pause ad"
        diff={[]}
        reason="CPL over threshold by 2x"
        onApprove={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    expect(screen.getByText(/CPL over threshold/)).toBeInTheDocument();
  });
});

// ─── Skeleton (loading-плейсхолдер) ──────────────────────────────────────────

describe("Skeleton", () => {
  // role=status для доступности
  it("имеет role=status при рендере", () => {
    render(<Skeleton height={80} />);
    expect(screen.getByRole("status")).toBeInTheDocument();
  });
});
