/**
 * Тесты страницы Offers (routes/offers/index.tsx) + компонентов offers/.
 *
 * Что проверяем:
 *   - OfferCard: рендер кода, статуса и catalog-конфигурации
 *   - Offers grid: список карточек, EmptyState, кнопка создания
 *   - OfferFormModal: создание (code/кабинеты/CPA + ползунки чувствительности)
 *   - OfferRulesFields: CPA + stop%/warning% передаются в onSave
 *   - Deactivation: ConfirmDialog с confirmWord
 */

import { render, screen } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import { describe, it, expect, vi } from "vitest";
import type { Offer } from "@fb/shared";

// ─── OfferCard тесты ──────────────────────────────────────────────────────────

import { OfferCard } from "@/components/offers/OfferCard";

function makeOffer(overrides: Partial<Offer> = {}): Offer {
  return {
    id: "offer-uuid-1",
    code: "CR2",
    name: "CR2",
    vertical: "gambling",
    is_active: true,
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    ...overrides,
  };
}

describe("OfferCard", () => {
  const noop = () => {};

  // Рендер кода оффера
  it("отображает код оффера", () => {
    render(
      <OfferCard offer={makeOffer()} onEditOffer={noop} onEditRules={noop} onDeactivate={noop} />,
    );
    expect(screen.getByText("CR2")).toBeInTheDocument();
  });

  // Статус активен
  it("показывает badge активен для активного оффера", () => {
    render(
      <OfferCard
        offer={makeOffer({ is_active: true })}
        onEditOffer={noop}
        onEditRules={noop}
        onDeactivate={noop}
      />,
    );
    expect(screen.getByText("активен")).toBeInTheDocument();
  });

  // Статус неактивен
  it("показывает badge неактивен для неактивного оффера", () => {
    render(
      <OfferCard
        offer={makeOffer({ is_active: false })}
        onEditOffer={noop}
        onEditRules={noop}
        onDeactivate={noop}
      />,
    );
    expect(screen.getByText("неактивен")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /деактивировать оффер/i })).not.toBeInTheDocument();
  });

  it("отображает подтверждённую конфигурацию вместо legacy KPI", () => {
    render(
      <OfferCard
        offer={makeOffer({
          cpa_threshold: "3.50",
          currency: "USD",
          countries: ["GH", "KE"],
          ad_account_ids: ["123", "456"],
        })}
        onEditOffer={noop}
        onEditRules={noop}
        onDeactivate={noop}
      />,
    );
    expect(screen.getByText(/\$3\.50/)).toBeInTheDocument();
    expect(screen.getByText("GH, KE")).toBeInTheDocument();
    expect(screen.getByText("2")).toBeInTheDocument();
    expect(screen.queryByText("Траты")).not.toBeInTheDocument();
  });

  it("не превращает незаданный CPA в ноль", () => {
    render(
      <OfferCard
        offer={makeOffer({ cpa_threshold: null })}
        onEditOffer={noop}
        onEditRules={noop}
        onDeactivate={noop}
      />,
    );
    expect(screen.getByText("Не задан")).toBeInTheDocument();
    expect(screen.queryByText("$0.00")).not.toBeInTheDocument();
  });

  // Кнопка "Правила" вызывает onEditRules
  it("клик на Правила вызывает onEditRules", async () => {
    const onEditRules = vi.fn();
    render(
      <OfferCard
        offer={makeOffer()}
        onEditOffer={() => {}}
        onEditRules={onEditRules}
        onDeactivate={() => {}}
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
        onDeactivate={() => {}}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: /редактировать оффер/i }));
    expect(onEditOffer).toHaveBeenCalledTimes(1);
  });

  it("клик на Деактивировать вызывает onDeactivate", async () => {
    const onDeactivate = vi.fn();
    render(
      <OfferCard
        offer={makeOffer()}
        onEditOffer={() => {}}
        onEditRules={() => {}}
        onDeactivate={onDeactivate}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: /деактивировать оффер/i }));
    expect(onDeactivate).toHaveBeenCalledTimes(1);
  });

  it("не обрезает длинную подпись действия на узкой карточке", () => {
    // «Деактивировать» вдвое длиннее «Правила»: равные доли flex:1 её не вмещают,
    // и на живом экране текст уезжал за карточку.
    render(
      <OfferCard offer={makeOffer()} onEditOffer={noop} onEditRules={noop} onDeactivate={noop} />,
    );

    const deactivate = screen.getByRole("button", { name: /Деактивировать оффер/ });
    const footer = deactivate.parentElement!;

    expect(footer.style.flexWrap).toBe("wrap");
    expect(deactivate.style.flex).toBe("1 1 auto");
  });
});

// ─── OfferFormModal тесты ─────────────────────────────────────────────────────

import { OfferFormModal } from "@/components/offers/OfferFormModal";

describe("OfferFormModal — создание", () => {
  it("отображает поле code при создании (offer=null)", () => {
    render(<OfferFormModal open onOpenChange={() => {}} offer={null} onSave={async () => {}} />);
    expect(screen.getByLabelText(/код оффера/i)).toBeInTheDocument();
  });

  it("НЕ отображает поле code при редактировании", () => {
    render(
      <OfferFormModal open onOpenChange={() => {}} offer={makeOffer()} onSave={async () => {}} />,
    );
    // В режиме редактирования нет input для code
    expect(screen.queryByLabelText(/код оффера/i)).not.toBeInTheDocument();
    // Но код отображается как текст
    expect(screen.getByText("CR2")).toBeInTheDocument();
  });

  it("onSave вызывается с правильными данными при создании", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<OfferFormModal open onOpenChange={() => {}} offer={null} onSave={onSave} />);

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

    await userEvent.selectOptions(screen.getByLabelText(/вертикаль/i), "gambling");

    // Гео (страны): ввод кода/имени → Enter выбирает совпавшую опцию, хранится ISO-2.
    await userEvent.type(screen.getByLabelText(/страны/i), "de{Enter}br{Enter}de{Enter}");

    // Нажимаем создать
    await userEvent.click(screen.getByRole("button", { name: /создать оффер/i }));

    expect(onSave).toHaveBeenCalledWith(
      expect.objectContaining({
        code: "GH_AVI", // toUpperCase
        vertical: "gambling",
        is_active: true,
        pixel_id: "9988776655",
        ad_account_ids: ["111", "222"],
        countries: ["DE", "BR"], // ISO-2 upper, дедуп
      }),
    );
  });

  // Гео: выбор по русскому названию → хранится ISO-2 («Гана» → GH).
  it("выбирает страну по русскому названию (хранится ISO-2)", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<OfferFormModal open onOpenChange={() => {}} offer={null} onSave={onSave} />);

    await userEvent.type(screen.getByLabelText(/код оффера/i), "CR2");
    await userEvent.type(screen.getByLabelText(/рекламные кабинеты/i), "111{Enter}");
    // Печатаем «гана» → в выпадашке появляется «Гана» → Enter выбирает.
    await userEvent.type(screen.getByLabelText(/страны/i), "гана");
    expect(await screen.findByText("Гана")).toBeInTheDocument();
    await userEvent.keyboard("{Enter}");

    await userEvent.click(screen.getByRole("button", { name: /создать оффер/i }));
    expect(onSave).toHaveBeenCalledWith(
      expect.objectContaining({ code: "CR2", countries: ["GH"] }),
    );
  });

  // Гео: мусорный ввод не даёт опций — ничего не добавляется (countries пустой).
  it("игнорирует ввод без совпадений по странам", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<OfferFormModal open onOpenChange={() => {}} offer={null} onSave={onSave} />);

    await userEvent.type(screen.getByLabelText(/код оффера/i), "CR2");
    await userEvent.type(screen.getByLabelText(/рекламные кабинеты/i), "111{Enter}");
    // Несуществующая страна — нет опций → Enter ничего не добавляет.
    await userEvent.type(screen.getByLabelText(/страны/i), "zzzzz{Enter}");

    await userEvent.click(screen.getByRole("button", { name: /создать оффер/i }));
    expect(onSave).toHaveBeenCalledWith(expect.objectContaining({ code: "CR2", countries: [] }));
  });

  it("показывает ошибку при пустом коде", async () => {
    render(<OfferFormModal open onOpenChange={() => {}} offer={null} onSave={async () => {}} />);

    // Не вводим ничего, сразу жмём создать
    await userEvent.click(screen.getByRole("button", { name: /создать оффер/i }));

    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(screen.getByText(/обязателен/i)).toBeInTheDocument();
  });

  // Мульти-кабинет: без ID кабинета сабмит блокируется с ошибкой (min 1 обязателен).
  it("показывает ошибку, если кабинеты не указаны", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<OfferFormModal open onOpenChange={() => {}} offer={null} onSave={onSave} />);

    await userEvent.type(screen.getByLabelText(/код оффера/i), "CR2");
    await userEvent.click(screen.getByRole("button", { name: /создать оффер/i }));

    expect(onSave).not.toHaveBeenCalled();
    expect(screen.getByText(/минимум один id кабинета/i)).toBeInTheDocument();
  });

  // Мульти-кабинет: мусорный ввод (не числа) — ошибка с перечислением плохих токенов.
  it("показывает ошибку при нечисловом ID кабинета", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<OfferFormModal open onOpenChange={() => {}} offer={null} onSave={onSave} />);

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

// ─── OfferDeactivateManager тест ──────────────────────────────────────────────

import { OfferDeactivateManager } from "@/routes/offers/index";

const mockDeleteMutateAsync = vi.fn().mockResolvedValue(null);

vi.mock("@/lib/api/offers", () => ({
  useOffers: vi.fn(() => ({ data: [], isLoading: false, isError: false })),
  useOffersCompare: vi.fn(() => ({ data: [] })),
  useCreateOffer: vi.fn(() => ({ mutateAsync: vi.fn(), isPending: false })),
  useUpdateOffer: vi.fn(() => ({ mutateAsync: vi.fn(), isPending: false })),
  useDeactivateOffer: vi.fn(() => ({
    mutateAsync: mockDeleteMutateAsync,
    isPending: false,
  })),
  useOfferRules: vi.fn(() => ({ data: null, isLoading: true, isError: false })),
  useUpdateOfferRules: vi.fn(() => ({ mutateAsync: vi.fn(), isPending: false })),
  useSaveOfferRules: vi.fn(() => ({ mutateAsync: vi.fn(), isPending: false })),
  // OfferRulesFields внутри формы дёргает preview — мок без сетевого запроса.
  useRulesPreview: vi.fn(() => ({ data: undefined, isLoading: false, isFetching: false })),
}));

describe("OfferDeactivateManager", () => {
  // ConfirmDialog с confirmWord показывается
  it("требует ввести код оффера для подтверждения деактивации", () => {
    render(
      <OfferDeactivateManager offer={makeOffer({ code: "CR2" })} open onOpenChange={() => {}} />,
    );
    // Поле ввода confirmWord
    expect(screen.getByPlaceholderText("CR2")).toBeInTheDocument();
  });

  it("кнопка Деактивировать активна после ввода правильного кода", async () => {
    render(
      <OfferDeactivateManager offer={makeOffer({ code: "CR2" })} open onOpenChange={() => {}} />,
    );

    // Вводим код
    await userEvent.type(screen.getByPlaceholderText("CR2"), "CR2");

    const deactivateButton = screen.getByRole("button", {
      name: /^деактивировать$/i,
    });
    expect(deactivateButton).not.toBeDisabled();
  });

  it("onConfirm вызывает deleteMutation с id оффера", async () => {
    const onOpenChange = vi.fn();
    render(
      <OfferDeactivateManager
        offer={makeOffer({ id: "offer-uuid-1", code: "CR2" })}
        open
        onOpenChange={onOpenChange}
      />,
    );

    await userEvent.type(screen.getByPlaceholderText("CR2"), "CR2");
    await userEvent.click(screen.getByRole("button", { name: /^деактивировать$/i }));

    expect(mockDeleteMutateAsync).toHaveBeenCalledWith("offer-uuid-1");
  });
});
