/**
 * Тест OffersPage: CRUD офферов и редактор 6 порогов.
 * Адаптирован под новый дизайн-канон — Badge теперь "active"/"inactive".
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import type { Offer, OfferRules } from "@fb/shared";

// Мок роутера
vi.mock("@tanstack/react-router", () => ({
  createFileRoute: () => ({ component: (c: unknown) => c }),
  useNavigate: () => vi.fn(),
}));

// Мок TG
vi.mock("@/lib/tg", () => ({
  haptic: { impact: vi.fn(), notify: vi.fn(), selection: vi.fn() },
  tgConfirm: vi.fn().mockResolvedValue(true),
  tgAlert: vi.fn().mockResolvedValue(undefined),
}));

// Мок MiniHeader
vi.mock("@/components/layout/MiniHeader", () => ({
  MiniHeader: ({ title, right }: { title: string; right?: React.ReactNode }) => (
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
    vertical: "gambling",
    is_active: true,
    created_at: null,
    updated_at: null,
    country_code: null,
    use_vision_creator: null,
    notes: null,
  },
  {
    id: "uuid-2",
    code: "NG_CR2",
    name: "NG_CR2",
    vertical: "gambling",
    is_active: false,
    created_at: null,
    updated_at: null,
    country_code: null,
    use_vision_creator: null,
    notes: null,
  },
];

const MOCK_RULES: OfferRules = {
  offer_id: "uuid-1",
  spend_no_event_threshold: "50.00",
  cpa_threshold: "20.00",
  cpm_threshold: null,
  ctr_threshold: null,
  frequency_threshold: "3.0",
  funnel_ratio_threshold: null,
  stop_percent_of_rule: "100.00",
  warning_percent_of_stop: "80.00",
};

const mockUseOffers = vi.fn();
const mockUseCreateOffer = vi.fn();
const mockUseUpdateOffer = vi.fn();
const mockUseDeleteOffer = vi.fn();
const mockUseOfferRules = vi.fn();
const mockUseUpdateOfferRules = vi.fn();

vi.mock("@/lib/api", () => ({
  useOffers: () => mockUseOffers(),
  useCreateOffer: () => mockUseCreateOffer(),
  useUpdateOffer: () => mockUseUpdateOffer(),
  useDeleteOffer: () => mockUseDeleteOffer(),
  useOfferRules: (id: string) => mockUseOfferRules(id),
  useUpdateOfferRules: () => mockUseUpdateOfferRules(),
}));

import OffersTestWrapper from "./Offers.test.helper";

describe("OffersPage", () => {
  beforeEach(() => {
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
    mockUseDeleteOffer.mockReturnValue({
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
  });

  // Список офферов отображается
  it("показывает список офферов", () => {
    render(<OffersTestWrapper />);
    expect(screen.getByText("GH_AVI")).toBeInTheDocument();
    expect(screen.getByText("NG_CR2")).toBeInTheDocument();
  });

  // Активный оффер имеет badge "active", неактивный — "inactive"
  it("активный оффер имеет badge 'active', неактивный — 'inactive'", () => {
    render(<OffersTestWrapper />);
    // Одна карточка с "active" (GH_AVI активен)
    expect(screen.getAllByText(/^active$/i).length).toBeGreaterThanOrEqual(1);
    // Одна карточка с "inactive" (NG_CR2 неактивен)
    expect(screen.getAllByText(/^inactive$/i).length).toBeGreaterThanOrEqual(1);
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
    expect(screen.getByText("Удалить")).toBeInTheDocument();
  });

  // Кнопка "Пороги" показывает 6 полей порогов
  it("клик по 'Пороги' показывает редактор 6 порогов", () => {
    render(<OffersTestWrapper />);
    const card = screen.getByRole("button", { name: /Оффер GH_AVI/i });
    fireEvent.click(card);
    fireEvent.click(screen.getByText("Пороги"));
    expect(screen.getByLabelText(/Spend без события/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/CPA порог/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/CPM порог/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/CTR порог/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/Frequency порог/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/Funnel ratio/i)).toBeInTheDocument();
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

    fireEvent.click(screen.getByText("Создать оффер"));

    await waitFor(() => {
      expect(mutateAsync).toHaveBeenCalledWith(
        expect.objectContaining({ code: "DRC_NEW", ad_account_ids: ["111", "222"] }),
      );
    });
  });

  // Мульти-кабинет: пустое поле кабинетов блокирует сабмит
  it("создание без кабинетов блокируется валидацией", async () => {
    const mutateAsync = vi.fn().mockResolvedValue({});
    mockUseCreateOffer.mockReturnValue({ mutateAsync, isPending: false });

    render(<OffersTestWrapper />);
    fireEvent.click(screen.getByRole("button", { name: /Новый/i }));
    fireEvent.change(screen.getByLabelText(/Код оффера/i), { target: { value: "DRC_NEW" } });
    fireEvent.click(screen.getByText("Создать оффер"));

    await waitFor(() => {
      expect(screen.getByText(/минимум один ID кабинета/i)).toBeInTheDocument();
    });
    expect(mutateAsync).not.toHaveBeenCalled();
  });

  // Удаление требует tgConfirm и вызывает deleteOffer
  it("удаление оффера вызывает tgConfirm, затем deleteOffer", async () => {
    const { tgConfirm } = await import("@/lib/tg");
    const mutateAsync = vi.fn().mockResolvedValue({});
    mockUseDeleteOffer.mockReturnValue({ mutateAsync, isPending: false });

    render(<OffersTestWrapper />);
    const card = screen.getByRole("button", { name: /Оффер GH_AVI/i });
    fireEvent.click(card);
    fireEvent.click(screen.getByText("Удалить"));

    await waitFor(() => {
      expect(tgConfirm).toHaveBeenCalled();
      expect(mutateAsync).toHaveBeenCalledWith({ id: "uuid-1" });
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
});
