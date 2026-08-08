/** Campaign budget UI and exact currency-precision validation. */
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { WizardStep3Goal, validateGoal } from "@/components/domain/campaigns/WizardStep3Goal";
import type { WizardGoal } from "@/stores/campaignWizard";

const BASE_VALUES: WizardGoal = {
  objective: "OUTCOME_SALES",
  optimization_goal: "OFFSITE_CONVERSIONS",
  custom_event_type: "PURCHASE",
  destination_link: "https://trk.example.com",
  cta: "PLAY_GAME",
  text_optimizations: "OPT_OUT",
  start_date: "2099-07-30",
  budget_level: "campaign",
  daily_budget: "200.00",
  bid_amount: "5.00",
  bid_strategy: "COST_CAP",
  countries: ["GH"],
  age_min: 21,
  age_max: 65,
  advantage_audience: true,
  click_through_days: 1,
  view_through_days: 1,
  ad_text_mode: "none",
  ad_text_primary: "",
};

function renderGoal(values: WizardGoal = BASE_VALUES) {
  return render(
    <WizardStep3Goal
      values={values}
      onChange={() => {}}
      currency="GHS"
      currencyExponent={2}
    />,
  );
}

describe("WizardStep3Goal — currency-aware major units", () => {
  it("пустой бюджет показывает placeholder, не подставляет ноль", () => {
    renderGoal({ ...BASE_VALUES, daily_budget: "" });

    expect(screen.getByPlaceholderText("Введите сумму")).toHaveValue("");
  });

  it("показывает exact decimal string и код валюты кабинета", () => {
    renderGoal();

    expect(screen.getByPlaceholderText("Введите сумму")).toHaveValue("200.00");
    expect(screen.getByLabelText(/Целевой CPA \(GHS\)/i)).toHaveValue("5.00");
    expect(document.body.textContent).not.toContain("$");
  });
});

describe("WizardStep3Goal — url_tags input удалён", () => {
  it("показывает SOP-подсказку без редактируемого sub2-поля", () => {
    renderGoal();

    expect(document.querySelector('input[placeholder*="sub2"]')).toBeNull();
    expect(screen.getByText(/трекинг по SOP|вычисляет автоматически/i)).toBeInTheDocument();
  });
});

describe("WizardStep3Goal — SOP-инварианты", () => {
  it("не содержит редактируемых Objective, Optimization Goal и Bid Strategy", () => {
    renderGoal();

    expect(screen.queryByLabelText(/^Objective$/i)).toBeNull();
    expect(screen.queryByLabelText(/Optimization Goal/i)).toBeNull();
    expect(screen.queryByLabelText(/Bid Strategy/i)).toBeNull();
    expect(screen.getByText("Cost cap")).toBeInTheDocument();
    expect(screen.getByText("IMPRESSIONS")).toBeInTheDocument();
  });
});

describe("validateGoal — exact exponent contract", () => {
  it("валидный двухзнаковый конфиг проходит", () => {
    expect(validateGoal(BASE_VALUES, 2)).toEqual({});
  });

  it("unknown exponent блокирует денежные поля", () => {
    const errors = validateGoal(BASE_VALUES, null);

    expect(errors.daily_budget).toMatch(/валютный контекст/i);
    expect(errors.bid_amount).toMatch(/валютный контекст/i);
  });

  it("JPY отклоняет ненулевую дробную часть", () => {
    const errors = validateGoal(
      { ...BASE_VALUES, daily_budget: "200.5", bid_amount: "5.1" },
      0,
    );

    expect(errors.daily_budget).toMatch(/целые/i);
    expect(errors.bid_amount).toMatch(/целые/i);
  });

  it("JPY принимает trailing zero без потери точности", () => {
    expect(
      validateGoal({ ...BASE_VALUES, daily_budget: "200.0", bid_amount: "5.000" }, 0),
    ).toEqual({});
  });

  it("трёхзнаковая валюта принимает 1.234 и отклоняет 1.2341", () => {
    expect(
      validateGoal({ ...BASE_VALUES, daily_budget: "200.000", bid_amount: "1.234" }, 3),
    ).toEqual({});
    expect(
      validateGoal({ ...BASE_VALUES, daily_budget: "200.000", bid_amount: "1.2341" }, 3)
        .bid_amount,
    ).toMatch(/не более 3/i);
  });

  it("отклоняет значения выше hard cap без float conversion", () => {
    const errors = validateGoal({ ...BASE_VALUES, daily_budget: "100000.01" }, 2);

    expect(errors.daily_budget).toMatch(/максимум/i);
  });

  it("пустой destination и countries остаются явными ошибками", () => {
    const errors = validateGoal(
      { ...BASE_VALUES, destination_link: "", countries: [] },
      2,
    );

    expect(errors.destination_link).toBeTruthy();
    expect(errors.countries).toBeTruthy();
  });

  it("пустая дата разрешена: сервер выберет следующий локальный день кабинета", () => {
    expect(validateGoal({ ...BASE_VALUES, start_date: "" }, 2).start_date).toBeUndefined();
  });
});
