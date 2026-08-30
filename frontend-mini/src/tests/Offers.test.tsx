/**
 * Тест OffersPage: CRUD офферов и редактор 6 порогов.
 * Адаптирован под новый дизайн-канон — Badge теперь "active"/"inactive".
 */
import type { ComponentType } from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import type { Offer, OfferRules } from "@fb/shared";

const routeState = vi.hoisted(() => ({ filter: "all", role: "owner" }));
const navigate = vi.hoisted(() => vi.fn());

// Мок роутера
vi.mock("@tanstack/react-router", () => ({
  createFileRoute: () => (options: { component: ComponentType }) => ({
    ...options,
    useSearch: () => ({ filter: routeState.filter }),
  }),
  useNavigate: () => navigate,
}));

vi.mock("@/lib/auth", () => ({
  getStoredRole: () => routeState.role,
}));

// Мок TG
vi.mock("@/lib/tg", () => ({
  haptic: { impact: vi.fn(), notify: vi.fn(), selection: vi.fn() },
  tgAlert: vi.fn().mockResolvedValue(undefined),
}));

// Мок MiniHeader
vi.mock("@/components/layout/MiniHeader", () => ({
  MiniHeader: ({
    title,
    right,
  }: {
    title: string;
    right?: React.ReactNode;
  }) => (
    <header>
      <span>{title}</span>
      {right}
    </header>
  ),
}));

// Мок данных
const MOCK_OFFERS: Offer[] = [
  {
    id: "uuid-1",
    code: "GH_AVI",
    name: "GH_AVI",
    is_active: true,
    created_at: null,
    updated_at: null,
  },
  {
    id: "uuid-2",
    code: "NG_CR2",
    name: "NG_CR2",
    is_active: false,
    created_at: null,
    updated_at: null,
  },
];

// Мёртвые пороги (spend_no_event/cpm/ctr/funnel_ratio) убраны из API-контракта
// (H-2 аудита 02.07) — мок содержит только реально работающие поля.
const MOCK_RULES: OfferRules = {
  offer_id: "uuid-1",
  cpa_threshold: "20.00",
  currency: "USD",
  frequency_threshold: "3.0",
  stop_percent_of_rule: "100.00",
  warning_percent_of_stop: "80.00",
};

const mockUseOffers = vi.fn();
const mockUseCreateOffer = vi.fn();
const mockUseUpdateOffer = vi.fn();
const mockUseOfferRules = vi.fn();
const mockUseUpdateOfferRules = vi.fn();
const mockUseRulesPreview = vi.fn();

vi.mock("@/lib/api", () => ({
  useOffers: () => mockUseOffers(),
  useCreateOffer: () => mockUseCreateOffer(),
  useUpdateOffer: () => mockUseUpdateOffer(),
  useOfferRules: (id: string) => mockUseOfferRules(id),
  useUpdateOfferRules: () => mockUseUpdateOfferRules(),
  useRulesPreview: (...args: unknown[]) => mockUseRulesPreview(...args),
}));

import { Route } from "@/routes/offers/index";

const OffersTestWrapper = (Route as unknown as { component: ComponentType })
  .component;

describe("OffersPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    routeState.filter = "all";
    routeState.role = "owner";
    mockUseOffers.mockReturnValue({
      data: MOCK_OFFERS,
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    });
    mockUseCreateOffer.mockReturnValue({
      mutateAsync: vi.fn().mockResolvedValue({}),
      isPending: false,
    });
    mockUseUpdateOffer.mockReturnValue({
      mutateAsync: vi.fn().mockResolvedValue({}),
      isPending: false,
    });
    mockUseOfferRules.mockReturnValue({
      data: MOCK_RULES,
      isLoading: false,
    });
    mockUseUpdateOfferRules.mockReturnValue({
      mutateAsync: vi.fn().mockResolvedValue(MOCK_RULES),
      isPending: false,
    });
    mockUseRulesPreview.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: false,
    });
  });

  // Список офферов отображается
  it("показывает список офферов", () => {
    render(<OffersTestWrapper />);
    expect(screen.getByText("GH_AVI")).toBeInTheDocument();
    expect(screen.getByText("NG_CR2")).toBeInTheDocument();
    expect(screen.getByText(/Активных: 1 · выключено: 1/i)).toBeInTheDocument();
  });

  it("показывает статусы офферов понятными русскими метками", () => {
    render(<OffersTestWrapper />);
    expect(screen.getAllByText("Активен").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("Выключен").length).toBeGreaterThanOrEqual(1);
  });

  it("keeps the web active-state filter in typed URL state", () => {
    routeState.filter = "inactive";
    render(<OffersTestWrapper />);
    expect(screen.queryByText("GH_AVI")).not.toBeInTheDocument();
    expect(screen.getByText("NG_CR2")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Выключенные" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("is read-only for a notification recipient", () => {
    routeState.role = "recipient";
    render(<OffersTestWrapper />);
    expect(
      screen.getByText(/каталог доступен только для чтения/i),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Новый/i })).toBeDisabled();
  });

  // Клик по офферу открывает bottom sheet с деталями
  it("клик по офферу открывает sheet с деталями", () => {
    render(<OffersTestWrapper />);
    // Кликаем по кнопке карточки с aria-label "Оффер GH_AVI"
    const card = screen.getByRole("button", { name: /Оффер GH_AVI/i });
    fireEvent.click(card);
    // В sheet появляются кнопки действий
    expect(screen.getByText("Редактировать")).toBeInTheDocument();
    expect(screen.getByText("Пороги")).toBeInTheDocument();
    expect(screen.getByText("Выключить")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Закрыть" })).toHaveClass(
      "size-11",
    );
  });

  // Кнопка "Пороги" показывает только рабочие пороги (CPA + Frequency);
  // неактивные (spend-без-события/CPM/CTR/funnel-ratio) убраны из формы.
  it("клик по 'Пороги' показывает только рабочие пороги (CPA, Frequency)", () => {
    render(<OffersTestWrapper />);
    const card = screen.getByRole("button", { name: /Оффер GH_AVI/i });
    fireEvent.click(card);
    fireEvent.click(screen.getByText("Пороги"));
    expect(screen.getByLabelText(/CPA ставка/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/Frequency порог/i)).toBeInTheDocument();
    // Неактивные пороги в форме отсутствовать (не вводить в заблуждение).
    expect(
      screen.queryByLabelText(/Spend без события/i),
    ).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/CPM порог/i)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/CTR порог/i)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/Funnel ratio/i)).not.toBeInTheDocument();
    const sheet = screen.getByRole("dialog");
    expect(sheet).toHaveClass(
      "max-h-[calc(var(--tg-viewport-stable-height,100dvh)-max(var(--tg-content-safe-top,0px),env(safe-area-inset-top)))]",
    );
    expect(sheet.querySelector("[data-sheet-scroll]")).toHaveClass(
      "overflow-y-auto",
    );
    expect(
      screen.getByRole("button", { name: "Сохранить пороги" }).parentElement,
    ).toHaveClass("sticky", "bottom-0");
  });

  // Кнопка "+ Новый" открывает форму создания
  it("кнопка 'Новый' открывает форму создания", () => {
    render(<OffersTestWrapper />);
    // Кнопка содержит текст "Новый" (с иконкой Plus)
    fireEvent.click(screen.getByRole("button", { name: /Новый/i }));
    expect(screen.getByText("Создать оффер")).toBeInTheDocument();
  });

  // Создание оффера вызывает mutateAsync с правильными данными (мульти-кабинет: + кабинеты)
  it("создание оффера вызывает useCreateOffer.mutateAsync", async () => {
    const mutateAsync = vi.fn().mockResolvedValue({});
    mockUseCreateOffer.mockReturnValue({ mutateAsync, isPending: false });

    render(<OffersTestWrapper />);
    fireEvent.click(screen.getByRole("button", { name: /Новый/i }));

    const codeInput = screen.getByLabelText(/Код оффера/i);
    fireEvent.change(codeInput, { target: { value: "DRC_NEW" } });

    // Мульти-кабинет: без ID кабинета сабмит блокируется валидацией
    const accountsInput = screen.getByLabelText(/Рекламные кабинеты/i);
    fireEvent.change(accountsInput, { target: { value: "act_111, 222" } });
    fireEvent.change(screen.getByLabelText(/FB Pixel ID/i), {
      target: { value: "9988776655" },
    });

    fireEvent.click(screen.getByRole("switch", { name: "Активен" }));

    fireEvent.click(screen.getByText("Создать оффер"));

    await waitFor(() => {
      expect(mutateAsync).toHaveBeenCalledWith(
        expect.objectContaining({
          code: "DRC_NEW",
            pixel_id: "9988776655",
          is_active: false,
          ad_account_ids: ["111", "222"],
        }),
      );
    });
  });

  // Мульти-кабинет: пустое поле кабинетов блокирует сабмит
  it("создание без кабинетов блокируется валидацией", async () => {
    const mutateAsync = vi.fn().mockResolvedValue({});
    mockUseCreateOffer.mockReturnValue({ mutateAsync, isPending: false });

    render(<OffersTestWrapper />);
    fireEvent.click(screen.getByRole("button", { name: /Новый/i }));
    fireEvent.change(screen.getByLabelText(/Код оффера/i), {
      target: { value: "DRC_NEW" },
    });
    fireEvent.click(screen.getByText("Создать оффер"));

    await waitFor(() => {
      expect(screen.getByText(/минимум один ID кабинета/i)).toBeInTheDocument();
    });
    expect(screen.getByLabelText(/Рекламные кабинеты/i)).toHaveFocus();
    expect(mutateAsync).not.toHaveBeenCalled();
  });

  it("выключение оффера использует единственный update-путь", async () => {
    const mutateAsync = vi.fn().mockResolvedValue({});
    mockUseUpdateOffer.mockReturnValue({ mutateAsync, isPending: false });
    render(<OffersTestWrapper />);
    const card = screen.getByRole("button", { name: /Оффер GH_AVI/i });
    fireEvent.click(card);
    fireEvent.click(screen.getByText("Выключить"));
    expect(screen.getByText("Подтвердить выключение")).toBeInTheDocument();
    fireEvent.click(screen.getByText("Подтвердить выключение"));

    await waitFor(() => {
      expect(mutateAsync).toHaveBeenCalledWith({
        id: "uuid-1",
        payload: { is_active: false },
      });
    });
  });

  // При загрузке показываются скелетоны
  it("при загрузке показывает skeleton", () => {
    mockUseOffers.mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
      refetch: vi.fn(),
    });
    render(<OffersTestWrapper />);
    expect(screen.queryByText("GH_AVI")).not.toBeInTheDocument();
  });

  // При пустом списке показывается EmptyState
  it("при пустом списке показывает EmptyState", () => {
    mockUseOffers.mockReturnValue({
      data: [],
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    });
    render(<OffersTestWrapper />);
    expect(screen.getByText(/Офферов нет/i)).toBeInTheDocument();
  });

  it("объясняет пустой фильтр и позволяет вернуться ко всем офферам", () => {
    routeState.filter = "active";
    mockUseOffers.mockReturnValue({
      data: [MOCK_OFFERS[1]],
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    });

    render(<OffersTestWrapper />);

    expect(screen.getByText("Активных офферов нет")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Показать все" }));
    expect(navigate).toHaveBeenCalledWith({
      to: "/offers",
      search: { filter: "all" },
    });
  });

  // Кнопка "Редактировать" в detail-sheet переходит к форме редактирования
  it("кнопка 'Редактировать' в sheet переходит к форме редактирования", () => {
    render(<OffersTestWrapper />);
    const card = screen.getByRole("button", { name: /Оффер GH_AVI/i });
    fireEvent.click(card);
    fireEvent.click(screen.getByText("Редактировать"));
    // Поле код должно быть disabled при редактировании
    const codeInput = screen.getByLabelText(/Код оффера/i);
    expect(codeInput).toBeDisabled();
    // Кнопка «Сохранить» появляется
    expect(screen.getByText("Сохранить")).toBeInTheDocument();
  });

  // Сохранение порогов — двухфазная кнопка (как toggle активности оффера):
  // первый тап только взводит подтверждение, второй сохраняет.
  it("сохранение порогов требует второго тапа", async () => {
    const mutateAsync = vi.fn().mockResolvedValue(MOCK_RULES);
    mockUseUpdateOfferRules.mockReturnValue({ mutateAsync, isPending: false });

    render(<OffersTestWrapper />);
    fireEvent.click(screen.getByRole("button", { name: /Оффер GH_AVI/i }));
    fireEvent.click(screen.getByText("Пороги"));

    fireEvent.click(screen.getByRole("button", { name: "Сохранить пороги" }));
    expect(mutateAsync).not.toHaveBeenCalled();
    expect(
      screen.getByRole("button", { name: "Подтвердить сохранение" }),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Подтвердить сохранение" }));
    await waitFor(() => expect(mutateAsync).toHaveBeenCalledTimes(1));
  });

  it("взвод сохранения порогов сбрасывается по таймауту", () => {
    vi.useFakeTimers();
    try {
      render(<OffersTestWrapper />);
      fireEvent.click(screen.getByRole("button", { name: /Оффер GH_AVI/i }));
      fireEvent.click(screen.getByText("Пороги"));

      fireEvent.click(screen.getByRole("button", { name: "Сохранить пороги" }));
      expect(
        screen.getByRole("button", { name: "Подтвердить сохранение" }),
      ).toBeInTheDocument();

      act(() => {
        vi.advanceTimersByTime(5_000);
      });
      expect(
        screen.getByRole("button", { name: "Сохранить пороги" }),
      ).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  // Восемь настраиваемых порогов стоп-правил
  it("показывает восемь настраиваемых порогов в форме Пороги", () => {
    render(<OffersTestWrapper />);
    const card = screen.getByRole("button", { name: /Оффер GH_AVI/i });
    fireEvent.click(card);
    fireEvent.click(screen.getByText("Пороги"));

    // cpc_percent_of_cpa — «Клик (CPC): % от CPA»
    expect(screen.getByLabelText(/Клик.*% от CPA/i)).toBeInTheDocument();
    // cpl_percent_of_cpa — «Лид (CPL): % от CPA»
    expect(screen.getByLabelText(/Лид.*% от CPA/i)).toBeInTheDocument();
    // cpr_percent_of_cpa — «Регистрация (CPR): % от CPA»
    expect(screen.getByLabelText(/Регистрация.*% от CPA/i)).toBeInTheDocument();
    // regs_no_dep_stop_count — «Регистраций без депозита (стоп, штук)»
    expect(screen.getByLabelText(/Регистраций без депозита/i)).toBeInTheDocument();
    // spend_no_dep_from/to — один заголовок на пару
    expect(screen.getByText(/Спенд без депозита/i)).toBeInTheDocument();
    // spend_with_dep_from/to — один заголовок на пару
    expect(screen.getByText(/Спенд с депозитом/i)).toBeInTheDocument();
    // min_ratio_denominator — «Минимальный знаменатель отношения»
    expect(screen.getByLabelText(/Минимальный знаменатель отношения/i)).toBeInTheDocument();
  });
});
