/**
 * RulesDrawer — сохранение стоп-порогов оффера двухфазное: первый клик только
 * взводит подтверждение, второй сохраняет. Симметрично toggle активности оффера
 * и TMA-редактору порогов.
 */
import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { RulesDrawer } from "@/components/offers/RulesDrawer";
import type { Offer, OfferRules } from "@fb/shared";

const mockUseOfferRules = vi.fn();
const mockUseUpdateOfferRules = vi.fn();
const mockUseRulesPreview = vi.fn();

vi.mock("@/lib/api/offers", () => ({
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

describe("RulesDrawer", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockUseOfferRules.mockReturnValue({
      data: RULES,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });
    mockUseRulesPreview.mockReturnValue({ data: undefined, isLoading: false, isFetching: false });
  });

  it("не сохраняет пороги по первому клику", () => {
    const mutateAsync = vi.fn().mockResolvedValue(RULES);
    mockUseUpdateOfferRules.mockReturnValue({ mutateAsync, isPending: false });

    render(<RulesDrawer offer={OFFER} open onOpenChange={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Сохранить правила" }));

    expect(mutateAsync).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Подтвердить сохранение" })).toBeInTheDocument();
  });

  it("сохраняет пороги по второму клику", async () => {
    const mutateAsync = vi.fn().mockResolvedValue(RULES);
    const onOpenChange = vi.fn();
    mockUseUpdateOfferRules.mockReturnValue({ mutateAsync, isPending: false });

    render(<RulesDrawer offer={OFFER} open onOpenChange={onOpenChange} />);
    fireEvent.click(screen.getByRole("button", { name: "Сохранить правила" }));
    fireEvent.click(screen.getByRole("button", { name: "Подтвердить сохранение" }));

    await vi.waitFor(() => expect(mutateAsync).toHaveBeenCalledTimes(1));
    await vi.waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));
  });

  it("сбрасывает взвод по таймауту", () => {
    vi.useFakeTimers();
    try {
      const mutateAsync = vi.fn().mockResolvedValue(RULES);
      mockUseUpdateOfferRules.mockReturnValue({ mutateAsync, isPending: false });

      render(<RulesDrawer offer={OFFER} open onOpenChange={vi.fn()} />);
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
