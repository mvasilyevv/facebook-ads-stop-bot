/**
 * Offers/$id — standalone редактор порогов: то же двухфазное сохранение,
 * что и в RulesDrawer (drawer-версии той же формы).
 */
import type { ComponentType } from "react";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Offer, OfferRules } from "@fb/shared";

const navigate = vi.hoisted(() => vi.fn());

vi.mock("@tanstack/react-router", () => ({
  createFileRoute: () => (options: { component: ComponentType }) => ({
    ...options,
    useParams: () => ({ id: "uuid-1" }),
  }),
  useNavigate: () => navigate,
}));

const mockUseOffers = vi.fn();
const mockUseOfferRules = vi.fn();
const mockUseUpdateOfferRules = vi.fn();
const mockUseRulesPreview = vi.fn();

vi.mock("@/lib/api/offers", () => ({
  useOffers: () => mockUseOffers(),
  useOfferRules: (id: string) => mockUseOfferRules(id),
  useUpdateOfferRules: (id: string) => mockUseUpdateOfferRules(id),
  useRulesPreview: (...args: unknown[]) => mockUseRulesPreview(...args),
}));

vi.mock("@/components/ui/Toast", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

const OFFER: Offer = {
  id: "uuid-1",
  code: "GH_AVI",
  name: "GH_AVI",
  is_active: true,
  created_at: null,
  updated_at: null,
};

const RULES: OfferRules = {
  offer_id: "uuid-1",
  cpa_threshold: "20.00",
  currency: "USD",
  frequency_threshold: "3.0",
  stop_percent_of_rule: "100.00",
  warning_percent_of_stop: "80.00",
};

import { Route } from "@/routes/offers/$id";

const OfferRulesPage = (Route as unknown as { component: ComponentType }).component;

describe("OfferRulesPage ($id)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockUseOffers.mockReturnValue({ data: [OFFER] });
    mockUseOfferRules.mockReturnValue({
      data: RULES,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });
    mockUseRulesPreview.mockReturnValue({ data: undefined, isLoading: false, isFetching: false });
  });

  it("требует второго клика перед сохранением порогов", async () => {
    const mutateAsync = vi.fn().mockResolvedValue(RULES);
    mockUseUpdateOfferRules.mockReturnValue({ mutateAsync, isPending: false });

    render(<OfferRulesPage />);
    fireEvent.click(screen.getByRole("button", { name: "Сохранить правила" }));
    expect(mutateAsync).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Подтвердить сохранение" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Подтвердить сохранение" }));
    await vi.waitFor(() => expect(mutateAsync).toHaveBeenCalledTimes(1));
    await vi.waitFor(() =>
      expect(navigate).toHaveBeenCalledWith({ to: "/offers" }),
    );
  });

  it("сбрасывает взвод по таймауту", () => {
    vi.useFakeTimers();
    try {
      mockUseUpdateOfferRules.mockReturnValue({
        mutateAsync: vi.fn().mockResolvedValue(RULES),
        isPending: false,
      });
      render(<OfferRulesPage />);
      fireEvent.click(screen.getByRole("button", { name: "Сохранить правила" }));
      expect(screen.getByRole("button", { name: "Подтвердить сохранение" })).toBeInTheDocument();

      act(() => {
        vi.advanceTimersByTime(5_000);
      });
      expect(screen.getByRole("button", { name: "Сохранить правила" })).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });
});
