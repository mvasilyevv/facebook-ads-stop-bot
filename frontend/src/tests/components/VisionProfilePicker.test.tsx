import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const useVisionSettings = vi.fn();
const useVisionProfiles = vi.fn();
const updateMutate = vi.fn();

vi.mock("@/lib/api/settings", () => ({
  useVisionSettings: () => useVisionSettings(),
  useVisionProfiles: () => useVisionProfiles(),
  useUpdateVisionSettings: () => ({ mutateAsync: updateMutate, isPending: false }),
  useReconnectVision: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

import { VisionTab } from "@/components/settings/VisionTab";

const SELECTED = "6a572873-24df-43b0-be1d-98939ac3b2e9";
const OTHER = "3a47ef23-c5bf-4bdb-bbda-275173a6d64d";

function renderTab() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <VisionTab />
    </QueryClientProvider>,
  );
}

function profiles(overrides: Record<string, unknown> = {}) {
  return {
    data: {
      state: "ready",
      reason: "READY",
      message: "",
      selected_profile_id: SELECTED,
      selected_present: true,
      items: [
        {
          id: SELECTED,
          name: "Desk 10 1000091",
          status: "Активно",
          tags: ["OBUCH"],
          running: true,
          last_run_at: null,
        },
        { id: OTHER, name: "desk10 2608 2B", status: "BAN", tags: ["OBUCH"], running: false },
      ],
      ...overrides,
    },
    isPending: false,
    isFetching: false,
    refetch: vi.fn(),
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  useVisionSettings.mockReturnValue({
    data: {
      has_token: true,
      profile_id: SELECTED,
      channel_status: "READY",
      required_browser_contract_version: 5,
    },
    isLoading: false,
    error: null,
    refetch: vi.fn(),
  });
  useVisionProfiles.mockReturnValue(profiles());
});

describe("выбор профиля Vision", () => {
  it("показывает профили именами, а не идентификаторами", () => {
    renderTab();

    const picker = screen.getByLabelText("Профиль Vision");

    expect(picker).toHaveValue(SELECTED);
    expect(screen.getByRole("option", { name: /Desk 10 1000091/ })).toBeInTheDocument();
  });

  it("показывает статус профиля, чтобы забаненный не выбрали вслепую", () => {
    renderTab();

    expect(screen.getByRole("option", { name: /desk10 2608 2B · BAN/ })).toBeInTheDocument();
  });

  it("говорит прямо, когда настроенный профиль исчез из облака", () => {
    useVisionProfiles.mockReturnValue(
      profiles({
        selected_present: false,
        message: "Настроенный профиль исчез из облака: его переименовали, пересоздали или удалили.",
        items: [{ id: OTHER, name: "desk10 2608 2B", status: "BAN", tags: [], running: false }],
      }),
    );

    renderTab();

    expect(screen.getByText(/исчез из облака/)).toBeInTheDocument();
    // Исчезнувший профиль остаётся выбранным: подставить соседний — значит
    // молча нацелиться на чужой кабинет.
    expect(screen.getByLabelText("Профиль Vision")).toHaveValue(SELECTED);
    expect(screen.getByRole("option", { name: /в облаке не найден/ })).toBeInTheDocument();
  });

  it("перечитывает список по требованию, не полагаясь на кэш", async () => {
    const refetch = vi.fn();
    useVisionProfiles.mockReturnValue({ ...profiles(), refetch });

    renderTab();
    await userEvent.click(screen.getByRole("button", { name: "Обновить список" }));

    expect(refetch).toHaveBeenCalledOnce();
  });

  it("объясняет недоступность списка вместо пустого выпадающего", () => {
    useVisionProfiles.mockReturnValue({
      data: {
        state: "unavailable",
        reason: "TOKEN_REJECTED",
        message: "Облако Vision отвергло токен, список профилей получить не удалось.",
        items: [],
        selected_profile_id: SELECTED,
        selected_present: false,
      },
      isPending: false,
      isFetching: false,
      refetch: vi.fn(),
    });

    renderTab();

    expect(screen.getByText(/отвергло токен/)).toBeInTheDocument();
    expect(screen.getByLabelText("Профиль Vision")).toBeDisabled();
  });

  it("сохраняет выбранный профиль идентификатором", async () => {
    updateMutate.mockResolvedValue({});
    renderTab();

    await userEvent.selectOptions(screen.getByLabelText("Профиль Vision"), OTHER);
    await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));

    await waitFor(() =>
      expect(updateMutate).toHaveBeenCalledWith(expect.objectContaining({ profile_id: OTHER })),
    );
  });
});
