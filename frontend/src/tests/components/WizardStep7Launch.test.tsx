import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  runDetail: null as Record<string, unknown> | null,
}));

vi.mock("@/lib/api/campaigns", () => ({
  useLaunchCampaign: () => ({
    mutate: vi.fn(),
    isPending: false,
    isError: false,
    error: null,
  }),
  useRunDetail: () => ({
    data: mocks.runDetail,
    isLoading: false,
  }),
  RUN_STATUS_LABELS: {
    queued: "В очереди",
    uniquifying: "Уникализация",
    uploading: "Загрузка",
    creating: "Создание",
    succeeded: "Готово",
    failed: "Ошибка",
    cancelled: "Отменено",
  },
  TERMINAL_RUN_STATUSES: ["succeeded", "failed", "cancelled"],
}));

import { WizardStep7Launch } from "@/components/domain/campaigns/WizardStep7Launch";
import type { CampaignConfig } from "@/lib/api/campaigns";

const CONFIG = {
  offer_code: "TEST",
  start_date: "2026-07-14",
  campaigns: [{ key: "camp1", adset_count: 3, concept_refs: ["creative.mp4"] }],
} as CampaignConfig;

const SUCCEEDED_RUN = {
  id: "1a6bb9a5-d83b-4652-b791-428a588a1be0",
  preset_id: null,
  status: "succeeded",
  config: {},
  progress: {
    stage: "succeeded",
    ads: "ad-1,ad-2,ad-3,ad-4,ad-5,ad-6",
  },
  created_meta_ids: {
    campaigns: "campaign-1",
    adsets: "adset-1,adset-2,adset-3",
    ads: "ad-1,ad-2,ad-3,ad-4,ad-5,ad-6",
    creatives: "creative-1,creative-2,creative-3,creative-4,creative-5,creative-6",
  },
  error: null,
  idempotency_key: "campaign:test",
  created_at: "2026-07-13T20:00:00Z",
  updated_at: "2026-07-13T20:01:00Z",
};

describe("WizardStep7Launch — успешный залив", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.runDetail = SUCCEEDED_RUN;
  });

  it("показывает компактный итог, завершённый progress и прячет ID в детали", async () => {
    const user = userEvent.setup();
    const onFinish = vi.fn();
    const { container } = render(
      <WizardStep7Launch
        config={CONFIG}
        runId="1a6bb9a5-d83b-4652-b791-428a588a1be0"
        onRunId={vi.fn()}
        onFinish={onFinish}
      />,
    );

    expect(screen.getByText("Залив завершён")).toBeInTheDocument();
    expect(screen.getByText(/PAUSED · без спенда/i)).toBeInTheDocument();
    const summary = screen.getByRole("heading", { name: "Залив завершён" }).closest("section");
    expect(summary).not.toBeNull();
    expect(within(summary!).getByText("Кампании").parentElement).toHaveTextContent("1");
    expect(within(summary!).getByText("Адсеты").parentElement).toHaveTextContent("3");
    expect(within(summary!).getByText("Объявления").parentElement).toHaveTextContent("6");
    expect(container.querySelectorAll('[data-state="done"]')).toHaveLength(5);

    const details = container.querySelector("details");
    expect(details).not.toHaveAttribute("open");
    expect(screen.getByText("Технические детали")).toBeInTheDocument();

    const adsManagerLink = screen.getByRole("link", { name: "Открыть в Ads Manager" });
    expect(adsManagerLink).toHaveAttribute("href", expect.stringContaining("campaign-1"));

    await user.click(screen.getByRole("button", { name: "Создать новый залив" }));
    expect(onFinish).toHaveBeenCalledOnce();
  });

  it("показывает action-required state и persisted Meta IDs после partial-create", () => {
    mocks.runDetail = {
      ...SUCCEEDED_RUN,
      status: "failed",
      progress: {
        stage: "failed",
        outcome: "UNKNOWN",
        reason: "partial_or_ack_lost",
      },
      created_meta_ids: {
        campaigns: ["101"],
        adsets: ["201", "202"],
        ads: ["301"],
        creatives: [],
      },
      error: "partial_fail: ответ Meta неоднозначен",
    };

    render(
      <WizardStep7Launch
        config={CONFIG}
        runId="1a6bb9a5-d83b-4652-b791-428a588a1be0"
        onRunId={vi.fn()}
        onFinish={vi.fn()}
      />,
    );

    const alert = screen.getByRole("alert", { name: "Требуется ручная сверка" });
    expect(alert).toHaveTextContent("Не повторяйте запуск");
    expect(alert).toHaveTextContent("Кампании · 1");
    expect(alert).toHaveTextContent("101");
    expect(alert).toHaveTextContent("Группы · 2");
    expect(screen.queryByRole("button", { name: /cleanup|удалить/i })).toBeNull();
    expect(screen.getByRole("link", { name: "Открыть Ads Manager" })).toHaveAttribute(
      "href",
      "https://www.facebook.com/adsmanager/manage/campaigns?ids=101",
    );
  });
});
