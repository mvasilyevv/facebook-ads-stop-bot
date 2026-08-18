import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api/campaigns", () => ({
  useAdAccountContext: () => ({ mutate: vi.fn(), isPending: false }),
  useAdAccountPages: () => ({ mutate: vi.fn(), isPending: false }),
  useAdAccountPixels: () => ({ mutate: vi.fn(), isPending: false }),
}));

vi.mock("@/lib/api/offers", () => ({
  useOffers: () => ({
    data: [
      {
        code: "GH_AVI",
        name: "GH Aviator",
        ad_account_ids: ["2108857220005012", "3570379159805007"],
        countries: [],
      },
    ],
    isLoading: false,
  }),
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

describe("WizardStep2Identity — кабинеты оффера", () => {
  // Кабинеты оффера видны все сразу и отмечаются галочкой: добавление по одному
  // через выпадающий список заставляло искать нужный кабинет вслепую.
  it("показывает кабинеты оффера списком с отметками", () => {
    renderStep({ offer_code: "GH_AVI", ad_account_ids: ["2108857220005012"] });

    const checked = screen.getByRole("checkbox", { name: /2108857220005012/ });
    expect(checked).toBeChecked();
    expect(screen.getByRole("checkbox", { name: /3570379159805007/ })).not.toBeChecked();
  });

  // act_ — префикс транспорта Meta, а не часть идентичности кабинета. В интерфейсе
  // он только удлинял строку и мешал сверить ID глазами.
  it("не показывает префикс act_", () => {
    renderStep({ offer_code: "GH_AVI", ad_account_ids: ["2108857220005012"] });

    expect(screen.queryByText(/act_/)).toBeNull();
  });
});
