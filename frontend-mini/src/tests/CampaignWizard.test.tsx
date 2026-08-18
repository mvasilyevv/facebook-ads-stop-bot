import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { CampaignWizardState } from "@fb/features/campaigns";
import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  launch: vi.fn(),
  validate: vi.fn(),
  runDetails: {} as Record<string, Record<string, unknown>>,
}));

// Identity в моке черновика зашита инлайном; холдер позволяет менять её по тесту.
const identityOverride = vi.hoisted(() => ({
  value: {} as Record<string, unknown>,
}));

vi.mock("@tanstack/react-router", async (importOriginal) => {
  const React = await import("react");
  const actual = await importOriginal<typeof import("@tanstack/react-router")>();
  return {
    ...actual,
    Link: ({ to, children, ...props }: { to: string; children: React.ReactNode }) =>
      React.createElement("a", { href: to, ...props }, children),
  };
});

vi.mock("@/lib/api", () => ({
  useOffers: () => ({ data: [], isPending: false }),
}));

vi.mock("@/lib/campaigns", () => ({
  useCampaignPresets: () => ({
    data: [],
    isPending: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  }),
  useCampaignAccountContext: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useCampaignAccountPages: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useCampaignAccountPixels: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useUploadCampaignConcepts: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useValidateCampaignConfig: () => ({
    mutateAsync: api.validate,
    isPending: false,
  }),
  useLaunchCampaign: () => ({ mutateAsync: api.launch, isPending: false }),
  useCampaignRunDetails: (runIds: string[]) =>
    runIds.map((runId) => ({ data: api.runDetails[runId], isLoading: false })),
}));

vi.mock("@/features/campaigns/useCampaignWizardDraft", async () => {
  const React = await import("react");
  const feature = await import("@fb/features/campaigns");
  return {
    useCampaignWizardDraft: () => {
      const [state, setState] = React.useState<CampaignWizardState>(() => ({
        ...feature.createCampaignWizardState(),
        identity: {
          act_id: "act_123",
          ad_account_ids: ["123"],
          page_id: "page_1",
          pixel_id: "pixel_1",
          account_context_state: "ready" as const,
          timezone_name: "America/New_York",
          currency: "USD" as const,
          currency_exponent: 2 as const,
          account_context_observed_at: "2026-08-09T12:00:00Z",
          account_context_issue: null,
          offer_code: "US_GAME",
          byer_tag: "MV",
          ...identityOverride.value,
        },
        goal: {
          ...feature.createCampaignWizardState().goal,
          destination_link: "https://trk.example/click",
          start_date: "2026-08-10",
          daily_budget: "100.00",
          bid_amount: "5.00",
          countries: ["US"],
        },
        structure: { campaigns: [{ key: "camp1", adset_count: 1 }] },
        creatives: {
          upload_id: "upload_1",
          concepts: [
            {
              ref: "creative.jpg",
              original_name: "creative.jpg",
              size_bytes: 1024,
              content_type: "image/jpeg",
              campaign_keys: ["camp1"],
            },
          ],
          copies_per_concept: 1,
        },
      }));
      const [plan, setPlan] = React.useState<unknown>(null);
      return {
        state,
        plan,
        setPlan,
        dispatch: (action: Parameters<typeof feature.campaignWizardReducer>[1]) => {
          setState((current) => feature.campaignWizardReducer(current, action));
          setPlan(null);
        },
        applyPreset: vi.fn(),
        revision: 3,
        updatedAt: "2026-08-09T12:00:00Z",
        syncState: "saved" as const,
        hydrated: true,
        isHydrating: false,
        isHydrationError: false,
        reload: vi.fn(),
        reset: vi.fn(),
        resetPending: false,
        markCleared: vi.fn(),
      };
    },
  };
});

import { CampaignWizard } from "@/features/campaigns/CampaignWizard";

const PLAN = {
  offer_code: "US_GAME",
  creation_policy: "all_paused" as const,
  copies_per_concept: 1,
  campaign_count: 1,
  adset_count: 1,
  ad_count: 1,
  campaigns: [],
  start_date: "2026-08-10",
  start_time: "00:05",
  timezone_name: "America/New_York",
  currency: "USD",
  account_context_observed_at: "2026-08-09T12:00:00Z",
};

describe("TMA campaign creator", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.runDetails = {};
    identityOverride.value = {};
  });

  it("passes all seven action-first steps and keeps queued distinct from success", async () => {
    const user = userEvent.setup();
    api.validate.mockResolvedValue(PLAN);
    api.launch.mockResolvedValue({
      run_id: "run-1",
      task_id: 1842,
      status: "queued",
      idempotency_key: "campaign:test",
      draft_cleared: true,
      request_state: "accepted",
      accounts: [
        {
          account_id: "123",
          run_id: "run-1",
          task_id: 1842,
          status: "queued",
          idempotency_key: "campaign:test",
          replayed: false,
        },
      ],
    });
    api.runDetails = { "run-1": { status: "queued" } };
    render(<CampaignWizard />);

    for (const expectedStep of [2, 3, 4, 5, 6]) {
      await user.click(screen.getByRole("button", { name: /Далее/ }));
      expect(
        screen.getByRole("region", {
          name: new RegExp(`Шаг ${expectedStep}:`),
        }),
      ).toBeVisible();
    }

    expect(await screen.findByText("ALL PAUSED")).toBeVisible();
    await user.click(screen.getByRole("button", { name: /Подтвердить план/ }));
    expect(screen.getByRole("region", { name: /Шаг 7:/ })).toBeVisible();

    await user.click(
      screen.getByRole("button", { name: /Подтвердить и поставить в очередь/ }),
    );
    expect(await screen.findByText("Запуски выполняются")).toBeVisible();
    expect(screen.getByText(/Зелёный итог появится только/)).toBeVisible();
    expect(api.launch).toHaveBeenCalledWith(
      expect.objectContaining({ draft_revision: 3, ad_account_ids: ["123"] }),
    );
  });

  it("keeps every interactive step and sticky action at least 44px", () => {
    const { container } = render(<CampaignWizard />);
    expect(container.querySelectorAll(".min-h-11").length).toBeGreaterThanOrEqual(7);
    expect(screen.queryByText(/desktop-first|доступно на desktop/i)).toBeNull();
  });

  it("показывает частичный результат отдельно от общего успеха", async () => {
    const user = userEvent.setup();
    api.validate.mockResolvedValue(PLAN);
    api.launch.mockResolvedValue({
      status: "partial",
      draft_cleared: true,
      request_state: "partial",
      accounts: [
        {
          account_id: "123",
          run_id: "run-1",
          task_id: 1842,
          status: "queued",
          idempotency_key: "campaign:123",
          replayed: false,
        },
        {
          account_id: "456",
          status: "rejected",
          error: "Контекст кабинета не подтверждён",
          replayed: false,
        },
      ],
    });
    api.runDetails = { "run-1": { status: "succeeded" } };
    render(<CampaignWizard />);

    for (let step = 2; step <= 6; step += 1) {
      await user.click(screen.getByRole("button", { name: /Далее/ }));
    }
    await user.click(screen.getByRole("button", { name: /Подтвердить план/ }));
    await user.click(
      screen.getByRole("button", { name: /Подтвердить и поставить в очередь/ }),
    );

    expect(await screen.findByText("Частичный результат")).toBeVisible();
    expect(screen.getByText("Контекст кабинета не подтверждён")).toBeVisible();
    expect(screen.queryByText("Все кабинеты подтверждены")).toBeNull();
  });

  // Тот же контракт в mini app: оператор видит причину, а не только факт.
  // Блок доказательств живёт на шаге «Идентичность» — доходим до него.
  it("показывает причину неподтверждённого контекста", async () => {
    const user = userEvent.setup();
    identityOverride.value = {
      account_context_state: "unavailable",
      account_context_issue: "Meta отклонила запрос по кабинету",
    };

    render(<CampaignWizard />);
    await user.click(screen.getByRole("button", { name: /Далее/ }));

    expect(await screen.findByText("Meta отклонила запрос по кабинету")).toBeVisible();
  });
});
