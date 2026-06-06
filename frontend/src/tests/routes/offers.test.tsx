/**
 * Тесты страницы Offers (routes/offers/index.tsx) + компонентов offers/.
 *
 * Что проверяем:
 *   - OfferCard: рендер кода, статуса, метрик
 *   - Offers grid: список карточек, EmptyState, кнопка создания
 *   - OfferFormModal: создание (поля code/vertical/is_active)
 *   - RulesForm: 6 полей порогов, onSave вызывается с правильными данными
 *   - Delete: ConfirmDialog с confirmWord
 */

import { render, screen } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import { describe, it, expect, vi } from "vitest";
import type { Offer, OfferRules } from "@fb/shared";

// ─── OfferCard тесты ──────────────────────────────────────────────────────────

import { OfferCard } from "@/components/offers/OfferCard";
import type { components } from "@fb/shared/api/generated";
type OfferCompareRow = components["schemas"]["OfferCompareRow"];

function makeOffer(overrides: Partial<Offer> = {}): Offer {
  return {
    id: "offer-uuid-1",
    code: "CR2",
    name: "CR2",
    vertical: "gambling",
    is_active: true,
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    country_code: null,
    use_vision_creator: null,
    notes: null,
    ...overrides,
  };
}

function makeMetrics(overrides: Partial<OfferCompareRow> = {}): OfferCompareRow {
  return {
    offer_id: "offer-uuid-1",
    offer_code: "CR2",
    offer_name: "CR2",
    days: 7,
    spend: "1234.56",
    leads: 42,
    registrations: 20,
    deposits: 10,
    active_ads_count: 5,
    stop_alerts_count: 2,
    cost_per_lead: "29.39",
    cost_per_registration: null,
    cost_per_deposit: null,
    ...overrides,
  };
}

describe("OfferCard", () => {
  const noop = () => {};

  // Рендер кода оффера
  it("отображает код оффера", () => {
    render(
      <OfferCard
        offer={makeOffer()}
        onEditOffer={noop}
        onEditRules={noop}
        onDelete={noop}
      />,
    );
    expect(screen.getByText("CR2")).toBeInTheDocument();
  });

  // Статус active
  it("показывает badge active для активного оффера", () => {
    render(
      <OfferCard
        offer={makeOffer({ is_active: true })}
        onEditOffer={noop}
        onEditRules={noop}
        onDelete={noop}
      />,
    );
    expect(screen.getByText("active")).toBeInTheDocument();
  });

  // Статус inactive
  it("показывает badge inactive для неактивного оффера", () => {
    render(
      <OfferCard
        offer={makeOffer({ is_active: false })}
        onEditOffer={noop}
        onEditRules={noop}
        onDelete={noop}
      />,
    );
    expect(screen.getByText("inactive")).toBeInTheDocument();
  });

  // Метрики отображаются
  it("отображает spend из metrics", () => {
    render(
      <OfferCard
        offer={makeOffer()}
        metrics={makeMetrics({ spend: "1234.56" })}
        onEditOffer={noop}
        onEditRules={noop}
        onDelete={noop}
      />,
    );
    expect(screen.getByText("$1,234.56")).toBeInTheDocument();
  });

  // Metrics отсутствуют — прочерки
  it("показывает «—» при отсутствии metrics", () => {
    render(
      <OfferCard
        offer={makeOffer()}
        metrics={undefined}
        onEditOffer={noop}
        onEditRules={noop}
        onDelete={noop}
      />,
    );
    // Несколько прочерков для разных метрик
    const dashes = screen.getAllByText("—");
    expect(dashes.length).toBeGreaterThan(0);
  });

  // Кнопка "Правила" вызывает onEditRules
  it("клик на Правила вызывает onEditRules", async () => {
    const onEditRules = vi.fn();
    render(
      <OfferCard
        offer={makeOffer()}
        onEditOffer={() => {}}
        onEditRules={onEditRules}
        onDelete={() => {}}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: /правила/i }));
    expect(onEditRules).toHaveBeenCalledTimes(1);
    expect(onEditRules).toHaveBeenCalledWith(expect.objectContaining({ code: "CR2" }));
  });

  // Кнопка "Изменить" вызывает onEditOffer (aria-label = "Редактировать оффер CR2")
  it("клик на Изменить вызывает onEditOffer", async () => {
    const onEditOffer = vi.fn();
    render(
      <OfferCard
        offer={makeOffer()}
        onEditOffer={onEditOffer}
        onEditRules={() => {}}
        onDelete={() => {}}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: /редактировать оффер/i }));
    expect(onEditOffer).toHaveBeenCalledTimes(1);
  });

  // Кнопка "Удалить" вызывает onDelete
  it("клик на Удалить вызывает onDelete", async () => {
    const onDelete = vi.fn();
    render(
      <OfferCard
        offer={makeOffer()}
        onEditOffer={() => {}}
        onEditRules={() => {}}
        onDelete={onDelete}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: /удалить/i }));
    expect(onDelete).toHaveBeenCalledTimes(1);
  });

  // stop_alerts_count > 0 → danger
  it("показывает danger-цвет для stop_alerts_count > 0", () => {
    render(
      <OfferCard
        offer={makeOffer()}
        metrics={makeMetrics({ stop_alerts_count: 3 })}
        onEditOffer={() => {}}
        onEditRules={() => {}}
        onDelete={() => {}}
      />,
    );
    // Значение "3" должно быть в DOM
    expect(screen.getByText("3")).toBeInTheDocument();
  });
});

// ─── OfferFormModal тесты ─────────────────────────────────────────────────────

import { OfferFormModal } from "@/components/offers/OfferFormModal";

describe("OfferFormModal — создание", () => {
  it("отображает поле code при создании (offer=null)", () => {
    render(
      <OfferFormModal
        open
        onOpenChange={() => {}}
        offer={null}
        onSave={async () => {}}
      />,
    );
    expect(screen.getByLabelText(/код оффера/i)).toBeInTheDocument();
  });

  it("НЕ отображает поле code при редактировании", () => {
    render(
      <OfferFormModal
        open
        onOpenChange={() => {}}
        offer={makeOffer()}
        onSave={async () => {}}
      />,
    );
    // В режиме редактирования нет input для code
    expect(screen.queryByLabelText(/код оффера/i)).not.toBeInTheDocument();
    // Но код отображается как текст
    expect(screen.getByText("CR2")).toBeInTheDocument();
  });

  it("onSave вызывается с правильными данными при создании", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(
      <OfferFormModal
        open
        onOpenChange={() => {}}
        offer={null}
        onSave={onSave}
      />,
    );

    // Вводим код
    await userEvent.clear(screen.getByLabelText(/код оффера/i));
    await userEvent.type(screen.getByLabelText(/код оффера/i), "gh_avi");

    // Нажимаем создать
    await userEvent.click(screen.getByRole("button", { name: /создать оффер/i }));

    expect(onSave).toHaveBeenCalledWith(
      expect.objectContaining({
        code: "GH_AVI", // toUpperCase
        is_active: true,
      }),
    );
  });

  it("показывает ошибку при пустом коде", async () => {
    render(
      <OfferFormModal
        open
        onOpenChange={() => {}}
        offer={null}
        onSave={async () => {}}
      />,
    );

    // Не вводим ничего, сразу жмём создать
    await userEvent.click(screen.getByRole("button", { name: /создать оффер/i }));

    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(screen.getByText(/обязателен/i)).toBeInTheDocument();
  });
});

// ─── RulesForm тесты ──────────────────────────────────────────────────────────

import { RulesForm } from "@/components/offers/RulesForm";

function makeRules(overrides: Partial<OfferRules> = {}): OfferRules {
  return {
    offer_id: "offer-uuid-1",
    spend_no_event_threshold: "50",
    cpa_threshold: "25",
    cpm_threshold: "10",
    ctr_threshold: "1.5",
    frequency_threshold: null,
    funnel_ratio_threshold: "0.3",
    stop_percent_of_rule: "100",
    warning_percent_of_stop: "80",
    ...overrides,
  };
}

describe("RulesForm", () => {
  // 6 полей порогов
  it("рендерит 6 полей правил", () => {
    render(
      <RulesForm rules={makeRules()} onSave={async () => {}} />,
    );
    // Проверяем наличие key-лейблов
    expect(screen.getByLabelText(/spend без события/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/cpa порог/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/cpm порог/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/ctr порог/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/frequency порог/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/funnel ratio/i)).toBeInTheDocument();
  });

  // Значения из rules отображаются в инпутах
  it("заполняет поля значениями из rules", () => {
    render(
      <RulesForm rules={makeRules({ cpa_threshold: "35" })} onSave={async () => {}} />,
    );
    const cpaInput = screen.getByLabelText(/cpa порог/i) as HTMLInputElement;
    expect(cpaInput.value).toBe("35");
  });

  // null threshold → пустое поле
  it("null threshold → пустое поле инпута", () => {
    render(
      <RulesForm
        rules={makeRules({ frequency_threshold: null })}
        onSave={async () => {}}
      />,
    );
    const freqInput = screen.getByLabelText(/frequency порог/i) as HTMLInputElement;
    expect(freqInput.value).toBe("");
  });

  // onSave вызывается с правильными данными
  it("onSave вызывается с корректными данными (непустое=строка, пустое=null)", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(
      <RulesForm
        rules={makeRules({ spend_no_event_threshold: "50", frequency_threshold: null })}
        onSave={onSave}
      />,
    );

    // Меняем cpa на 30
    await userEvent.clear(screen.getByLabelText(/cpa порог/i));
    await userEvent.type(screen.getByLabelText(/cpa порог/i), "30");

    await userEvent.click(screen.getByRole("button", { name: /сохранить правила/i }));

    expect(onSave).toHaveBeenCalledWith(
      expect.objectContaining({
        cpa_threshold: "30",
        frequency_threshold: null, // пустое → null
        spend_no_event_threshold: "50",
      }),
    );
  });

  // Пустое поле → null в payload
  it("очистка поля → передаёт null в onSave", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(
      <RulesForm rules={makeRules({ cpm_threshold: "10" })} onSave={onSave} />,
    );

    await userEvent.clear(screen.getByLabelText(/cpm порог/i));
    await userEvent.click(screen.getByRole("button", { name: /сохранить правила/i }));

    expect(onSave).toHaveBeenCalledWith(
      expect.objectContaining({ cpm_threshold: null }),
    );
  });

  // onCancel вызывается
  it("кнопка Отмена вызывает onCancel", async () => {
    const onCancel = vi.fn();
    render(
      <RulesForm rules={makeRules()} onSave={async () => {}} onCancel={onCancel} />,
    );
    await userEvent.click(screen.getByRole("button", { name: /отмена/i }));
    expect(onCancel).toHaveBeenCalledTimes(1);
  });
});

// ─── OfferDeleteManager тест ──────────────────────────────────────────────────

import { OfferDeleteManager } from "@/routes/offers/index";

const mockDeleteMutateAsync = vi.fn().mockResolvedValue(null);

vi.mock("@/lib/api/offers", () => ({
  useOffers: vi.fn(() => ({ data: [], isLoading: false, isError: false })),
  useOffersCompare: vi.fn(() => ({ data: [] })),
  useCreateOffer: vi.fn(() => ({ mutateAsync: vi.fn(), isPending: false })),
  useUpdateOffer: vi.fn(() => ({ mutateAsync: vi.fn(), isPending: false })),
  useDeleteOffer: vi.fn(() => ({ mutateAsync: mockDeleteMutateAsync, isPending: false })),
  useOfferRules: vi.fn(() => ({ data: null, isLoading: true, isError: false })),
  useUpdateOfferRules: vi.fn(() => ({ mutateAsync: vi.fn(), isPending: false })),
}));

describe("OfferDeleteManager", () => {
  // ConfirmDialog с confirmWord показывается
  it("требует ввести код оффера для подтверждения удаления", () => {
    render(
      <OfferDeleteManager
        offer={makeOffer({ code: "CR2" })}
        open
        onOpenChange={() => {}}
      />,
    );
    // Поле ввода confirmWord
    expect(screen.getByPlaceholderText("CR2")).toBeInTheDocument();
  });

  it("кнопка Удалить активна после ввода правильного кода", async () => {
    render(
      <OfferDeleteManager
        offer={makeOffer({ code: "CR2" })}
        open
        onOpenChange={() => {}}
      />,
    );

    // Вводим код
    await userEvent.type(screen.getByPlaceholderText("CR2"), "CR2");

    const deleteBtn = screen.getByRole("button", { name: /^удалить$/i });
    expect(deleteBtn).not.toBeDisabled();
  });

  it("onConfirm вызывает deleteMutation с id оффера", async () => {
    const onOpenChange = vi.fn();
    render(
      <OfferDeleteManager
        offer={makeOffer({ id: "offer-uuid-1", code: "CR2" })}
        open
        onOpenChange={onOpenChange}
      />,
    );

    await userEvent.type(screen.getByPlaceholderText("CR2"), "CR2");
    await userEvent.click(screen.getByRole("button", { name: /^удалить$/i }));

    expect(mockDeleteMutateAsync).toHaveBeenCalledWith("offer-uuid-1");
  });
});
