// Тесты Offers страницы: рендер карточек, empty state, валидация формы.
// Используем presentational-подход (данные через props), без router/QueryClient.

import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";

import { OfferCard } from "@/components/offers/OfferCard";
import { OfferFormModal } from "@/components/offers/OfferFormModal";
import { EmptyState } from "@/components/ui/EmptyState";
import { Button } from "@/components/ui/Button";
import { Tag, Plus } from "lucide-react";
import type { Offer, OfferCompareRow } from "@/lib/types/api";

// Mock хуков — OfferFormModal использует useCreateOffer/useUpdateOffer.
vi.mock("@/lib/api/offers", () => ({
  useCreateOffer: () => ({ mutate: vi.fn(), isPending: false }),
  useUpdateOffer: () => ({ mutate: vi.fn(), isPending: false }),
  useOfferRules: () => ({ data: undefined, isLoading: false, isError: false }),
  useUpsertOfferRules: () => ({ mutate: vi.fn(), isPending: false }),
}));

// Mock Toast — не нужен для unit-тестов.
vi.mock("@/components/ui/Toast", () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
  ToastViewport: () => null,
}));

// ─── Тестовые данные ─────────────────────────────────────────────────────────

const OFFER_ACTIVE: Offer = {
  id: "offer-1",
  code: "CR2",
  name: "Crypto Registration 2",
  vertical: "crypto",
  is_active: true,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: null,
};

const OFFER_INACTIVE: Offer = {
  id: "offer-2",
  code: "DRC_NUTRA",
  name: "Nutra DR Campaign",
  vertical: "nutra",
  is_active: false,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: null,
};

const METRICS: OfferCompareRow = {
  offer_id: "offer-1",
  offer_code: "CR2",
  offer_name: "Crypto Registration 2",
  days: 7,
  spend: "1234.56",
  leads: 42,
  registrations: 30,
  deposits: 12,
  active_ads_count: 5,
  stop_alerts_count: 3,
  cost_per_lead: "29.39",
  cost_per_registration: "41.15",
  cost_per_deposit: "102.88",
};

// ─── OfferCard ────────────────────────────────────────────────────────────────

describe("OfferCard", () => {
  // Тест: карточка активного оффера показывает код, название и метрики.
  it("рендерит код, название и метрики активного оффера", () => {
    const onEdit = vi.fn();
    const onDelete = vi.fn();
    const onRules = vi.fn();

    render(
      <OfferCard
        offer={OFFER_ACTIVE}
        metrics={METRICS}
        onEdit={onEdit}
        onDelete={onDelete}
        onRules={onRules}
      />,
    );

    // Код оффера
    expect(screen.getByText("CR2")).toBeInTheDocument();
    // Название
    expect(screen.getByText("Crypto Registration 2")).toBeInTheDocument();
    // Вертикаль badge
    expect(screen.getByText("crypto")).toBeInTheDocument();
    // Метрики: spend (форматированный)
    expect(screen.getByText("$1,234.56")).toBeInTheDocument();
    // Leads
    expect(screen.getByText("42")).toBeInTheDocument();
    // Deposits
    expect(screen.getByText("12")).toBeInTheDocument();
    // Stop alerts count
    expect(screen.getByText("3")).toBeInTheDocument();
  });

  // Тест: неактивный оффер показывает badge "inactive".
  it("показывает badge inactive для неактивного оффера", () => {
    render(
      <OfferCard
        offer={OFFER_INACTIVE}
        onEdit={vi.fn()}
        onDelete={vi.fn()}
        onRules={vi.fn()}
      />,
    );
    expect(screen.getByText("inactive")).toBeInTheDocument();
    // Код
    expect(screen.getByText("DRC_NUTRA")).toBeInTheDocument();
  });

  // Тест: кнопки edit/delete/rules вызывают правильные хендлеры.
  it("кнопки вызывают onEdit, onDelete, onRules", () => {
    const onEdit = vi.fn();
    const onDelete = vi.fn();
    const onRules = vi.fn();

    render(
      <OfferCard
        offer={OFFER_ACTIVE}
        onEdit={onEdit}
        onDelete={onDelete}
        onRules={onRules}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /редактировать/i }));
    expect(onEdit).toHaveBeenCalledWith(OFFER_ACTIVE);

    fireEvent.click(screen.getByRole("button", { name: /удалить/i }));
    expect(onDelete).toHaveBeenCalledWith(OFFER_ACTIVE);

    fireEvent.click(screen.getByRole("button", { name: /правила/i }));
    expect(onRules).toHaveBeenCalledWith(OFFER_ACTIVE);
  });

  // Тест: при отсутствии metrics отображаются прочерки.
  it("показывает прочерки при отсутствии metrics", () => {
    render(
      <OfferCard
        offer={OFFER_ACTIVE}
        onEdit={vi.fn()}
        onDelete={vi.fn()}
        onRules={vi.fn()}
      />,
    );
    // Все 4 ячейки метрик должны показывать "—"
    const dashes = screen.getAllByText("—");
    expect(dashes.length).toBeGreaterThanOrEqual(4);
  });
});

// ─── EmptyState (интеграция с offers-контекстом) ─────────────────────────────

describe("Offers · EmptyState", () => {
  // Тест: EmptyState рендерится с кнопкой создания при пустом списке.
  it("рендерит EmptyState с CTA создания оффера", () => {
    const onCreate = vi.fn();
    render(
      <EmptyState
        icon={<Tag size={40} strokeWidth={1.25} aria-hidden="true" />}
        title="Офферов нет"
        description="Создайте первый оффер."
        action={
          <Button
            variant="primary"
            leftIcon={<Plus size={14} aria-hidden="true" />}
            onClick={onCreate}
          >
            New offer
          </Button>
        }
      />,
    );

    expect(screen.getByText("Офферов нет")).toBeInTheDocument();
    expect(screen.getByText("Создайте первый оффер.")).toBeInTheDocument();

    const btn = screen.getByRole("button", { name: /new offer/i });
    expect(btn).toBeInTheDocument();
    fireEvent.click(btn);
    expect(onCreate).toHaveBeenCalledOnce();
  });
});

// ─── OfferFormModal — валидация ───────────────────────────────────────────────

describe("OfferFormModal · валидация", () => {
  // Тест: кнопка "Создать" задизейблена при пустом коде.
  it("кнопка создания задизейблена при пустом коде", () => {
    render(
      <OfferFormModal open={true} onOpenChange={vi.fn()} editOffer={null} />,
    );

    const createBtn = screen.getByRole("button", { name: /создать/i });
    expect(createBtn).toBeDisabled();
  });

  // Тест: кнопка включается после ввода валидного кода и имени.
  it("кнопка включается при валидном коде и имени", () => {
    render(
      <OfferFormModal open={true} onOpenChange={vi.fn()} editOffer={null} />,
    );

    const codeInput = screen.getByRole("textbox", { name: /код оффера/i });
    const nameInput = screen.getByRole("textbox", { name: /название/i });

    fireEvent.change(codeInput, { target: { value: "CR3" } });
    fireEvent.change(nameInput, { target: { value: "New Offer" } });

    const createBtn = screen.getByRole("button", { name: /создать/i });
    expect(createBtn).not.toBeDisabled();
  });

  // Тест: невалидный код (со спецсимволами) показывает ошибку валидации.
  it("показывает ошибку при невалидном коде (пробел/кириллица)", () => {
    render(
      <OfferFormModal open={true} onOpenChange={vi.fn()} editOffer={null} />,
    );

    const codeInput = screen.getByRole("textbox", { name: /код оффера/i });
    fireEvent.change(codeInput, { target: { value: "CR 3" } });

    // Ошибка валидации должна появиться
    expect(
      screen.getByText(/только a-z/i),
    ).toBeInTheDocument();
  });

  // Тест: при edit-режиме поле code задизейблено.
  it("поле code задизейблено при редактировании", () => {
    render(
      <OfferFormModal
        open={true}
        onOpenChange={vi.fn()}
        editOffer={OFFER_ACTIVE}
      />,
    );

    const codeInput = screen.getByRole("textbox", { name: /код оффера/i });
    expect(codeInput).toBeDisabled();
  });
});
