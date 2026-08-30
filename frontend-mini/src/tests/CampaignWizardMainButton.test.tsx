/**
 * Визард кампании внутри Telegram: MainButton ведёт «Далее»/«Запустить»,
 * своя закреплённая кнопка прячется, «Назад» остаётся обычной кнопкой.
 */
import { act, render, screen } from "@testing-library/react";
import type { CampaignWizardState } from "@fb/features/campaigns";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  launch: vi.fn(),
  validate: vi.fn(),
  runDetails: {} as Record<string, Record<string, unknown>>,
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

function installTelegramMainButton() {
  const button = {
    isVisible: false,
    show: vi.fn(),
    hide: vi.fn(),
    setText: vi.fn(),
    onClick: vi.fn(),
    offClick: vi.fn(),
    enable: vi.fn(),
    disable: vi.fn(),
    showProgress: vi.fn(),
    hideProgress: vi.fn(),
  };
  (window as typeof window & { Telegram?: unknown }).Telegram = {
    WebApp: { MainButton: button },
  };
  return button;
}

function lastClickHandler(button: ReturnType<typeof installTelegramMainButton>) {
  const calls = button.onClick.mock.calls;
  return calls[calls.length - 1]?.[0] as () => void;
}

describe("CampaignWizard в Telegram: MainButton ведёт визард", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.runDetails = {};
  });

  afterEach(() => {
    delete (window as typeof window & { Telegram?: unknown }).Telegram;
  });

  it("показывает MainButton с текстом «Далее» и прячет собственную кнопку", () => {
    const button = installTelegramMainButton();
    render(<CampaignWizard />);

    expect(button.show).toHaveBeenCalled();
    expect(button.setText).toHaveBeenCalledWith("Далее");
    // Собственная закреплённая кнопка «Далее» больше не дублирует MainButton.
    expect(screen.queryByRole("button", { name: /^Далее$/ })).toBeNull();
    // «Назад» остаётся обычной кнопкой страницы — не управляется MainButton.
    expect(screen.getByRole("button", { name: /Назад/ })).toBeInTheDocument();
  });

  it("клик по нативной кнопке продвигает визард на следующий шаг", () => {
    const button = installTelegramMainButton();
    render(<CampaignWizard />);

    act(() => lastClickHandler(button)());

    expect(
      screen.getByRole("region", { name: /Шаг 2:/ }),
    ).toBeVisible();
  });

  it("на шаге 7 подтверждающая кнопка внутри карточки скрыта в пользу MainButton", async () => {
    api.validate.mockResolvedValue(PLAN);
    const button = installTelegramMainButton();
    render(<CampaignWizard />);

    for (let step = 1; step < 6; step += 1) {
      act(() => lastClickHandler(button)());
    }
    // Шаг 6 авто-запрашивает план (PreviewStep); ждём подтверждения перед
    // переходом на шаг 7, иначе «Далее» блокируется валидацией.
    await screen.findByText("Всё создаётся выключенным");
    act(() => lastClickHandler(button)());

    expect(
      screen.queryByRole("button", { name: /Подтвердить и поставить в очередь/ }),
    ).toBeNull();
    expect(
      screen.getByText(/Подтвердите запуск нижней кнопкой Telegram/i),
    ).toBeInTheDocument();
    expect(button.setText).toHaveBeenLastCalledWith(
      "Подтвердить и поставить в очередь",
    );
    expect(button.enable).toHaveBeenCalled();
  });

  it("cleanup на размонтировании прячет MainButton", () => {
    const button = installTelegramMainButton();
    const { unmount } = render(<CampaignWizard />);
    unmount();
    expect(button.hide).toHaveBeenCalled();
  });
});
