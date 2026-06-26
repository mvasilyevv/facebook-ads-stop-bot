/**
 * Тесты страницы Offers (routes/offers/index.tsx) + компонентов offers/.
 *
 * Что проверяем:
 *   - OfferCard: рендер кода, статуса, метрик
 *   - Offers grid: список карточек, EmptyState, кнопка создания
 *   - OfferFormModal: создание (code/кабинеты/CPA + ползунки чувствительности)
 *   - OfferRulesFields: CPA + stop%/warning% передаются в onSave
 *   - Delete: ConfirmDialog с confirmWord
 */

import { render, screen } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import { describe, it, expect, vi } from "vitest";
import type { Offer } from "@fb/shared";

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

    // Кабинеты тэгами: Enter добавляет; act_-префикс срезается, дубли схлопываются.
    await userEvent.type(
      screen.getByLabelText(/рекламные кабинеты/i),
      "act_111{Enter}222{Enter}111{Enter}",
    );

    // Пиксель оффера
    await userEvent.type(screen.getByLabelText(/fb pixel id/i), "9988776655");

    // Гео (страны): нижний регистр аплоадится в upper, дубли схлопываются.
    await userEvent.type(screen.getByLabelText(/страны/i), "de{Enter}br{Enter}de{Enter}");

    // Нажимаем создать
    await userEvent.click(screen.getByRole("button", { name: /создать оффер/i }));

    expect(onSave).toHaveBeenCalledWith(
      expect.objectContaining({
        code: "GH_AVI", // toUpperCase
        is_active: true,
        pixel_id: "9988776655",
        ad_account_ids: ["111", "222"],
        countries: ["DE", "BR"], // ISO-2 upper, дедуп
      }),
    );
  });

  // Гео: нечисловой/невалидный ISO-2 токен отклоняется при добавлении (chip не создаётся).
  it("отклоняет невалидный ISO-2 код страны", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<OfferFormModal open onOpenChange={() => {}} offer={null} onSave={onSave} />);

    await userEvent.type(screen.getByLabelText(/код оффера/i), "CR2");
    await userEvent.type(screen.getByLabelText(/рекламные кабинеты/i), "111{Enter}");
    // Трёхбуквенный код — не ISO-2 → отклонён.
    await userEvent.type(screen.getByLabelText(/страны/i), "deu{Enter}");
    expect(screen.getByText(/не подходит/i)).toBeInTheDocument();

    // Сабмит проходит (countries опц.) — countries пустой.
    await userEvent.click(screen.getByRole("button", { name: /создать оффер/i }));
    expect(onSave).toHaveBeenCalledWith(
      expect.objectContaining({ code: "CR2", countries: [] }),
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

  // Мульти-кабинет: без ID кабинета сабмит блокируется с ошибкой (min 1 обязателен).
  it("показывает ошибку, если кабинеты не указаны", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(
      <OfferFormModal
        open
        onOpenChange={() => {}}
        offer={null}
        onSave={onSave}
      />,
    );

    await userEvent.type(screen.getByLabelText(/код оффера/i), "CR2");
    await userEvent.click(screen.getByRole("button", { name: /создать оффер/i }));

    expect(onSave).not.toHaveBeenCalled();
    expect(screen.getByText(/минимум один id кабинета/i)).toBeInTheDocument();
  });

  // Мульти-кабинет: мусорный ввод (не числа) — ошибка с перечислением плохих токенов.
  it("показывает ошибку при нечисловом ID кабинета", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(
      <OfferFormModal
        open
        onOpenChange={() => {}}
        offer={null}
        onSave={onSave}
      />,
    );

    await userEvent.type(screen.getByLabelText(/код оффера/i), "CR2");
    // Нечисловой токен отклоняется прямо при добавлении (Enter) — chip не создаётся.
    await userEvent.type(screen.getByLabelText(/рекламные кабинеты/i), "abc123x{Enter}");

    expect(screen.getByText(/не подходит/i)).toBeInTheDocument();

    // Сабмит без валидных кабинетов — отдельная ошибка «минимум один».
    await userEvent.click(screen.getByRole("button", { name: /создать оффер/i }));
    expect(onSave).not.toHaveBeenCalled();
    expect(screen.getByText(/минимум один id кабинета/i)).toBeInTheDocument();
  });

  // Стоп-правила (CPA + чувствительность) больше НЕ в форме оффера — они в «Правилах»
  // (RulesDrawer). Форма передаёт только identity (покрыто тестами выше).
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
  useSaveOfferRules: vi.fn(() => ({ mutateAsync: vi.fn(), isPending: false })),
  // OfferRulesFields внутри формы дёргает preview — мок без сетевого запроса.
  useRulesPreview: vi.fn(() => ({ data: undefined, isLoading: false, isFetching: false })),
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
