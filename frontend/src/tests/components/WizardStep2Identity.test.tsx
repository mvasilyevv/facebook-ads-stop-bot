import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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
        ad_account_ids: ["1234567890123456", "9876543210987654"],
        countries: [],
        pixel_id: "555",
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
  pixel_confirmed: false,
  account_context_state: "unavailable",
  timezone_name: "",
  currency: "",
  currency_exponent: null,
  account_context_observed_at: null,
  account_context_issue: null,
  offer_code: "",
  byer_tag: "",
};

function renderStep(overrides: Partial<WizardIdentity>, onChange: (v: Partial<WizardIdentity>) => void = () => {}) {
  return render(
    <WizardStep2Identity
      values={{ ...BASE_IDENTITY, ...overrides }}
      onChange={onChange}
      onGoalChange={() => {}}
    />,
  );
}

describe("WizardStep2Identity — контекст кабинета", () => {
  // Оператору нужна причина, а не факт блокировки: «Контекст недоступен» без
  // объяснения отправляет его гадать, что именно не так с кабинетом.
  it("показывает причину недоступного контекста", () => {
    renderStep({
      act_id: "1234567890123456",
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
      act_id: "1234567890123456",
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
    renderStep({ offer_code: "GH_AVI", ad_account_ids: ["1234567890123456"] });

    const checked = screen.getByRole("checkbox", { name: /1234567890123456/ });
    expect(checked).toBeChecked();
    expect(screen.getByRole("checkbox", { name: /9876543210987654/ })).not.toBeChecked();
  });

  // act_ — префикс транспорта Meta, а не часть идентичности кабинета. В интерфейсе
  // он только удлинял строку и мешал сверить ID глазами.
  it("не показывает префикс act_", () => {
    renderStep({ offer_code: "GH_AVI", ad_account_ids: ["1234567890123456"] });

    expect(screen.queryByText(/act_/)).toBeNull();
  });
});

describe("WizardStep2Identity — расхождение пикселя с оффером (issue #359)", () => {
  // Тихий случай из issue: пиксель валиден, но чужой офферу — сервер это
  // отклонит, а UI обязан предупредить раньше, а не молчать.
  it("показывает предупреждение, когда пиксель разошёлся с офферным", () => {
    renderStep({ offer_code: "GH_AVI", pixel_id: "999" });

    expect(screen.getByText(/отличается от привязанного к офферу/)).toBeInTheDocument();
  });

  it("не показывает предупреждение, когда пиксель совпадает с офферным", () => {
    renderStep({ offer_code: "GH_AVI", pixel_id: "555" });

    expect(screen.queryByText(/отличается от привязанного к офферу/)).toBeNull();
  });

  it("не показывает предупреждение без выбранного оффера (нечего сверять)", () => {
    renderStep({ offer_code: "", pixel_id: "999" });

    expect(screen.queryByText(/отличается от привязанного к офферу/)).toBeNull();
  });

  it("чекбокс подтверждения переключает pixel_confirmed на true", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    renderStep({ offer_code: "GH_AVI", pixel_id: "999", pixel_confirmed: false }, onChange);

    await user.click(screen.getByRole("checkbox", { name: /осознанно/ }));

    expect(onChange).toHaveBeenCalledWith({ pixel_confirmed: true });
  });
});
