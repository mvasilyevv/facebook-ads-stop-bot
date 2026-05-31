// Тесты DraftsPage: рендер DraftCard, approve-флоу, ACL-blocked карточка, empty-state.
// Стратегия: тестируем чистые функции (buildSummary, buildDiff, isExpiringSoon)
// и presentation-поведение через рендер DraftCard и DraftsPage с мок-хуками.

import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import React from "react";

// Мок TanStack Router — убираем зависимость от route tree
vi.mock("@tanstack/react-router", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@tanstack/react-router")>();
  return {
    ...actual,
    createFileRoute: (_path: string) => (opts: { component: React.ComponentType }) => opts,
    useNavigate: () => vi.fn(),
  };
});

// Мок API хуков drafts
vi.mock("@/lib/api/drafts", () => ({
  useDrafts: vi.fn(),
  useApproveDraft: vi.fn(),
  useCancelDraft: vi.fn(),
}));

import { DraftCard } from "@/components/domain/DraftCard";
import { Skeleton } from "@/components/ui/Skeleton";
import { buildSummary, buildDiff, isExpiringSoon, mutationLabel } from "@/routes/drafts/index";
import type { TaskQueueRow } from "@/lib/types/api";

/** Мок TaskQueueRow — соответствует РЕАЛЬНОЙ структуре backend (TaskQueueRowOut). */
function makeDraft(overrides: Partial<TaskQueueRow> = {}): TaskQueueRow {
  return {
    id: "abc12345-0000-0000-0000-000000000001",
    fb_ad_id: "120211984573",
    ad_name: "CR2 | DRC | MV | Tyver | 25.03",
    task_type: "disable",
    status: "PENDING",
    attempt_count: 0,
    max_attempts: 5,
    requested_by: "markvasilev",
    requested_by_chat_id: 12345,
    created_at: "2026-05-29T10:00:00Z",
    updated_at: "2026-05-29T10:00:00Z",
    next_attempt_at: null,
    last_error_message: null,
    ...overrides,
  };
}

// ─── Чистые функции ────────────────────────────────────────────────────────

describe("buildSummary", () => {
  // Корректный заголовок для disable-задачи
  it("disable включает ad_name", () => {
    const row = makeDraft({ task_type: "disable", ad_name: "CR2 | DRC" });
    expect(buildSummary(row)).toContain("CR2 | DRC");
    expect(buildSummary(row)).toContain("Отключить");
  });

  // Корректный заголовок для enable-задачи
  it("enable содержит Activate", () => {
    const row = makeDraft({ task_type: "enable" });
    expect(buildSummary(row)).toContain("Включить");
  });

  // meta_api_mutation получает fallback на id
  it("meta_api_mutation использует id как fallback", () => {
    const row = makeDraft({ task_type: "meta_api_mutation", ad_name: null, fb_ad_id: null });
    const s = buildSummary(row);
    expect(s).toContain("Действие с API");
    expect(s).toContain(row.id.slice(0, 8));
  });
});

describe("buildDiff", () => {
  // diff содержит changed row для disable
  it("disable-задача имеет highlighted строку effective_status", () => {
    const row = makeDraft({ task_type: "disable" });
    const diff = buildDiff(row);
    const changed = diff.find((r) => r.key === "Статус");
    expect(changed).toBeDefined();
    expect(changed?.highlight).toBe(true);
    expect(changed?.current).toBe("ACTIVE");
    expect(changed?.target).toBe("PAUSED");
  });

  // enable меняет PAUSED → ACTIVE
  it("enable-задача показывает PAUSED → ACTIVE", () => {
    const row = makeDraft({ task_type: "enable" });
    const diff = buildDiff(row);
    const changed = diff.find((r) => r.key === "Статус");
    expect(changed?.current).toBe("PAUSED");
    expect(changed?.target).toBe("ACTIVE");
  });
});

describe("isExpiringSoon", () => {
  // null → false
  it("null → false", () => {
    expect(isExpiringSoon(null)).toBe(false);
  });

  // созданная 23.5 часов назад → true (< 30 мин до истечения)
  it("23.5 часа назад → true", () => {
    const d = new Date();
    d.setMinutes(d.getMinutes() - 23 * 60 - 30);
    expect(isExpiringSoon(d.toISOString())).toBe(true);
  });

  // созданная 1 час назад → false
  it("1 час назад → false", () => {
    const d = new Date();
    d.setHours(d.getHours() - 1);
    expect(isExpiringSoon(d.toISOString())).toBe(false);
  });
});

describe("mutationLabel", () => {
  it("disable → Отключить объявление", () => {
    expect(mutationLabel("disable")).toBe("Отключить объявление");
  });
  it("enable → Включить объявление", () => {
    expect(mutationLabel("enable")).toBe("Включить объявление");
  });
  it("неизвестный тип остаётся как есть", () => {
    expect(mutationLabel("plan_run")).toBe("plan_run");
  });
});

// ─── DraftCard компонент ────────────────────────────────────────────────────

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

// ─── DraftsPage (через presentation-компоненты) ─────────────────────────────

// Тестируем DraftCard и вспомогательные компоненты напрямую (как в ads.test.tsx),
// без полного route-контекста.

// Мини-враппер для DraftCard + логика ACL
function TestDraftsList({ drafts, currentChatId = null }: { drafts: TaskQueueRow[]; currentChatId?: number | null }) {
  function isBlocked(draft: TaskQueueRow): boolean {
    return (
      currentChatId !== null &&
      draft.requested_by_chat_id !== null &&
      draft.requested_by_chat_id !== currentChatId
    );
  }

  if (drafts.length === 0) {
    return <div>Нет черновиков на подтверждение</div>;
  }

  return (
    <div>
      {drafts.map((draft) => (
        <DraftCard
          key={draft.id}
          taskType={mutationLabel(draft.task_type)}
          createdAt={draft.created_at}
          requestedBy={draft.requested_by}
          summary={buildSummary(draft)}
          diff={buildDiff(draft)}
          canApprove={!isBlocked(draft)}
          approveDisabledReason={
            isBlocked(draft) ? `Only the owner can approve this draft.` : undefined
          }
          onApprove={vi.fn()}
          onCancel={vi.fn()}
        />
      ))}
    </div>
  );
}

describe("DraftsPage (presentation)", () => {
  // Empty state когда черновиков нет
  it("показывает empty state при пустом списке", () => {
    render(<TestDraftsList drafts={[]} />);
    expect(screen.getByText(/нет черновиков/i)).toBeInTheDocument();
  });

  // Рендер карточки из реального типа TaskQueueRow
  it("рендерит DraftCard для каждого черновика в списке", () => {
    const drafts = [
      makeDraft({ id: "id-001", ad_name: "CR2 | DRC | MV | 25.03" }),
      makeDraft({ id: "id-002", task_type: "enable", ad_name: "UA17 | SP | 24.03" }),
    ];

    render(<TestDraftsList drafts={drafts} />);
    // Summaries рендерятся внутри карточки (font-display bold)
    expect(screen.getByText(/Отключить «CR2 \| DRC \| MV \| 25\.03»/)).toBeInTheDocument();
    expect(screen.getByText(/Включить «UA17 \| SP \| 24\.03»/)).toBeInTheDocument();
  });

  // ACL-blocked: другой chat_id
  it("ACL-blocked: Approve задизейблен при несовпадении chat_id", () => {
    const draft = makeDraft({ requested_by_chat_id: 12345, requested_by: "anna_buyer" });

    render(<TestDraftsList drafts={[draft]} currentChatId={99999} />);
    // Approve кнопка должна быть задизейблена
    const approveBtn = screen.getByRole("button", { name: /подтвердить/i });
    expect(approveBtn).toBeDisabled();
  });

  // Owner может approve
  it("владелец (совпадение chat_id) видит активный Approve", () => {
    const draft = makeDraft({ requested_by_chat_id: 12345 });

    render(<TestDraftsList drafts={[draft]} currentChatId={12345} />);
    const approveBtn = screen.getByRole("button", { name: /подтвердить/i });
    expect(approveBtn).not.toBeDisabled();
  });

  // Скелетоны загрузки (рендер напрямую)
  it("Skeleton имеет role=status при рендере", () => {
    render(<Skeleton height={80} />);
    expect(screen.getByRole("status")).toBeInTheDocument();
  });
});
