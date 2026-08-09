import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { GeneratedApiError } from "@fb/operator-api";

const api = vi.hoisted(() => ({
  document: null as Record<string, unknown> | null,
  save: vi.fn(),
  remove: vi.fn(),
  refetch: vi.fn(),
}));

vi.mock("@/lib/api/campaigns", () => ({
  useCampaignDraft: () => ({
    data: { draft: api.document },
    isSuccess: true,
    isPending: false,
    isError: false,
    error: null,
    refetch: api.refetch,
  }),
  useSaveCampaignDraft: () => ({ mutateAsync: api.save, isPending: false }),
  useDeleteCampaignDraft: () => ({ mutateAsync: api.remove, isPending: false }),
}));

import { useCampaignDraftSync } from "@/features/campaigns/useCampaignDraftSync";
import { useWizardStore } from "@/stores/campaignWizard";

function draftDocument(revision: number, offerCode: string) {
  return {
    revision,
    updated_at: "2026-08-09T12:00:00Z",
    state: {
      current_step: 2,
      start: { mode: "new", preset_id: null },
      identity: {
        act_id: "123",
        page_id: "456",
        pixel_id: "789",
        account_context_state: "ready",
        timezone_name: "America/New_York",
        currency: "USD",
        currency_exponent: 2,
        account_context_observed_at: "2026-08-09T11:59:30Z",
        account_context_issue: null,
        offer_code: offerCode,
        byer_tag: "MV",
      },
      goal: {
        objective: "OUTCOME_SALES",
        optimization_goal: "OFFSITE_CONVERSIONS",
        custom_event_type: "PURCHASE",
        destination_link: "https://trk.example/click",
        cta: "PLAY_GAME",
        text_optimizations: "OPT_OUT",
        start_date: "2026-08-10",
        budget_level: "campaign",
        daily_budget: "100.00",
        bid_amount: "5.00",
        bid_strategy: "COST_CAP",
        countries: ["US"],
        age_min: 21,
        age_max: 65,
        advantage_audience: true,
        click_through_days: 1,
        view_through_days: 1,
        ad_text_mode: "none",
        ad_text_primary: "",
      },
      structure: { campaigns: [] },
      creatives: { upload_id: null, concepts: [], copies_per_concept: null },
    },
  };
}

function Probe() {
  const draft = useCampaignDraftSync();
  const offerCode = useWizardStore((state) => state.identity.offer_code);
  const syncState = useWizardStore((state) => state.draftSyncState);
  return (
    <div>
      <span>{offerCode}</span>
      <span>{syncState}</span>
      <button type="button" onClick={() => void draft.reloadServerDraft()}>
        reload
      </button>
    </div>
  );
}

describe("server campaign draft sync", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.document = draftDocument(5, "SERVER_A");
    api.refetch.mockResolvedValue({ data: { draft: api.document } });
    api.save.mockResolvedValue(draftDocument(6, "LOCAL_B"));
    api.remove.mockResolvedValue(undefined);
    useWizardStore.getState().reset();
    useWizardStore.setState({ draftHydrated: false, draftSyncState: "loading" });
    window.localStorage.removeItem("fb-agent-campaign-draft");
  });

  it("hydrates after reload and persists only through revision CAS", async () => {
    render(<Probe />);
    await act(async () => {});
    expect(screen.getByText("SERVER_A")).toBeVisible();

    act(() => useWizardStore.getState().setIdentity({ offer_code: "LOCAL_B" }));
    await waitFor(() => expect(api.save).toHaveBeenCalledOnce(), {
      timeout: 2_000,
    });
    expect(api.save).toHaveBeenCalledWith(expect.objectContaining({ expected_revision: 5 }));
    expect(useWizardStore.getState().draftRevision).toBe(6);
    expect(window.localStorage.getItem("fb-agent-campaign-draft")).toBeNull();
  });

  it("stops on CAS conflict and reloads the authoritative server version", async () => {
    const user = userEvent.setup();
    api.save.mockRejectedValue(new GeneratedApiError(409, { message: "conflict" }));
    render(<Probe />);
    await act(async () => {});

    act(() => useWizardStore.getState().setIdentity({ offer_code: "LOCAL_B" }));
    await waitFor(
      () => expect(useWizardStore.getState().draftSyncState).toBe("conflict"),
      { timeout: 2_000 },
    );

    api.document = draftDocument(7, "SERVER_C");
    api.refetch.mockResolvedValue({ data: { draft: api.document } });
    await user.click(screen.getByRole("button", { name: "reload" }));
    expect(screen.getByText("SERVER_C")).toBeVisible();
    expect(useWizardStore.getState().draftRevision).toBe(7);
  });
});
