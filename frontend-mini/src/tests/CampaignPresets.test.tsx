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

vi.mock("@/components/layout/MiniHeader", () => ({
  MiniHeader: ({ title, right }: { title: string; right?: ReactNode }) => (
    <header>
      <h1>{title}</h1>
      {right}
    </header>
  ),
}));

vi.mock("@/lib/auth", () => ({ getStoredRole: () => "owner" }));

vi.mock("@/lib/campaigns", () => ({
  useCampaignPresets: () => api.query(),
  useCreateCampaignPreset: () => ({
    mutateAsync: api.create,
    isPending: false,
  }),
  useUpdateCampaignPreset: () => ({
    mutateAsync: api.update,
    isPending: false,
  }),
  useDeleteCampaignPreset: () => ({
    mutateAsync: api.remove,
    isPending: false,
  }),
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

describe("mini CampaignPresetsPage", () => {
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

  it("показывает объяснённый empty, а ошибку — как unavailable", () => {
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
    expect(screen.getByText(/Пресеты недоступны/)).toBeInTheDocument();
  });

  it("переименовывает snapshot и сохраняет Purchase", async () => {
    render(<PresetsPage />);

    fireEvent.click(screen.getByRole("button", { name: "Изменить" }));
    fireEvent.change(screen.getByLabelText("Название"), {
      target: { value: "US scale" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Женщины" }));
    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));

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

  it("удаляет только выбранный пресет", async () => {
    render(<PresetsPage />);

    fireEvent.click(screen.getByRole("button", { name: "Удалить" }));
    fireEvent.click(screen.getByRole("button", { name: "Удалить" }));

    await waitFor(() => expect(api.remove).toHaveBeenCalledWith("preset-1"));
  });
});
