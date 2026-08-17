/** Campaign budget UI and exact currency-precision validation. */
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { WizardStep3Goal, validateGoal } from "@/components/domain/campaigns/WizardStep3Goal";
import type { WizardGoal } from "@/stores/campaignWizard";

const BASE_VALUES: WizardGoal = {
  objective: "OUTCOME_SALES",
  optimization_goal: "OFFSITE_CONVERSIONS",
  custom_event_type: "PURCHASE",
  display_link: "",
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
  genders: [],
  placements: [],
  click_through_days: 1,
  view_through_days: 1,
  naming_template: "",
  url_tags_template: "",
  ad_text_mode: "none",
  ad_text_primary: "",
};

function renderGoal(values: WizardGoal = BASE_VALUES) {
  return render(
    <WizardStep3Goal values={values} onChange={() => {}} currency="USD" currencyExponent={2} />,
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
    // Подпись поля ставки — это название выбранной стратегии, а не общее
    // «Целевой CPA»: у предельной ставки смысл поля другой.
    expect(screen.getByLabelText(/Цель по цене за результат \(USD\)/i)).toHaveValue("5.00");
  });
});

describe("WizardStep3Goal — редактируемые поля пресета", () => {
  it("показывает URL tags и нейминг как обычные поля", () => {
    renderGoal();

    expect(screen.getByLabelText("URL Tags")).toBeEnabled();
    expect(screen.getByLabelText("Шаблон нейминга")).toBeEnabled();
  });
});

describe("WizardStep3Goal — верхняя граница возраста при Advantage+", () => {
  it("при Advantage+ показывает 65 и не даёт задать своё значение", () => {
    // Билдер всё равно отправит 65: Meta отвергает adset с меньшим капом.
    // Поле не должно показывать выбор, который будет молча заменён.
    renderGoal({ ...BASE_VALUES, advantage_audience: true, age_max: 45 });

    const ageMax = screen.getByLabelText("Возраст до");
    expect(ageMax).toHaveValue(65);
    expect(ageMax).toBeDisabled();
    expect(screen.getByText(/Advantage\+ сам расширяет аудиторию/)).toBeInTheDocument();
  });

  it("без Advantage+ верхняя граница остаётся выбором оператора", () => {
    renderGoal({ ...BASE_VALUES, advantage_audience: false, age_max: 45 });

    const ageMax = screen.getByLabelText("Возраст до");
    expect(ageMax).toHaveValue(45);
    expect(ageMax).toBeEnabled();
  });
});

describe("WizardStep3Goal — SOP-инварианты", () => {
  it("не содержит редактируемых Objective и Optimization Goal", () => {
    renderGoal();

    expect(screen.queryByLabelText(/^Objective$/i)).toBeNull();
    expect(screen.queryByLabelText(/Optimization Goal/i)).toBeNull();
    expect(screen.getByText("IMPRESSIONS")).toBeInTheDocument();
  });

  it("стратегия ставок выбирается, а не зашита", () => {
    // Замер 17.08 по трём кабинетам: 41 живая кампания из 55 идёт на
    // «Максимальном количестве». «Зашито по SOP» было неправдой, и оператор
    // не мог повторить три четверти того, что уже работает.
    renderGoal();

    expect(screen.getByLabelText("Стратегия ставок")).toBeInTheDocument();
    expect(screen.queryByText("Cost cap")).toBeNull();
  });

  it("прячет поле ставки у стратегии без кэпа", () => {
    // У «Максимального количества» ставки нет вовсе: пустое обязательное поле
    // сбивало бы с толку и роняло валидацию на ровном месте.
    renderGoal({ ...BASE_VALUES, bid_strategy: "LOWEST_COST_WITHOUT_CAP" });

    expect(screen.queryByLabelText(/Цель по цене за результат/)).toBeNull();
    expect(screen.getByLabelText("Стратегия ставок")).toHaveValue("LOWEST_COST_WITHOUT_CAP");
  });
});

describe("validateGoal — exact exponent contract", () => {
  it("валидный двухзнаковый конфиг проходит", () => {
    expect(validateGoal(BASE_VALUES, 2)).toEqual({});
  });

  it("unknown exponent блокирует денежные поля", () => {
    const errors = validateGoal(BASE_VALUES, null);

    expect(errors.daily_budget).toMatch(/USD-контекст/i);
    expect(errors.bid_amount).toMatch(/USD-контекст/i);
  });

  it("блокирует денежные поля при любой неподтверждённой USD-точности", () => {
    const errors = validateGoal({ ...BASE_VALUES, daily_budget: "200.5", bid_amount: "5.1" }, 0);

    expect(errors.daily_budget).toMatch(/USD-контекст/i);
    expect(errors.bid_amount).toMatch(/USD-контекст/i);
  });

  it("USD принимает максимум две значимые дробные цифры", () => {
    expect(validateGoal({ ...BASE_VALUES, daily_budget: "200.00", bid_amount: "1.23" }, 2)).toEqual(
      {},
    );
    expect(
      validateGoal({ ...BASE_VALUES, daily_budget: "200.00", bid_amount: "1.234" }, 2).bid_amount,
    ).toMatch(/не более 2/i);
  });

  it("отклоняет значения выше hard cap без float conversion", () => {
    const errors = validateGoal({ ...BASE_VALUES, daily_budget: "100000.01" }, 2);

    expect(errors.daily_budget).toMatch(/максимум/i);
  });

  it("пустой destination и countries остаются явными ошибками", () => {
    const errors = validateGoal({ ...BASE_VALUES, destination_link: "", countries: [] }, 2);

    expect(errors.destination_link).toBeTruthy();
    expect(errors.countries).toBeTruthy();
  });

  it("пустая дата разрешена: сервер выберет следующий локальный день кабинета", () => {
    expect(validateGoal({ ...BASE_VALUES, start_date: "" }, 2).start_date).toBeUndefined();
  });
});
