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
  Link: ({ children, to }: { children: ReactNode; to: string }) => <a href={to}>{children}</a>,
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

    await waitFor(() =>
      expect(api.update).toHaveBeenCalledWith(
        expect.objectContaining({
          name: "US scale",
          genders: ["female"],
          custom_event_type: "PURCHASE",
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
    fireEvent.click(screen.getByRole("button", { name: "Создать пресет" }));
    await waitFor(() => expect(api.create).toHaveBeenCalledOnce());

    fireEvent.click(screen.getByRole("button", { name: "Удалить US broad" }));
    fireEvent.click(screen.getByRole("button", { name: "Удалить пресет" }));
    await waitFor(() => expect(api.remove).toHaveBeenCalledWith("preset-1"));
  });
});
