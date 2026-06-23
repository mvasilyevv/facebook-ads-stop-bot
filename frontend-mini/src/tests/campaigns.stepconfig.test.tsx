/**
 * Тесты StepConfig mini-визарда.
 *
 * Покрываем:
 *   - пустой бюджет блокирует «Далее» (MID null-бюджет fix)
 *   - бюджет < $1 блокирует «Далее»
 *   - валидный бюджет + ссылка → переход дальше
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { useWizardStore } from "@/routes/campaigns/-wizardStore";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StepConfig } from "@/routes/campaigns/StepConfig";

vi.mock("@/lib/tg", () => ({
  haptic: { impact: vi.fn(), selection: vi.fn() },
}));

vi.mock("@tanstack/react-router", () => ({
  createFileRoute: () => ({ component: (c: unknown) => c }),
  useNavigate: () => vi.fn(),
}));

function TestWrapper({ children }: { children: React.ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

describe("StepConfig — валидация бюджета", () => {
  beforeEach(() => {
    useWizardStore.getState().reset();
    useWizardStore.getState().setStep("config");
  });

  function renderConfig() {
    return render(<TestWrapper><StepConfig /></TestWrapper>);
  }

  it("пустой бюджет → показывает ошибку, не переходит дальше (money-guard)", () => {
    renderConfig();
    // Заполняем ссылку, оставляем бюджет пустым
    fireEvent.change(screen.getByLabelText(/Ссылка назначения/i), {
      target: { value: "https://example.com" },
    });
    // Очищаем поле бюджета
    const budgetInput = screen.getByLabelText(/Дневной бюджет/i);
    fireEvent.change(budgetInput, { target: { value: "" } });

    fireEvent.click(screen.getByRole("button", { name: /далее/i }));

    expect(screen.getByText(/минимум \$1|Укажите дневной бюджет/i)).toBeTruthy();
    // Шаг не изменился
    expect(useWizardStore.getState().step).toBe("config");
  });

  it("бюджет = 0 → показывает ошибку минимального значения", () => {
    renderConfig();
    fireEvent.change(screen.getByLabelText(/Ссылка назначения/i), {
      target: { value: "https://example.com" },
    });
    fireEvent.change(screen.getByLabelText(/Дневной бюджет/i), {
      target: { value: "0" },
    });
    fireEvent.click(screen.getByRole("button", { name: /далее/i }));

    expect(screen.getByText(/минимум \$1|Минимальный бюджет/i)).toBeTruthy();
    expect(useWizardStore.getState().step).toBe("config");
  });

  it("бюджет > $100 000 → показывает ошибку превышения", () => {
    renderConfig();
    fireEvent.change(screen.getByLabelText(/Ссылка назначения/i), {
      target: { value: "https://example.com" },
    });
    fireEvent.change(screen.getByLabelText(/Дневной бюджет/i), {
      target: { value: "200000" },
    });
    fireEvent.click(screen.getByRole("button", { name: /далее/i }));

    expect(screen.getByText(/превышает \$100 000/i)).toBeTruthy();
    expect(useWizardStore.getState().step).toBe("config");
  });

  it("валидный бюджет $50 + ссылка → переходит на structure", async () => {
    renderConfig();
    fireEvent.change(screen.getByLabelText(/Ссылка назначения/i), {
      target: { value: "https://trk.example.com/click" },
    });
    fireEvent.change(screen.getByLabelText(/Дневной бюджет/i), {
      target: { value: "50" },
    });
    fireEvent.click(screen.getByRole("button", { name: /далее/i }));

    await waitFor(() => {
      expect(useWizardStore.getState().step).toBe("structure");
    });
  });

  it("бюджет сохраняется в центах в store", async () => {
    renderConfig();
    fireEvent.change(screen.getByLabelText(/Ссылка назначения/i), {
      target: { value: "https://trk.example.com" },
    });
    fireEvent.change(screen.getByLabelText(/Дневной бюджет/i), {
      target: { value: "75.50" },
    });
    fireEvent.click(screen.getByRole("button", { name: /далее/i }));

    await waitFor(() => {
      // $75.50 = 7550 центов
      expect(useWizardStore.getState().config.daily_budget_cents).toBe(7550);
    });
  });
});
