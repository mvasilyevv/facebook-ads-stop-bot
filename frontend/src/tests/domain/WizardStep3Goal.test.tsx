/**
 * Тесты WizardStep3Goal.
 *
 * Покрываем:
 *   - url_tags-инпут отсутствует в разметке (HIGH mislabel-fix)
 *   - SOP-подсказка присутствует вместо инпута
 *   - validateGoal не требует url_tags
 */
import { describe, it, expect } from "vitest";
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
  start_date: "2026-06-24",
  budget_level: "campaign",
  daily_budget_cents: 20000,
  bid_amount_cents: 500, // $5 целевой CPA (обязателен для COST_CAP)
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

describe("WizardStep3Goal — дневной бюджет без дефолта", () => {
  // Бюджет=0 → поле пустое с placeholder «Введите сумму», а не «0».
  it("пустой бюджет (0) показывает placeholder, не «0»", () => {
    render(<WizardStep3Goal values={{ ...BASE_VALUES, daily_budget_cents: 0 }} onChange={() => {}} />);
    const input = screen.getByPlaceholderText("Введите сумму");
    expect(input).toHaveValue("");
  });

  // Заданный бюджет отображается в долларах.
  it("ненулевой бюджет показывает значение в долларах", () => {
    render(<WizardStep3Goal values={{ ...BASE_VALUES, daily_budget_cents: 20000 }} onChange={() => {}} />);
    expect(screen.getByPlaceholderText("Введите сумму")).toHaveValue("200");
  });
});

describe("WizardStep3Goal — url_tags инпут убран", () => {
  it("не содержит редактируемого поля 'URL Tags (sub2…sub7)'", () => {
    render(<WizardStep3Goal values={BASE_VALUES} onChange={() => {}} />);
    // Поле ввода с таким placeholder не должно присутствовать
    const urlTagsInput = document.querySelector('input[placeholder*="sub2"]');
    expect(urlTagsInput).toBeNull();
  });

  it("не содержит label 'URL Tags (sub2…sub7)' в виде поля ввода", () => {
    render(<WizardStep3Goal values={BASE_VALUES} onChange={() => {}} />);
    // input с aria-label или label, содержащим sub2…sub7, не должно быть
    const allInputs = document.querySelectorAll("input");
    const urlTagsInputs = Array.from(allInputs).filter((el) =>
      (el.getAttribute("placeholder") ?? "").includes("sub2"),
    );
    expect(urlTagsInputs).toHaveLength(0);
  });

  it("содержит подсказку о SOP-трекинге вместо инпута", () => {
    render(<WizardStep3Goal values={BASE_VALUES} onChange={() => {}} />);
    // Проверяем что есть текст про SOP или автоматический трекинг
    const sopText = screen.getByText(/трекинг по SOP|вычисляет автоматически/i);
    expect(sopText).toBeTruthy();
  });
});

describe("WizardStep3Goal — инварианты зашиты, CPA вводится", () => {
  // Дропдаунов Objective/Optimization Goal больше нет — инварианты read-only
  it("не содержит редактируемого дропдауна Objective", () => {
    render(<WizardStep3Goal values={BASE_VALUES} onChange={() => {}} />);
    expect(screen.queryByLabelText(/^Objective$/i)).toBeNull();
    expect(screen.queryByLabelText(/Optimization Goal/i)).toBeNull();
    expect(screen.queryByLabelText(/Bid Strategy/i)).toBeNull();
  });

  // Read-only блок «Зашито по SOP» показывает инварианты как текст
  it("показывает read-only инварианты SOP (Cost cap, IMPRESSIONS)", () => {
    render(<WizardStep3Goal values={BASE_VALUES} onChange={() => {}} />);
    expect(screen.getByText("Cost cap")).toBeInTheDocument();
    expect(screen.getByText("IMPRESSIONS")).toBeInTheDocument();
    expect(screen.getByText("OFFSITE_CONVERSIONS")).toBeInTheDocument();
  });

  // Поле «Целевой CPA, $» присутствует и отражает значение из стора (центы → $)
  it("содержит поле «Целевой CPA, $» со значением из bid_amount_cents", () => {
    render(<WizardStep3Goal values={BASE_VALUES} onChange={() => {}} />);
    const cpa = screen.getByLabelText(/Целевой CPA/i) as HTMLInputElement;
    expect(cpa).toBeInTheDocument();
    expect(cpa.value).toBe("5"); // 500 центов = $5
  });
});

describe("validateGoal — не требует url_tags", () => {
  it("валидный конфиг без url_tags → нет ошибок", () => {
    const errors = validateGoal(BASE_VALUES);
    expect(Object.keys(errors)).toHaveLength(0);
  });

  it("пустой destination_link → ошибка", () => {
    const errors = validateGoal({ ...BASE_VALUES, destination_link: "" });
    expect(errors.destination_link).toBeTruthy();
  });

  it("слишком маленький бюджет < $1 → ошибка", () => {
    const errors = validateGoal({ ...BASE_VALUES, daily_budget_cents: 50 });
    expect(errors.daily_budget_cents).toBeTruthy();
  });

  it("пустые страны → ошибка", () => {
    const errors = validateGoal({ ...BASE_VALUES, countries: [] });
    expect(errors.countries).toBeTruthy();
  });
});
