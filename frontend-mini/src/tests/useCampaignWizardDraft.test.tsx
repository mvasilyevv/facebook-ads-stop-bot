import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { GeneratedApiError } from "@fb/operator-api";
import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  document: null as ReturnType<typeof draftDocument> | null,
  save: vi.fn(),
  remove: vi.fn(),
  refetch: vi.fn(),
}));

vi.mock("@/lib/campaigns", () => ({
  useCampaignDraft: () => ({
    data: { draft: api.document },
    isSuccess: true,
    isPending: false,
    isError: false,
    error: null,
    refetch: api.refetch,
  }),
  useSaveCampaignDraft: () => ({ mutateAsync: api.save, isPending: false }),
  useDeleteCampaignDraft: () => ({
    mutateAsync: api.remove,
    isPending: false,
  }),
}));

import { useCampaignWizardDraft } from "@/features/campaigns/useCampaignWizardDraft";

function draftDocument(revision: number, offerCode: string) {
  return {
    revision,
    updated_at: "2026-08-09T12:00:00Z",
    state: {
      current_step: 2 as const,
      start: { mode: "new" as const, preset_id: null },
      identity: {
        act_id: "123",
        page_id: "456",
        pixel_id: "789",
        account_context_state: "ready" as const,
        timezone_name: "America/New_York",
        currency: "USD" as const,
        currency_exponent: 2 as const,
        account_context_observed_at: "2026-08-09T11:59:30Z",
        account_context_issue: null,
        offer_code: offerCode,
        byer_tag: "MV",
      },
      goal: {
        objective: "OUTCOME_SALES" as const,
        optimization_goal: "OFFSITE_CONVERSIONS" as const,
        custom_event_type: "PURCHASE" as const,
        display_link: "",
  destination_link: "https://trk.example/click",
        cta: "PLAY_GAME",
        text_optimizations: "OPT_OUT" as const,
        start_date: "2026-08-10",
        budget_level: "campaign" as const,
        daily_budget: "100.00",
        bid_amount: "5.00",
        bid_strategy: "COST_CAP" as const,
        countries: ["US"],
        age_min: 21,
        age_max: 65,
        advantage_audience: true,
        click_through_days: 1 as const,
        view_through_days: 1 as const,
        ad_text_mode: "none" as const,
        ad_text_primary: "",
      },
      structure: { campaigns: [] },
      creatives: {
        upload_id: null,
        concepts: [],
        copies_per_concept: null,
      },
    },
  };
}

function Probe() {
  const draft = useCampaignWizardDraft();
  return (
    <div>
      <span>{draft.state.identity.offer_code}</span>
      <span>{draft.syncState}</span>
      <button
        type="button"
        onClick={() =>
          draft.dispatch({
            type: "patchIdentity",
            value: { offer_code: "LOCAL_B" },
          })
        }
      >
        edit
      </button>
      <button type="button" onClick={() => void draft.reload()}>
        reload
      </button>
    </div>
  );
}

describe("TMA server campaign draft sync", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.document = draftDocument(5, "SERVER_A");
    api.refetch.mockResolvedValue({ data: { draft: api.document } });
    api.save.mockResolvedValue(draftDocument(6, "LOCAL_B"));
    api.remove.mockResolvedValue(undefined);
  });

  it("hydrates after reload and saves with the exact CAS revision", async () => {
    const user = userEvent.setup();
    render(<Probe />);
    await act(async () => {});
    expect(screen.getByText("SERVER_A")).toBeVisible();

    await user.click(screen.getByRole("button", { name: "edit" }));

    await waitFor(() => expect(api.save).toHaveBeenCalledOnce(), {
      timeout: 2_000,
    });
    expect(api.save).toHaveBeenCalledWith(
      expect.objectContaining({ expected_revision: 5 }),
    );
  });

  it("stops on conflict and reloads the authoritative server draft", async () => {
    const user = userEvent.setup();
    api.save.mockRejectedValue(
      new GeneratedApiError(409, { message: "conflict" }),
    );
    render(<Probe />);
    await act(async () => {});

    await user.click(screen.getByRole("button", { name: "edit" }));
    expect(await screen.findByText("conflict", {}, { timeout: 2_000 })).toBeVisible();

    api.document = draftDocument(7, "SERVER_C");
    api.refetch.mockResolvedValue({ data: { draft: api.document } });
    await user.click(screen.getByRole("button", { name: "reload" }));
    expect(screen.getByText("SERVER_C")).toBeVisible();
  });
});
