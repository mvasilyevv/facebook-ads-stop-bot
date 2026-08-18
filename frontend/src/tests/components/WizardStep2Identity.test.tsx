import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api/campaigns", () => ({
  useAdAccountContext: () => ({ mutate: vi.fn(), isPending: false }),
  useAdAccountPages: () => ({ mutate: vi.fn(), isPending: false }),
  useAdAccountPixels: () => ({ mutate: vi.fn(), isPending: false }),
}));

vi.mock("@/lib/api/offers", () => ({
  useOffers: () => ({ data: [], isLoading: false }),
}));

import { WizardStep2Identity } from "@/components/domain/campaigns/WizardStep2Identity";
import type { WizardIdentity } from "@/stores/campaignWizard";

const BASE_IDENTITY: WizardIdentity = {
  act_id: "",
  ad_account_ids: [],
  page_id: "",
  pixel_id: "",
  account_context_state: "unavailable",
  timezone_name: "",
  currency: "",
  currency_exponent: null,
  account_context_observed_at: null,
  account_context_issue: null,
  offer_code: "",
  byer_tag: "",
};

function renderStep(overrides: Partial<WizardIdentity>) {
  return render(
    <WizardStep2Identity
      values={{ ...BASE_IDENTITY, ...overrides }}
      onChange={() => {}}
      onGoalChange={() => {}}
    />,
  );
}

describe("WizardStep2Identity — контекст кабинета", () => {
  // Оператору нужна причина, а не факт блокировки: «Контекст недоступен» без
  // объяснения отправляет его гадать, что именно не так с кабинетом.
  it("показывает причину недоступного контекста", () => {
    renderStep({
      act_id: "2108857220005012",
      account_context_state: "unavailable",
      account_context_issue: "Meta не отдала часовой пояс и валюту по кабинету",
    });

    expect(
      screen.getByText("Meta не отдала часовой пояс и валюту по кабинету"),
    ).toBeInTheDocument();
  });

  // Причины нет — остаётся честная общая формулировка, а не пустая строка.
  it("без причины оставляет общую формулировку", () => {
    renderStep({
      act_id: "2108857220005012",
      account_context_state: "unavailable",
      account_context_issue: null,
    });

    expect(
      screen.getByText("Запуск заблокирован до свежего подтверждения Meta."),
    ).toBeInTheDocument();
  });
});
