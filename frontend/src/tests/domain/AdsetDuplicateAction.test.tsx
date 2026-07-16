/** End-to-end component contract for the drawer duplication step form. */

import type { AdSnapshot } from "@fb/shared";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  preview: vi.fn(),
  previewReset: vi.fn(),
  draft: vi.fn(),
  draftReset: vi.fn(),
  draftData: null as null | Record<string, unknown>,
  statusData: null as null | Record<string, unknown>,
}));

vi.mock("@/lib/api/adsetDuplicates", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api/adsetDuplicates")>(
    "@/lib/api/adsetDuplicates",
  );
  return {
    ...actual,
    usePreviewAdsetDuplicate: () => ({
      mutateAsync: mocks.preview,
      reset: mocks.previewReset,
      isPending: false,
      error: null,
    }),
    useStartAdsetDuplicate: () => ({
      mutateAsync: mocks.draft,
      reset: mocks.draftReset,
      isPending: false,
      error: null,
      data: mocks.draftData,
    }),
    useAdsetDuplicateStatus: () => ({
      data: mocks.statusData,
      isFetching: false,
      error: null,
    }),
  };
});

import { AdsetDuplicateAction } from "@/components/domain/ads/AdsetDuplicateAction";
import type { AdsetDuplicatePreviewOut } from "@/lib/api/adsetDuplicates";

const SOURCE_AD_ID = "238001";
const SECOND_AD_ID = "238002";
const TIMEZONE = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";

const SOURCE_PREVIEW: AdsetDuplicatePreviewOut = {
  preview_token: "preview-source",
  source: {
    account: { id: "act_42", name: "Main", currency: "USD" },
    campaign: { id: "cmp-1", name: "MV | CR2 | 15.07" },
    adset: { id: "set-1", name: "MV | CR2 | broad" },
    ads: [
      { id: SOURCE_AD_ID, fb_ad_id: SOURCE_AD_ID, name: "Creative 1" },
      { id: SECOND_AD_ID, fb_ad_id: SECOND_AD_ID, name: "Creative 2" },
    ],
  },
  format_code: "3-2-1",
  counts: { campaigns: 3, adsets: 6, ads: 6, total_objects: 15 },
  budget: {
    level: "ABO",
    unit_daily_budget_cents: 10_000,
    total_daily_budget_cents: 60_000,
    currency: "USD",
  },
  schedule: {
    timezone_name: TIMEZONE,
    offset: "+02:00",
    start_time_utc: "2026-07-15T22:00:00Z",
    start_time_local: "2026-07-16T00:00:00+02:00",
  },
  generated_names: {
    campaigns: ["MV | CR2 | copy 1", "MV | CR2 | copy 2", "MV | CR2 | copy 3"],
    adsets: ["MV | CR2 | broad | copy 1"],
  },
  warnings: [],
  expires_at: "2099-07-15T18:00:00Z",
};

const FINAL_PREVIEW: AdsetDuplicatePreviewOut = {
  ...SOURCE_PREVIEW,
  preview_token: "preview-final",
  format_code: "3-2-2",
  counts: { campaigns: 3, adsets: 6, ads: 12, total_objects: 21 },
  budget: {
    ...SOURCE_PREVIEW.budget,
    unit_daily_budget_cents: 5_000,
    total_daily_budget_cents: 30_000,
  },
};

const AD = {
  fb_ad_id: SOURCE_AD_ID,
  internal_id: "uuid-1",
  ad_name: "Creative 1",
  campaign_name: "MV | CR2 | 15.07",
  adset_name: "MV | CR2 | broad",
  alert_state: "normal",
  is_active: true,
  adset_daily_budget: "25000",
} as AdSnapshot;

describe("AdsetDuplicateAction", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.draftData = null;
    mocks.statusData = null;
    mocks.preview.mockImplementation(async (body: { selected_ad_ids: string[] }) =>
      body.selected_ad_ids.length > 1 ? FINAL_PREVIEW : SOURCE_PREVIEW,
    );
    mocks.draft.mockImplementation(async () => {
      const response = {
        task_id: 77,
        status: "pending",
        expires_at: "2026-07-15T18:00:00Z",
      };
      mocks.draftData = response;
      return response;
    });
  });

  it("selects only requested ads, previews 3-2-N totals and launches from web", async () => {
    const user = userEvent.setup();
    render(<AdsetDuplicateAction ad={AD} />);

    await user.click(screen.getByRole("button", { name: "Дублировать структуру объявления" }));
    expect(await screen.findByText("Creative 2")).toBeInTheDocument();
    expect(screen.getByText("3-2-1")).toBeInTheDocument();
    expect(screen.getByLabelText("Кампаний")).toHaveAttribute("max", "5");
    expect(screen.getByLabelText("Адсетов / кампания")).toHaveAttribute("max", "10");
    const initialBudget = screen.getByLabelText(/Дневной бюджет · ABO/);
    expect(initialBudget).toHaveValue("100");
    expect(initialBudget).toHaveAttribute("type", "text");
    expect(initialBudget).toHaveAttribute("inputmode", "decimal");
    expect(screen.getByText(/Итого:/)).toHaveTextContent("600");
    expect(screen.getByLabelText("Дата старта · 00:00 кабинета")).toHaveAttribute("type", "date");
    expect(screen.getByLabelText("Дата старта · 00:00 кабинета")).not.toHaveValue("");
    expect(screen.getByRole("dialog")).toHaveClass("overflow-auto");
    expect(screen.getByRole("dialog")).not.toHaveClass("overflow-hidden");
    expect(document.querySelector("label label")).toBeNull();
    expect(mocks.preview.mock.calls[0]?.[0]).toMatchObject({
      budget_level: "ABO",
      daily_budget_cents: 10_000,
      start_date: null,
    });

    await user.click(screen.getByRole("checkbox", { name: "Выбрать Creative 2" }));
    expect(screen.getByText("3-2-2")).toBeInTheDocument();

    const budget = screen.getByLabelText(/Дневной бюджет · ABO/);
    await user.clear(budget);
    await user.type(budget, "50");
    await user.click(screen.getByRole("button", { name: "Рассчитать дубль" }));

    await waitFor(() => {
      const lastRequest = mocks.preview.mock.calls.at(-1)?.[0] as {
        selected_ad_ids: string[];
        daily_budget_cents: number;
      };
      expect(lastRequest.selected_ad_ids).toEqual([SOURCE_AD_ID, SECOND_AD_ID]);
      expect(lastRequest.daily_budget_cents).toBe(5_000);
    });
    expect(await screen.findByText("Всего объектов")).toBeInTheDocument();
    expect(screen.getByText("21")).toBeInTheDocument();
    expect(screen.getByText("3-2-2")).toBeInTheDocument();
    expect(screen.getByText("16.07.2026, 00:00")).toBeInTheDocument();
    expect(screen.getByText(`${TIMEZONE} · UTC+02:00`)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Запустить дублирование" }));
    await waitFor(() =>
      expect(mocks.draft).toHaveBeenCalledWith({ preview_token: "preview-final" }),
    );
    expect(await screen.findByText("Дублирование запущено")).toBeInTheDocument();
    expect(screen.getByText(/Статус создания обновляется прямо здесь/)).toBeInTheDocument();
    expect(screen.getByText("В очереди")).toBeInTheDocument();
    expect(screen.getByText("#77")).toBeInTheDocument();
  });

  it("defaults to CBO without source adset budget and blocks more than 50 ads", async () => {
    const user = userEvent.setup();
    render(<AdsetDuplicateAction ad={{ ...AD, adset_daily_budget: null } as AdSnapshot} />);

    await user.click(screen.getByRole("button", { name: "Дублировать структуру объявления" }));
    await screen.findByText("Creative 2");
    expect(screen.getByRole("button", { name: /CBO.*бюджет на кампанию/i })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByLabelText(/Дневной бюджет · CBO/)).toHaveValue("100");

    const campaigns = screen.getByLabelText("Кампаний");
    const adsets = screen.getByLabelText("Адсетов / кампания");
    await user.clear(campaigns);
    await user.type(campaigns, "5");
    await user.clear(adsets);
    await user.type(adsets, "10");
    await user.click(screen.getByRole("checkbox", { name: "Выбрать Creative 2" }));

    expect(screen.getByRole("alert")).toHaveTextContent("Получится 100 объявлений — максимум 50");
    expect(screen.getByRole("button", { name: "Рассчитать дубль" })).toBeDisabled();
  });

  it("shows verified future-start semantics after successful polling status", async () => {
    const user = userEvent.setup();
    mocks.statusData = {
      task_id: 77,
      status: "succeeded",
      progress: { completed: 15, total: 15 },
      created_meta_ids: { campaigns: ["cmp-new"] },
      error: null,
    };
    render(<AdsetDuplicateAction ad={AD} />);

    await user.click(screen.getByRole("button", { name: "Дублировать структуру объявления" }));
    await screen.findByText("Creative 2");
    await user.click(screen.getByRole("button", { name: "Рассчитать дубль" }));
    await screen.findByText("Всего объектов");
    await user.click(screen.getByRole("button", { name: "Запустить дублирование" }));

    expect(
      await screen.findByText(
        "Структура создана и проверена. Объекты активированы с будущим временем старта.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveAttribute("aria-live", "polite");
    expect(screen.getByRole("status")).toHaveTextContent("Задача завершена");
  });

  it("exposes polling progress to assistive technology", async () => {
    const user = userEvent.setup();
    mocks.statusData = {
      task_id: 77,
      status: "running",
      progress: { phase: "creating", completed: 4, total: 15, message: "Создаём объявления" },
      created_meta_ids: { campaigns: ["cmp-new"] },
      error: null,
    };
    render(<AdsetDuplicateAction ad={AD} />);

    await user.click(screen.getByRole("button", { name: "Дублировать структуру объявления" }));
    await screen.findByText("Creative 2");
    await user.click(screen.getByRole("button", { name: "Рассчитать дубль" }));
    await screen.findByText("Всего объектов");
    await user.click(screen.getByRole("button", { name: "Запустить дублирование" }));

    expect(screen.getByRole("status")).toHaveTextContent("Создаём объявления");
    const progressbar = screen.getByRole("progressbar", {
      name: "Прогресс создания структуры",
    });
    expect(progressbar).toHaveAttribute("aria-valuemin", "0");
    expect(progressbar).toHaveAttribute("aria-valuemax", "15");
    expect(progressbar).toHaveAttribute("aria-valuenow", "4");
    expect(progressbar).toHaveAttribute("aria-valuetext", "Создаём объявления: 4 из 15");
  });

  it("requires close and reopen with a fresh token after a partial terminal failure", async () => {
    const user = userEvent.setup();
    mocks.statusData = {
      task_id: 77,
      status: "failed",
      progress: { phase: "failed_cleanup", completed: 2, total: 15 },
      created_meta_ids: { campaigns: ["cmp-partial"], adsets: ["set-partial"] },
      error: "Создание остановлено; объекты поставлены на PAUSED",
    };
    render(<AdsetDuplicateAction ad={AD} />);

    await user.click(screen.getByRole("button", { name: "Дублировать структуру объявления" }));
    await screen.findByText("Creative 2");
    const firstToken = (mocks.preview.mock.calls[0]?.[0] as { idempotency_token: string })
      .idempotency_token;
    await user.click(screen.getByRole("button", { name: "Рассчитать дубль" }));
    await screen.findByText("Всего объектов");
    await user.click(screen.getByRole("button", { name: "Запустить дублирование" }));

    expect(await screen.findByText("Частичная структура в Meta")).toBeInTheDocument();
    expect(screen.getByText(/Создано объектов: 2/)).toBeInTheDocument();
    expect(screen.getByText(/cmp-partial.*set-partial/)).toBeInTheDocument();
    expect(screen.getByText(/закройте окно и снова нажмите/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "К расчёту" })).not.toBeInTheDocument();
    expect(screen.queryByText("Preview read-only · Meta не изменена")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Закрыть окно" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Дублировать структуру объявления" }));
    await screen.findByText("Creative 2");
    const reopenedToken = (mocks.preview.mock.calls.at(-1)?.[0] as { idempotency_token: string })
      .idempotency_token;
    expect(reopenedToken).not.toBe(firstToken);
  });

  it("caps source ad selection at ten", async () => {
    const user = userEvent.setup();
    const manyAds = Array.from({ length: 11 }, (_, index) => ({
      id: `2380${String(index + 1).padStart(2, "0")}`,
      fb_ad_id: `2380${String(index + 1).padStart(2, "0")}`,
      name: `Creative ${index + 1}`,
    }));
    manyAds[0] = { id: SOURCE_AD_ID, fb_ad_id: SOURCE_AD_ID, name: "Creative 1" };
    mocks.preview.mockResolvedValue({
      ...SOURCE_PREVIEW,
      source: { ...SOURCE_PREVIEW.source, ads: manyAds },
    });
    render(<AdsetDuplicateAction ad={AD} />);

    await user.click(screen.getByRole("button", { name: "Дублировать структуру объявления" }));
    await screen.findByText("Creative 11");
    for (let index = 2; index <= 10; index += 1) {
      await user.click(screen.getByRole("checkbox", { name: `Выбрать Creative ${index}` }));
    }

    expect(screen.getByRole("checkbox", { name: "Выбрать Creative 11" })).toBeDisabled();
    expect(screen.getByText("10/11")).toBeInTheDocument();
  });

  it("requires a fresh preview after the 15-minute token expires", async () => {
    const user = userEvent.setup();
    mocks.preview.mockResolvedValue({
      ...SOURCE_PREVIEW,
      expires_at: "2020-01-01T00:00:00Z",
    });
    render(<AdsetDuplicateAction ad={AD} />);

    await user.click(screen.getByRole("button", { name: "Дублировать структуру объявления" }));
    await screen.findByText("Creative 2");
    await user.click(screen.getByRole("button", { name: "Рассчитать дубль" }));

    expect(await screen.findByRole("button", { name: "Обновить preview" })).toBeEnabled();
    expect(
      screen.queryByRole("button", { name: "Запустить дублирование" }),
    ).not.toBeInTheDocument();
    expect(mocks.draft).not.toHaveBeenCalled();
  });

  it("caps the preview expiry timer at the browser timeout limit", async () => {
    const timeoutSpy = vi.spyOn(window, "setTimeout");
    const user = userEvent.setup();
    render(<AdsetDuplicateAction ad={AD} />);

    await user.click(screen.getByRole("button", { name: "Дублировать структуру объявления" }));
    await screen.findByText("Creative 2");

    expect(timeoutSpy.mock.calls.some(([, delay]) => Number(delay) === 2_147_483_647)).toBe(true);
    timeoutSpy.mockRestore();
  });

  it("blocks a daily budget above the backend money cap", async () => {
    const user = userEvent.setup();
    render(<AdsetDuplicateAction ad={AD} />);

    await user.click(screen.getByRole("button", { name: "Дублировать структуру объявления" }));
    await screen.findByText("Creative 2");
    const budget = screen.getByLabelText(/Дневной бюджет · ABO/);
    await user.clear(budget);
    await user.type(budget, "100000.01");

    expect(screen.getByRole("alert")).toHaveTextContent("Максимальный дневной бюджет — 100 000.00");
    expect(screen.getByRole("button", { name: "Рассчитать дубль" })).toBeDisabled();
  });

  it("offers quick budget presets and recalculates the total before preview", async () => {
    const user = userEvent.setup();
    render(<AdsetDuplicateAction ad={AD} />);

    await user.click(screen.getByRole("button", { name: "Дублировать структуру объявления" }));
    await screen.findByText("Creative 2");

    const preset = screen.getByRole("button", {
      name: "Установить дневной бюджет 200 USD",
    });
    await user.click(preset);

    expect(screen.getByLabelText(/Дневной бюджет · ABO/)).toHaveValue("200");
    expect(preset).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText(/Итого:/)).toHaveTextContent("1 200");
  });
});
