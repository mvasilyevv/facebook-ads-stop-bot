import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  fetchNextPage: vi.fn(),
}));

function run(id: string, offerCode: string, createdAt: string) {
  return {
    id,
    preset_id: null,
    status: "succeeded",
    offer_code: offerCode,
    idempotency_key: `campaign-${id}`,
    error: null,
    created_at: createdAt,
    updated_at: createdAt,
  };
}

vi.mock("@/lib/operatorApi", () => ({
  operatorProblemMessage: (error: unknown) =>
    error instanceof Error ? error.message : "Ошибка",
  useCampaignRun: () => ({ data: null, error: null, isError: false, isLoading: false, refetch: vi.fn() }),
  useAbortCampaignRun: () => ({ isPending: false, mutateAsync: vi.fn() }),
  useResumeCampaignRun: () => ({ isPending: false, mutateAsync: vi.fn() }),
  useRunsHistory: () => ({
    data: {
      pages: [
        {
          runs: [run("run-1", "GH_CR2", "2026-07-21T10:00:00Z"), run("run-2", "DRC_CR", "2026-07-20T09:00:00Z")],
          total: 3,
          offset: 0,
          limit: 2,
        },
      ],
    },
    error: null,
    isError: false,
    isLoading: false,
    isFetching: false,
    hasNextPage: true,
    isFetchingNextPage: false,
    fetchNextPage: mocks.fetchNextPage,
    refetch: vi.fn(),
  }),
}));

vi.mock("@/lib/tg", () => ({
  haptic: { impact: vi.fn(), notify: vi.fn(), selection: vi.fn() },
  openLink: vi.fn(),
}));

import { RunsHistory } from "@/routes/campaigns/RunsHistory";

describe("RunsHistory pagination (issue #340)", () => {
  it("shows an honest shown-of-total count and a working show-more control", async () => {
    const user = userEvent.setup();
    render(<RunsHistory />);

    // Сервер подтвердил total=3, накоплено 2 — честно «2 из 3», а не «3».
    expect(screen.getByText("2 из 3 запуска")).toBeInTheDocument();
    expect(screen.getByText("GH_CR2")).toBeInTheDocument();
    expect(screen.getByText("DRC_CR")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Показать ещё" }));
    expect(mocks.fetchNextPage).toHaveBeenCalledOnce();
  });
});
