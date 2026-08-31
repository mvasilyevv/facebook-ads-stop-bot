import type { ComponentType, ReactNode } from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  query: vi.fn(),
  create: vi.fn(),
  update: vi.fn(),
  remove: vi.fn(),
}));

vi.mock("@tanstack/react-router", () => ({
  createFileRoute: () => (options: { component: ComponentType }) => options,
  Link: ({
    children,
    to,
    search,
  }: {
    children: ReactNode;
    to: string;
    search?: Record<string, string>;
  }) => (
    <a href={search ? `${to}?${new URLSearchParams(search).toString()}` : to}>{children}</a>
  ),
}));

vi.mock("@/components/layout/PageHeader", () => ({
  PageHeader: ({ title, action }: { title: string; action?: ReactNode }) => (
    <header>
      <h1>{title}</h1>
      {action}
    </header>
  ),
}));

vi.mock("@/components/ui/CountryMultiSelect", () => ({
  CountryMultiSelect: ({
    label,
    values,
    onChange,
  }: {
    label: string;
    values: string[];
    onChange: (values: string[]) => void;
  }) => (
    <button type="button" onClick={() => onChange(["US"])}>
      {label}: {values.join(",") || "empty"}
    </button>
  ),
}));

vi.mock("@/stores/campaignWizard", async () => {
  const feature = await import("@fb/features/campaigns");
  return { getWizardFeatureState: () => feature.createCampaignWizardState() };
});

vi.mock("@/lib/api/campaigns", () => ({
  usePresets: () => api.query(),
  useCreatePreset: () => ({ mutateAsync: api.create, isPending: false }),
  useUpdatePreset: () => ({ mutateAsync: api.update, isPending: false }),
  useDeletePreset: () => ({ mutateAsync: api.remove, isPending: false }),
}));

import { Route } from "@/routes/campaigns/presets/index";

const PresetsPage = (Route as unknown as { component: ComponentType }).component;

const PRESET = {
  id: "preset-1",
  name: "US broad",
  countries: ["US"],
  age_min: 21,
  age_max: 65,
  genders: [],
  placements: ["facebook"],
  custom_event_type: "PURCHASE" as const,
  budget_level: "campaign" as const,
  daily_budget: "200.00",
  bid_strategy: "COST_CAP" as const,
  bid_amount: "5.00",
  display_link: "play.example.com",
  naming_template: null,
  url_tags_template: null,
  created_at: "2026-08-15T08:00:00Z",
  updated_at: "2026-08-15T08:00:00Z",
};

describe("CampaignPresetsPage", () => {
  beforeEach(() => {
    api.create.mockReset().mockResolvedValue(PRESET);
    api.update.mockReset().mockResolvedValue(PRESET);
    api.remove.mockReset().mockResolvedValue(undefined);
    api.query.mockReturnValue({
      data: [PRESET],
      isPending: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });
  });

  it("различает empty и unavailable", () => {
    api.query.mockReturnValueOnce({
      data: [],
      isPending: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });
    const { unmount } = render(<PresetsPage />);
    expect(screen.getByText("Пресетов пока нет")).toBeInTheDocument();
    unmount();

    api.query.mockReturnValueOnce({
      data: undefined,
      isPending: false,
      isError: true,
      error: new Error("offline"),
      refetch: vi.fn(),
    });
    render(<PresetsPage />);
    expect(screen.getByText("Пресеты недоступны")).toBeInTheDocument();
  });

  it("переименовывает и сохраняет полный редактируемый snapshot", async () => {
    render(<PresetsPage />);

    fireEvent.click(screen.getByRole("button", { name: "Изменить US broad" }));
    fireEvent.change(screen.getByLabelText("Название пресета"), {
      target: { value: "US scale" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Женщины" }));
    fireEvent.click(screen.getByRole("button", { name: "Сохранить изменения" }));

    // #347 — ставка и отображаемая ссылка пресета должны доехать до payload
    // нетронутыми, а не потеряться при открытии/сохранении редактора.
    await waitFor(() =>
      expect(api.update).toHaveBeenCalledWith(
        expect.objectContaining({
          name: "US scale",
          genders: ["female"],
          custom_event_type: "PURCHASE",
          bid_strategy: "COST_CAP",
          bid_amount: "5.00",
          display_link: "play.example.com",
        }),
      ),
    );
  });

  it("создаёт и удаляет пресет через отдельные CRUD-действия", async () => {
    render(<PresetsPage />);

    fireEvent.click(screen.getByRole("button", { name: "Создать пресет" }));
    fireEvent.change(screen.getByLabelText("Название пресета"), {
      target: { value: "US fresh" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Гео: empty/ }));
    fireEvent.change(screen.getByLabelText("Дневной бюджет, USD"), {
      target: { value: "150.00" },
    });
    // Стратегия по умолчанию — COST_CAP, ей нужна ставка (issue #347): без неё
    // форма не соберётся, как и визард не соберёт COST_CAP-конфиг без bid_amount.
    fireEvent.change(screen.getByLabelText("Цель по цене за результат, USD"), {
      target: { value: "5.00" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Создать пресет" }));
    await waitFor(() => expect(api.create).toHaveBeenCalledOnce());
    expect(api.create).toHaveBeenCalledWith(
      expect.objectContaining({ bid_strategy: "COST_CAP", bid_amount: "5.00" }),
    );

    fireEvent.click(screen.getByRole("button", { name: "Удалить US broad" }));
    fireEvent.click(screen.getByRole("button", { name: "Удалить пресет" }));
    await waitFor(() => expect(api.remove).toHaveBeenCalledWith("preset-1"));
  });

  // #345 QW11 — карточка пресета вела только в редактор; применить его к
  // новой кампании можно было только вручную выбрав тот же id на шаге 1.
  it("ведёт «Применить и создать» на визард с выбранным пресетом", () => {
    render(<PresetsPage />);

    const link = screen.getByRole("link", { name: /Применить и создать/ });
    expect(link).toHaveAttribute("href", "/campaigns/create?preset=preset-1");
  });
});
