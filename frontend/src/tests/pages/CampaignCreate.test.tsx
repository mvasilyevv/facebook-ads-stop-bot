/**
 * Тесты визарда создания кампаний.
 *
 * - Шаг 1: карточки выбора, смена режима
 * - Шаг 2: валидация обязательных полей
 * - Шаг 3: валидация бюджета, стран, destination_link
 * - Шаг 4: добавление кампаний, валидация
 * - Шаг 5: drag&drop dropzone виден, кнопка upload
 * - Шаг 6: сухой прогон вызывает /validate
 * - Wizard store: buildConfig, applyPreset, reset
 * - API: uploadConcepts структура, статусы RunStatus
 */

import { render, screen, act, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState, type ReactElement } from "react";

// ─── Моки ─────────────────────────────────────────────────────────────────────

// ?preset=<id> — карточка пресета ведёт на /campaigns/create с этим query
// (#345 QW11). ?tab=history — старый deep-link на вкладку истории, которая
// теперь редиректит на канонический /campaigns (issue-аудит UI). Управляемое
// состояние, чтобы конкретный тест мог задать его.
const createRouteSearch = vi.hoisted(
  () => ({ value: {} as { preset?: string; tab?: "history" } }),
);
const campaignsCreateNavigate = vi.hoisted(() => vi.fn());

// Мок TanStack Router (роут createFileRoute)
vi.mock("@tanstack/react-router", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@tanstack/react-router")>();
  return {
    ...actual,
    createFileRoute:
      () =>
      <T,>(options: T): T =>
        ({ ...options, useSearch: () => createRouteSearch.value }) as T,
    Link: ({
      children,
      to,
      ...rest
    }: { children: React.ReactNode; to: string } & Record<string, unknown>) => (
      <a href={to} {...rest}>
        {children}
      </a>
    ),
    useNavigate: () => campaignsCreateNavigate,
    useRouterState: () => ({ location: { pathname: "/campaigns/create" } }),
  };
});

// Мок API campaigns
vi.mock("@/lib/api/campaigns", () => ({
  useCampaignDraft: () => ({
    data: { draft: null },
    isSuccess: true,
    isPending: false,
    isError: false,
    error: null,
    refetch: vi.fn().mockResolvedValue({ data: { draft: null } }),
  }),
  useSaveCampaignDraft: () => ({
    mutateAsync: vi.fn().mockResolvedValue({
      revision: 1,
      state: {},
      updated_at: "2026-08-09T12:00:00Z",
    }),
    isPending: false,
  }),
  useDeleteCampaignDraft: () => ({
    mutateAsync: vi.fn().mockResolvedValue(undefined),
    isPending: false,
  }),
  usePresets: () => ({
    data: [
      {
        id: "preset-1",
        name: "Test Preset",
        countries: ["GH"],
        age_min: 21,
        age_max: 65,
        genders: [],
        placements: [],
        custom_event_type: "PURCHASE",
        budget_level: "campaign",
        daily_budget: "200.00",
        url_tags_template: null,
        naming_template: null,
        created_at: "2026-06-01T00:00:00Z",
        updated_at: "2026-06-01T00:00:00Z",
      },
    ],
    isPending: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  }),
  useValidateConfig: () => ({
    mutate: vi.fn(),
    mutateAsync: vi.fn().mockResolvedValue({
      offer_code: "GH_CR2",
      creation_policy: "all_paused",
      copies_per_concept: 3,
      campaign_count: 1,
      adset_count: 3,
      ad_count: 6,
      start_date: "2099-07-30",
      start_time: "2099-07-30T00:00:00Z",
      timezone_name: "Etc/UTC",
      currency: "USD",
      account_context_observed_at: "2026-07-29T08:30:00Z",
      campaigns: [
        {
          key: "image1",
          name: "MV | GH_CR2 | Static | adset.pro | 2026-06-23",
          status: "PAUSED",
          adsets: [{ name: "adset-1", status: "PAUSED", ad_count: 2 }],
        },
      ],
    }),
    isPending: false,
    isError: false,
    data: null,
    error: null,
  }),
  useLaunchCampaign: () => ({
    mutate: vi.fn(),
    mutateAsync: vi.fn().mockResolvedValue({
      run_id: "run-abc",
      task_id: 42,
      status: "queued",
      idempotency_key: "campaign:GH_CR2:2026-06-23:abc123",
      draft_cleared: true,
      request_state: "accepted",
      accounts: [
        {
          account_id: "123",
          run_id: "run-abc",
          task_id: 42,
          status: "queued",
          idempotency_key: "campaign:GH_CR2:2026-06-23:abc123",
          replayed: false,
        },
      ],
    }),
    isPending: false,
    isError: false,
    error: null,
  }),
  useRunsHistory: () => ({
    data: {
      pages: [
        {
          runs: [
            {
              id: "run-queued",
              preset_id: null,
              status: "queued",
              offer_code: "GH_PENDING",
              idempotency_key: "campaign:GH_PENDING:2026-06-22:queued",
              error: null,
              created_at: "2026-06-22T10:30:00Z",
              updated_at: "2026-06-22T10:30:00Z",
            },
            {
              id: "run-1",
              preset_id: null,
              status: "succeeded",
              offer_code: "GH_CR2",
              idempotency_key: "campaign:GH_CR2:2026-06-22:deadbeef",
              error: null,
              created_at: "2026-06-22T10:00:00Z",
              updated_at: "2026-06-22T10:15:00Z",
            },
            {
              id: "run-2",
              preset_id: null,
              status: "failed",
              offer_code: "DRC_CR",
              idempotency_key: null,
              error: "Meta API timeout",
              created_at: "2026-06-21T09:00:00Z",
              updated_at: "2026-06-21T09:01:00Z",
            },
          ],
          total: 3,
          offset: 0,
          limit: 50,
        },
      ],
    },
    isLoading: false,
    isError: false,
    error: null,
    hasNextPage: false,
    isFetchingNextPage: false,
    fetchNextPage: vi.fn(),
    refetch: vi.fn(),
  }),
  useRunDetail: () => ({
    data: null,
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  }),
  useRunDetails: () => [],
  useAbortCampaignRun: () => ({
    mutateAsync: vi.fn(),
    isPending: false,
  }),
  useResumeCampaignRun: () => ({
    mutateAsync: vi.fn(),
    isPending: false,
  }),
  uploadConcepts: vi.fn().mockResolvedValue({
    upload_id: "abc123",
    upload_dir: "/tmp/abc123",
    concepts: [
      { ref: "test.jpg", original_name: "test.jpg", size_bytes: 1024, content_type: "image/jpeg" },
    ],
    added_refs: ["test.jpg"],
    total_bytes: 1024,
  }),
  useAdAccountContext: () => ({
    mutate: vi.fn(
      (
        _actId: string,
        opts?: {
          onSuccess?: (data: {
            account_id: string;
            state: "ready";
            timezone_name: string;
            currency: string;
            currency_exponent: number;
            observed_at: string;
            next_start_date: string;
            issue: null;
          }) => void;
        },
      ) => {
        opts?.onSuccess?.({
          account_id: "123",
          state: "ready",
          timezone_name: "Etc/UTC",
          currency: "USD",
          currency_exponent: 2,
          observed_at: "2026-07-29T08:30:00Z",
          next_start_date: "2099-07-30",
          issue: null,
        });
      },
    ),
    mutateAsync: vi.fn().mockResolvedValue({
      account_id: "123",
      state: "ready",
      timezone_name: "Etc/UTC",
      currency: "USD",
      currency_exponent: 2,
      observed_at: "2026-07-29T08:30:00Z",
      next_start_date: "2099-07-30",
      issue: null,
    }),
    isPending: false,
    isError: false,
    error: null,
  }),
  useAdAccountPages: () => ({
    // mutate сразу зовёт onSuccess с непустым списком — имитируем удачный фетч
    // страниц, чтобы тест мог проверить рендер дропдаупа после blur.
    mutate: vi.fn(
      (
        _actId: string,
        opts?: { onSuccess?: (d: { pages: { id: string; name: string }[] }) => void },
      ) => {
        opts?.onSuccess?.({ pages: [{ id: "111", name: "Acme Page" }] });
      },
    ),
    mutateAsync: vi.fn().mockResolvedValue({ pages: [{ id: "111", name: "Acme Page" }] }),
    isPending: false,
    isError: false,
    error: null,
  }),
  useAdAccountPixels: () => ({
    // Тот же контракт, что у страниц: непустой список → дропдаун вместо ввода ID.
    mutate: vi.fn(
      (
        _actId: string,
        opts?: { onSuccess?: (d: { pixels: { id: string; name: string }[] }) => void },
      ) => {
        opts?.onSuccess?.({ pixels: [{ id: "999", name: "Acme Pixel" }] });
      },
    ),
    mutateAsync: vi.fn().mockResolvedValue({ pixels: [{ id: "999", name: "Acme Pixel" }] }),
    isPending: false,
    isError: false,
    error: null,
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

// Мок офферов (комбобокс кода оффера в шаге 2) — иначе useOffers дёрнет реальный
// apiGet и промис повиснет/реджектнется в jsdom (флейки). Один оффер с новыми
// полями (ad_account_ids/pixel_id/countries) для теста дерайва.
vi.mock("@/lib/api/offers", () => ({
  useOffers: () => ({
    data: [
      {
        id: "offer-1",
        code: "GH_AVI",
        name: "Aviator GH",
        vertical: "gambling",
        is_active: true,
        pixel_id: "px555",
        ad_account_ids: ["111222"],
        countries: ["br", "de"],
        cpa_threshold: "5.00",
        currency: "USD",
      },
      {
        id: "offer-2",
        code: "GH_MULTI",
        name: "Multi GH",
        vertical: "gambling",
        is_active: true,
        pixel_id: "px777",
        ad_account_ids: ["111222", "333444"],
        countries: ["gh"],
        cpa_threshold: "4.00",
        currency: "USD",
      },
    ],
    isLoading: false,
    isError: false,
  }),
}));

// Мок toast
vi.mock("@/components/ui/Toast", () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
    info: vi.fn(),
    warning: vi.fn(),
  },
}));

// ─── Импорты после моков ───────────────────────────────────────────────────────

import { WizardStep1Start } from "@/components/domain/campaigns/WizardStep1Start";
import {
  WizardStep2Identity,
  validateIdentity,
} from "@/components/domain/campaigns/WizardStep2Identity";
import { validateGoal } from "@/components/domain/campaigns/WizardStep3Goal";
import {
  WizardStep4Structure,
  validateStructure,
} from "@/components/domain/campaigns/WizardStep4Structure";
import {
  WizardStep5Creatives,
  validateCreatives,
} from "@/components/domain/campaigns/WizardStep5Creatives";
import { WizardStep6Preview } from "@/components/domain/campaigns/WizardStep6Preview";
import { CampaignRunsHistory } from "@/components/domain/campaigns/CampaignRunsHistory";
import { CampaignCreatePage } from "@/routes/campaigns/create";
import { useWizardStore } from "@/stores/campaignWizard";
import type { WizardIdentity, WizardGoal, WizardCreatives } from "@/stores/campaignWizard";
import { uploadConcepts, type CampaignConfig, type PresetOut } from "@/lib/api/campaigns";

// ─── Хелперы ──────────────────────────────────────────────────────────────────

function makeQC() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

function wrap(ui: ReactElement) {
  return <QueryClientProvider client={makeQC()}>{ui}</QueryClientProvider>;
}

const DEFAULT_IDENTITY: WizardIdentity = {
  act_id: "act_123",
  ad_account_ids: ["123"],
  page_id: "456",
  pixel_id: "789",
  pixel_confirmed: false,
  account_context_state: "ready",
  timezone_name: "Etc/UTC",
  currency: "USD",
  currency_exponent: 2,
  account_context_observed_at: "2026-07-29T08:30:00Z",
  account_context_issue: null,
  offer_code: "GH_CR2",
  byer_tag: "MV",
};

const DEFAULT_GOAL: WizardGoal = {
  objective: "OUTCOME_SALES",
  optimization_goal: "OFFSITE_CONVERSIONS",
  custom_event_type: "PURCHASE",
  display_link: "",
  destination_link: "https://tracker.example.com",
  cta: "PLAY_GAME",
  text_optimizations: "OPT_OUT",
  start_date: "2099-07-30",
  budget_level: "campaign",
  daily_budget: "200.00",
  bid_amount: "5.00",
  bid_strategy: "COST_CAP",
  countries: ["US", "BR"],
  age_min: 21,
  age_max: 65,
  advantage_audience: true,
  genders: [],
  placements: [],
  click_through_days: 1,
  view_through_days: 1,
  naming_template: "",
  url_tags_template: "",
  ad_text_mode: "none",
  ad_text_primary: "",
};

// ─── ШАГ 1: WizardStep1Start ──────────────────────────────────────────────────

describe("WizardStep1Start", () => {
  it("рендерит 2 карточки-опции", () => {
    render(wrap(<WizardStep1Start mode="new" onChange={vi.fn()} />));
    expect(screen.getByText("Новый залив")).toBeInTheDocument();
    expect(screen.getByText("Из пресета")).toBeInTheDocument();
  });

  // Клик на "Из пресета" вызывает onChange с mode=preset
  it("клик 'Из пресета' → onChange({mode:'preset'})", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(wrap(<WizardStep1Start mode="new" onChange={onChange} />));
    await user.click(screen.getByText("Из пресета"));
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ mode: "preset" }));
  });

  // При mode=preset отображается список пресетов
  it("mode=preset показывает select с пресетом", () => {
    render(wrap(<WizardStep1Start mode="preset" onChange={vi.fn()} />));
    expect(screen.getByText("Test Preset")).toBeInTheDocument();
  });

  // По умолчанию активна карточка "Новый залив" (aria-pressed=true)
  it("активная карточка имеет aria-pressed=true", () => {
    render(wrap(<WizardStep1Start mode="new" onChange={vi.fn()} />));
    const newCard = screen.getByRole("button", { name: /Новый залив/ });
    expect(newCard).toHaveAttribute("aria-pressed", "true");
  });
});

// ─── ШАГ 2: validateIdentity ─────────────────────────────────────────────────

describe("validateIdentity", () => {
  // Пустые поля дают ошибки для всех обязательных
  it("пустые поля → ошибки для act_id, page_id, pixel_id, offer_code", () => {
    const empty: WizardIdentity = {
      act_id: "",
      ad_account_ids: [],
      page_id: "",
      pixel_id: "",
      pixel_confirmed: false,
      account_context_state: "unavailable",
      timezone_name: "",
      currency: "",
      currency_exponent: null,
      account_context_observed_at: null,
      account_context_issue: null,
      offer_code: "",
      byer_tag: "",
    };
    const errs = validateIdentity(empty);
    expect(errs.act_id).toBeTruthy();
    expect(errs.page_id).toBeTruthy();
    expect(errs.pixel_id).toBeTruthy();
    expect(errs.offer_code).toBeTruthy();
  });

  // Заполненные обязательные поля + fresh durable context → нет ошибок
  it("заполненные поля → нет ошибок", () => {
    const errs = validateIdentity(DEFAULT_IDENTITY);
    expect(Object.keys(errs)).toHaveLength(0);
  });

  it("stale context блокирует переход", () => {
    const errs = validateIdentity({ ...DEFAULT_IDENTITY, account_context_state: "stale" });
    expect(errs.account_context_state).toBeTruthy();
  });

  // byer_tag опционален — без него нет ошибки
  it("пустой byer_tag не даёт ошибку", () => {
    const errs = validateIdentity({ ...DEFAULT_IDENTITY, byer_tag: "" });
    expect(errs.byer_tag).toBeUndefined();
  });
});

// ─── ШАГ 2: WizardStep2Identity рендер ───────────────────────────────────────

describe("WizardStep2Identity render", () => {
  // Поля заполнены из values
  it("отображает переданные значения", () => {
    render(wrap(<WizardStep2Identity values={DEFAULT_IDENTITY} onChange={vi.fn()} />));
    expect(screen.getByText("123")).toBeInTheDocument();
    expect(screen.getByDisplayValue("GH_CR2")).toBeInTheDocument();
  });

  // При ошибке act_id выводится сообщение
  it("отображает ошибку для act_id", () => {
    render(
      wrap(
        <WizardStep2Identity
          values={{ ...DEFAULT_IDENTITY, act_id: "" }}
          onChange={vi.fn()}
          errors={{ act_id: "Обязательное поле" }}
        />,
      ),
    );
    expect(screen.getByRole("alert")).toHaveTextContent("Обязательное поле");
  });

  // После выбора основного кабинета страницы подтянулись → page_id рендерится дропдаупом
  // с опцией "{name} — {id}" (а не свободным Input).
  it("страницы подтянулись → рендерится Select с опцией страницы", async () => {
    render(wrap(<WizardStep2Identity values={DEFAULT_IDENTITY} onChange={vi.fn()} />));
    await waitFor(() =>
      expect(screen.getByRole("option", { name: "Acme Page — 111" })).toBeInTheDocument(),
    );
  });

  // Дерайв: выбор кода оффера, совпавшего с каталогом, подставляет act_id (1 кабинет),
  // pixel_id из оффера (identity) и countries (goal). Stateful-обёртка, чтобы
  // контролируемый Input реально набирал полный код, иначе onChange-spy не держит value.
  it("выбор оффера подставляет act_id/pixel/countries", async () => {
    const user = userEvent.setup();
    const onGoalChange = vi.fn();
    let lastIdentity: Partial<WizardIdentity> = {};

    function Harness() {
      const [identity, setIdentity] = useState<WizardIdentity>({
        ...DEFAULT_IDENTITY,
        act_id: "",
        ad_account_ids: [],
        pixel_id: "",
        offer_code: "",
      });
      lastIdentity = identity;
      return (
        <WizardStep2Identity
          values={identity}
          onChange={(v) => setIdentity((prev) => ({ ...prev, ...v }))}
          onGoalChange={onGoalChange}
        />
      );
    }

    render(wrap(<Harness />));
    const offerInput = screen.getByPlaceholderText("GH_CR2");
    await user.type(offerInput, "GH_AVI");

    // act_id (1 кабинет → авто) + pixel_id из оффера осели в identity.
    expect(lastIdentity.act_id).toBe("111222");
    expect(lastIdentity.ad_account_ids).toEqual(["111222"]);
    expect(lastIdentity.pixel_id).toBe("px555");
    // countries (ISO-2 upper) ушли в goal.
    expect(onGoalChange).toHaveBeenCalledWith(expect.objectContaining({ countries: ["BR", "DE"] }));
  });

  it("выбирает несколько кабинетов только из привязки оффера", async () => {
    const user = userEvent.setup();
    let lastIdentity: WizardIdentity = {
      ...DEFAULT_IDENTITY,
      act_id: "",
      ad_account_ids: [],
      offer_code: "",
    };

    function Harness() {
      const [identity, setIdentity] = useState<WizardIdentity>(lastIdentity);
      lastIdentity = identity;
      return (
        <WizardStep2Identity
          values={identity}
          onChange={(value) => setIdentity((previous) => ({ ...previous, ...value }))}
        />
      );
    }

    render(wrap(<Harness />));
    await user.type(screen.getByPlaceholderText("GH_CR2"), "GH_MULTI");
    await user.click(screen.getByRole("button", { name: "Выбрать все" }));

    expect(lastIdentity.ad_account_ids).toEqual(["111222", "333444"]);
    expect(lastIdentity.act_id).toBe("111222");
    expect(screen.queryByPlaceholderText(/через запятую/i)).toBeNull();
  });
});

// ─── ШАГ 3: validateGoal ─────────────────────────────────────────────────────

describe("validateGoal", () => {
  // Пустая destination_link → ошибка
  it("пустой destination_link → ошибка", () => {
    const errs = validateGoal({ ...DEFAULT_GOAL, destination_link: "" }, 2);
    expect(errs.destination_link).toBeTruthy();
  });

  it("нулевой бюджет → ошибка", () => {
    const errs = validateGoal({ ...DEFAULT_GOAL, daily_budget: "0.00" }, 2);
    expect(errs.daily_budget).toBeTruthy();
  });

  it("бюджет выше hard cap → ошибка", () => {
    const errs = validateGoal({ ...DEFAULT_GOAL, daily_budget: "100000.01" }, 2);
    expect(errs.daily_budget).toBeTruthy();
  });

  // Нет стран → ошибка
  it("пустые countries → ошибка", () => {
    const errs = validateGoal({ ...DEFAULT_GOAL, countries: [] }, 2);
    expect(errs.countries).toBeTruthy();
  });

  it("пустой bid_amount → ошибка целевого CPA", () => {
    const errs = validateGoal({ ...DEFAULT_GOAL, bid_amount: "" }, 2);
    expect(errs.bid_amount).toBeTruthy();
  });

  // Корректные данные → нет ошибок
  it("корректные данные → нет ошибок", () => {
    const errs = validateGoal(DEFAULT_GOAL, 2);
    expect(Object.keys(errs)).toHaveLength(0);
  });
});

// ─── ШАГ 4: validateStructure ────────────────────────────────────────────────

describe("validateStructure", () => {
  // Пустой список → ошибка
  it("пустой список кампаний → ошибка", () => {
    expect(validateStructure([])).toBeTruthy();
  });

  // adset_count < 1 → ошибка
  it("adset_count=0 → ошибка", () => {
    const err = validateStructure([{ key: "image1", adset_count: 0 }]);
    expect(err).toBeTruthy();
  });

  // Корректная структура → null
  it("корректная структура → null", () => {
    expect(validateStructure([{ key: "image1", adset_count: 3 }])).toBeNull();
  });
});

// ─── ШАГ 4: WizardStep4Structure рендер ──────────────────────────────────────

describe("WizardStep4Structure", () => {
  // Пустой список → dropzone-подсказка
  it("пустой список кампаний — показывает подсказку добавить", () => {
    render(wrap(<WizardStep4Structure campaigns={[]} onChange={vi.fn()} />));
    expect(screen.getByText(/Нет кампаний/)).toBeInTheDocument();
  });

  // Кнопка «Кампания» (иконка-плюс) добавляет кампанию
  it("клик «Кампания» вызывает onChange с новой кампанией", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(wrap(<WizardStep4Structure campaigns={[]} onChange={onChange} />));
    await user.click(screen.getByRole("button", { name: "Кампания" }));
    expect(onChange).toHaveBeenCalledWith([expect.objectContaining({ adset_count: 3 })]);
  });

  // Кнопка удалить уменьшает список
  it("кнопка «удалить» убирает кампанию", async () => {
    const user = userEvent.setup();
    const campaigns = [{ key: "image1", adset_count: 3 }];
    const onChange = vi.fn();
    render(wrap(<WizardStep4Structure campaigns={campaigns} onChange={onChange} />));
    await user.click(screen.getByRole("button", { name: /Удалить кампанию/ }));
    expect(onChange).toHaveBeenCalledWith([]);
  });

  // Итого adset'ов отображается
  it("итого adset'ов суммируется корректно", () => {
    const campaigns = [
      { key: "image1", adset_count: 3 },
      { key: "video1", adset_count: 5 },
    ];
    render(wrap(<WizardStep4Structure campaigns={campaigns} onChange={vi.fn()} />));
    expect(screen.getByText(/8/)).toBeInTheDocument(); // 3 + 5
  });
});

// ─── ШАГ 5: validateCreatives ────────────────────────────────────────────────

describe("validateCreatives", () => {
  const emptyCreatives: WizardCreatives = {
    upload_id: null,
    concepts: [],
    copies_per_concept: null,
  };

  // Нет концептов → ошибка
  it("нет концептов → ошибка", () => {
    expect(validateCreatives(emptyCreatives)).toBeTruthy();
  });

  // Концепты есть, но upload_id = null → ошибка
  it("концепты без upload_id → ошибка", () => {
    const c: WizardCreatives = {
      upload_id: null,
      concepts: [
        {
          ref: "a.jpg",
          original_name: "a.jpg",
          size_bytes: 100,
          content_type: "image/jpeg",
          campaign_keys: [],
        },
      ],
      copies_per_concept: null,
    };
    expect(validateCreatives(c)).toBeTruthy();
  });

  // Всё заполнено → null
  it("концепты с upload_id → null", () => {
    const c: WizardCreatives = {
      upload_id: "abc",
      concepts: [
        {
          ref: "a.jpg",
          original_name: "a.jpg",
          size_bytes: 100,
          content_type: "image/jpeg",
          campaign_keys: [],
        },
      ],
      copies_per_concept: null,
    };
    expect(validateCreatives(c)).toBeNull();
  });
});

// ─── ШАГ 5: WizardStep5Creatives dropzone ────────────────────────────────────

describe("WizardStep5Creatives", () => {
  const emptyCreatives: WizardCreatives = {
    upload_id: null,
    concepts: [],
    copies_per_concept: null,
  };

  // Dropzone рендерится с текстом-подсказкой
  it("dropzone содержит подсказку по загрузке", () => {
    render(
      wrap(<WizardStep5Creatives values={emptyCreatives} campaigns={[]} onChange={vi.fn()} />),
    );
    expect(screen.getByText(/Перетащите файлы/)).toBeInTheDocument();
  });

  // Загруженные концепты отображаются
  it("загруженный концепт отображается в списке", () => {
    const c: WizardCreatives = {
      upload_id: "abc",
      concepts: [
        {
          ref: "photo.jpg",
          original_name: "photo.jpg",
          size_bytes: 204800,
          content_type: "image/jpeg",
          campaign_keys: [],
        },
      ],
      copies_per_concept: null,
    };
    render(wrap(<WizardStep5Creatives values={c} campaigns={[]} onChange={vi.fn()} />));
    expect(screen.getByText("photo.jpg")).toBeInTheDocument();
    expect(screen.getByText("200.0 KB")).toBeInTheDocument();
  });

  it("дозагрузка добавляет файл в текущий серверный набор", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    vi.mocked(uploadConcepts).mockClear();
    vi.mocked(uploadConcepts).mockResolvedValueOnce({
      upload_id: "abc123",
      upload_dir: "/tmp/abc123",
      concepts: [
        {
          ref: "old.jpg",
          original_name: "old.jpg",
          size_bytes: 100,
          content_type: "image/jpeg",
        },
        {
          ref: "new.jpg",
          original_name: "new.jpg",
          size_bytes: 3,
          content_type: "image/jpeg",
        },
        {
          ref: "removed.jpg",
          original_name: "removed.jpg",
          size_bytes: 20,
          content_type: "image/jpeg",
        },
      ],
      added_refs: ["new.jpg"],
      total_bytes: 103,
    });
    const existing: WizardCreatives = {
      upload_id: "abc123",
      concepts: [
        {
          ref: "old.jpg",
          original_name: "old.jpg",
          size_bytes: 100,
          content_type: "image/jpeg",
          campaign_keys: ["c1"],
        },
        {
          ref: "stale.jpg",
          original_name: "stale.jpg",
          size_bytes: 50,
          content_type: "image/jpeg",
          campaign_keys: ["c1"],
        },
      ],
      copies_per_concept: null,
    };
    const { container } = render(
      wrap(
        <WizardStep5Creatives
          values={existing}
          campaigns={[{ key: "c1", adset_count: 1 }]}
          onChange={onChange}
        />,
      ),
    );
    const input = container.querySelector<HTMLInputElement>('input[type="file"]');
    expect(input).not.toBeNull();
    const file = new File(["new"], "new.jpg", { type: "image/jpeg" });
    await user.upload(input!, file);

    await waitFor(() => expect(onChange).toHaveBeenCalledTimes(1));
    expect(uploadConcepts).toHaveBeenCalledWith([file], "abc123");
    expect(onChange).toHaveBeenCalledWith({
      upload_id: "abc123",
      concepts: [
        existing.concepts[0],
        expect.objectContaining({ ref: "new.jpg", campaign_keys: ["c1"] }),
      ],
    });
  });

  // Кнопка удалить концепт вызывает onChange без него
  it("удаление концепта вызывает onChange с пустым списком", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const c: WizardCreatives = {
      upload_id: "abc",
      concepts: [
        {
          ref: "photo.jpg",
          original_name: "photo.jpg",
          size_bytes: 1024,
          content_type: "image/jpeg",
          campaign_keys: [],
        },
      ],
      copies_per_concept: null,
    };
    render(wrap(<WizardStep5Creatives values={c} campaigns={[]} onChange={onChange} />));
    await user.click(screen.getByRole("button", { name: /Удалить photo.jpg/ }));
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ concepts: [] }));
  });

  // ── Колонки-кампании + распределение (новый формат) ──
  const twoCampaigns = [
    { key: "c1", adset_count: 2 },
    { key: "c2", adset_count: 2 },
  ];
  function oneConcept(keys: string[]): WizardCreatives {
    return {
      upload_id: "abc",
      concepts: [
        {
          ref: "a.jpg",
          original_name: "a.jpg",
          size_bytes: 100,
          content_type: "image/jpeg",
          campaign_keys: keys,
        },
      ],
      copies_per_concept: null,
    };
  }
  function twoConcepts(k1: string[], k2: string[]): WizardCreatives {
    return {
      upload_id: "abc",
      concepts: [
        {
          ref: "a.jpg",
          original_name: "a.jpg",
          size_bytes: 100,
          content_type: "image/jpeg",
          campaign_keys: k1,
        },
        {
          ref: "b.jpg",
          original_name: "b.jpg",
          size_bytes: 100,
          content_type: "image/jpeg",
          campaign_keys: k2,
        },
      ],
      copies_per_concept: null,
    };
  }

  // Концепт, привязанный к обеим кампаниям, виден в каждой колонке
  it("концепт в обеих кампаниях виден в обеих колонках", () => {
    render(
      wrap(
        <WizardStep5Creatives
          values={oneConcept(["c1", "c2"])}
          campaigns={twoCampaigns}
          onChange={vi.fn()}
        />,
      ),
    );
    expect(screen.getAllByText("a.jpg")).toHaveLength(2);
  });

  // ✕ убирает концепт из одной кампании — остаётся в другой
  it("убрать из одной кампании оставляет концепт в другой", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      wrap(
        <WizardStep5Creatives
          values={oneConcept(["c1", "c2"])}
          campaigns={twoCampaigns}
          onChange={onChange}
        />,
      ),
    );
    await user.click(screen.getByRole("button", { name: "Убрать a.jpg из c1" }));
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({
        concepts: [expect.objectContaining({ ref: "a.jpg", campaign_keys: ["c2"] })],
      }),
    );
  });

  // ✕ из последней кампании отправляет концепт в пул (НЕ удаляет файл)
  it("убрать из последней кампании отправляет концепт в пул", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      wrap(
        <WizardStep5Creatives
          values={oneConcept(["c1"])}
          campaigns={twoCampaigns}
          onChange={onChange}
        />,
      ),
    );
    await user.click(screen.getByRole("button", { name: "Убрать a.jpg из c1" }));
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({
        concepts: [expect.objectContaining({ ref: "a.jpg", campaign_keys: [] })],
      }),
    );
  });

  // Чип кампании в пуле назначает нераспределённый концепт
  it("чип кампании в пуле добавляет концепт в кампанию", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      wrap(
        <WizardStep5Creatives
          values={oneConcept([])}
          campaigns={twoCampaigns}
          onChange={onChange}
        />,
      ),
    );
    await user.click(screen.getByRole("button", { name: "Добавить a.jpg в c2" }));
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({
        concepts: [expect.objectContaining({ ref: "a.jpg", campaign_keys: ["c2"] })],
      }),
    );
  });

  // «Поровну» раскидывает по одной кампании на концепт (round-robin)
  it("«Поровну» распределяет концепты по одной кампании", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      wrap(
        <WizardStep5Creatives
          values={twoConcepts(["c1", "c2"], ["c1", "c2"])}
          campaigns={twoCampaigns}
          onChange={onChange}
        />,
      ),
    );
    await user.click(screen.getByRole("button", { name: "Поровну" }));
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({
        concepts: [
          expect.objectContaining({ ref: "a.jpg", campaign_keys: ["c1"] }),
          expect.objectContaining({ ref: "b.jpg", campaign_keys: ["c2"] }),
        ],
      }),
    );
  });

  // «В каждую» — все концепты во все кампании
  it("«В каждую» назначает все концепты во все кампании", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      wrap(
        <WizardStep5Creatives
          values={twoConcepts(["c1"], [])}
          campaigns={twoCampaigns}
          onChange={onChange}
        />,
      ),
    );
    await user.click(screen.getByRole("button", { name: "В каждую" }));
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({
        concepts: [
          expect.objectContaining({ ref: "a.jpg", campaign_keys: ["c1", "c2"] }),
          expect.objectContaining({ ref: "b.jpg", campaign_keys: ["c1", "c2"] }),
        ],
      }),
    );
  });

  // «Очистить» — все концепты в пул
  it("«Очистить» снимает все назначения", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      wrap(
        <WizardStep5Creatives
          values={twoConcepts(["c1", "c2"], ["c1"])}
          campaigns={twoCampaigns}
          onChange={onChange}
        />,
      ),
    );
    await user.click(screen.getByRole("button", { name: "Очистить" }));
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({
        concepts: [
          expect.objectContaining({ ref: "a.jpg", campaign_keys: [] }),
          expect.objectContaining({ ref: "b.jpg", campaign_keys: [] }),
        ],
      }),
    );
  });
});

describe("WizardStep6Preview", () => {
  it("показывает неизменяемый all-paused контракт без selector", () => {
    const config = {
      campaigns: [{ key: "image1", adset_count: 1, concept_refs: ["a.jpg"] }],
    } as unknown as CampaignConfig;

    render(
      wrap(<WizardStep6Preview config={config} preview={{ plan: null }} onChange={vi.fn()} />),
    );

    expect(screen.getByRole("status")).toHaveTextContent("Всё создаётся на паузе");
    expect(screen.getByText(/создаются выключенными/)).toBeInTheDocument();
    expect(screen.queryByText("Кампания PAUSED, дети активны")).not.toBeInTheDocument();
  });
});

// ─── История запусков ─────────────────────────────────────────────────────────

describe("CampaignRunsHistory", () => {
  // Рендерит список запусков из mock
  it("отображает запуски из истории", () => {
    render(wrap(<CampaignRunsHistory />));
    expect(screen.getByText("GH_CR2")).toBeInTheDocument();
    expect(screen.getByText("DRC_CR")).toBeInTheDocument();
    expect(screen.getByTestId("campaign-runs-desktop-header")).toHaveClass("hidden", "md:grid");
    expect(screen.getAllByTestId("campaign-run-card")[0]).toHaveClass("flex", "md:grid");
  });

  // Статус "Готово" для succeeded — может быть несколько (option + строка)
  it("статус succeeded отображается как 'Готово'", () => {
    render(wrap(<CampaignRunsHistory />));
    const els = screen.getAllByText("Готово");
    expect(els.length).toBeGreaterThanOrEqual(1);
  });

  // Статус "Ошибка" + текст ошибки для failed
  it("статус failed + ошибка отображаются", () => {
    render(wrap(<CampaignRunsHistory />));
    const errEls = screen.getAllByText("Ошибка");
    expect(errEls.length).toBeGreaterThanOrEqual(1);
    expect(
      screen.getByText("Запуск завершился ошибкой. Откройте детали для безопасных действий."),
    ).toBeInTheDocument();
    expect(screen.queryByText("Meta API timeout")).not.toBeInTheDocument();
  });

  it("доступны только допустимые действия запуска", () => {
    render(wrap(<CampaignRunsHistory />));
    expect(screen.queryByRole("button", { name: /отменить запуск/i })).toBeNull();
    expect(screen.queryByText("Отменить до начала создания")).toBeNull();
    expect(screen.queryByTitle("Cleanup Meta-объектов")).not.toBeInTheDocument();
  });
});

describe("CampaignCreatePage responsive policy", () => {
  it("keeps the full creation wizard available on mobile", () => {
    const originalMatchMedia = window.matchMedia;
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      value: vi.fn().mockImplementation((query: string) => ({
        matches: query === "(max-width: 767px)",
        media: query,
        onchange: null,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        addListener: vi.fn(),
        removeListener: vi.fn(),
        dispatchEvent: vi.fn(),
      })),
    });

    try {
      render(wrap(<CampaignCreatePage />));

      expect(screen.getByRole("heading", { name: "Создание кампаний" })).toBeInTheDocument();
      expect(screen.getByRole("tab", { name: "Создать" })).toBeVisible();
      expect(screen.getByRole("heading", { name: "Как начать?" })).toBeVisible();
      expect(screen.queryByText(/доступно на desktop/i)).toBeNull();
    } finally {
      Object.defineProperty(window, "matchMedia", {
        configurable: true,
        value: originalMatchMedia,
      });
    }
  });
});

// #345 QW11 — карточка пресета вела «Применить и создать» на /campaigns/create,
// но без ?preset= визард не знал, какой пресет применить: оператор снова
// выбирал его вручную на шаге 1.
describe("CampaignCreatePage query preset (#345 QW11)", () => {
  afterEach(() => {
    createRouteSearch.value = {};
  });

  it("применяет ?preset= к визарду сразу при открытии", async () => {
    useWizardStore.getState().reset();
    createRouteSearch.value = { preset: "preset-1" };

    render(wrap(<CampaignCreatePage />));

    await waitFor(() => expect(useWizardStore.getState().start.preset_id).toBe("preset-1"));
    expect(useWizardStore.getState().start.mode).toBe("preset");
    expect(useWizardStore.getState().goal.countries).toEqual(["GH"]);
  });

  it("без ?preset= не трогает режим старта", () => {
    useWizardStore.getState().reset();
    createRouteSearch.value = {};

    render(wrap(<CampaignCreatePage />));

    expect(useWizardStore.getState().start.mode).toBe("new");
    expect(useWizardStore.getState().start.preset_id).toBeFalsy();
  });
});

// История заливов смонтирована только на /campaigns (issue-аудит UI): раньше
// тот же CampaignRunsHistory второй раз рендерился на вкладке "История" здесь.
// Старый deep-link ?tab=history не должен ломаться — он уводит на /campaigns.
describe("CampaignCreatePage ?tab=history redirect", () => {
  afterEach(() => {
    createRouteSearch.value = {};
    campaignsCreateNavigate.mockClear();
  });

  it("уводит на канонический /campaigns вместо второй истории здесь", async () => {
    createRouteSearch.value = { tab: "history" };

    render(wrap(<CampaignCreatePage />));

    await waitFor(() =>
      expect(campaignsCreateNavigate).toHaveBeenCalledWith({
        to: "/campaigns",
        replace: true,
      }),
    );
    // Пока редирект не сработал, визард не мигает своей копией истории.
    expect(screen.queryByRole("heading", { name: "Как начать?" })).not.toBeInTheDocument();
  });

  it("без ?tab=history остаётся на визарде и не редиректит", () => {
    createRouteSearch.value = {};

    render(wrap(<CampaignCreatePage />));

    expect(campaignsCreateNavigate).not.toHaveBeenCalled();
    expect(screen.getByRole("tab", { name: "Создать" })).toBeInTheDocument();
  });

  it("ведёт на /campaigns вкладкой «История» вместо переключения панели", () => {
    createRouteSearch.value = {};

    render(wrap(<CampaignCreatePage />));

    const historyLink = screen.getByRole("tab", { name: "История" });
    expect(historyLink.tagName).toBe("A");
    expect(historyLink).toHaveAttribute("href", "/campaigns");
  });
});

// ─── Wizard Store ─────────────────────────────────────────────────────────────

const PRESET_WITH_URL_TAGS: PresetOut = {
  id: "p1",
  name: "Test",
  countries: ["US", "CA"],
  age_min: 25,
  age_max: 55,
  genders: ["female"],
  placements: ["facebook", "instagram"],
  custom_event_type: "PURCHASE",
  budget_level: "campaign",
  daily_budget: "250.00",
  bid_strategy: "COST_CAP",
  bid_amount: "5.00",
  display_link: "",
  url_tags_template: "sub2={{ad.id}}",
  naming_template: "{byer} | {date}",
  created_at: "2026-06-01T00:00:00Z",
  updated_at: "2026-06-01T00:00:00Z",
};

describe("useWizardStore", () => {
  const seedBuildableDraft = () => {
    const store = useWizardStore.getState();
    store.setIdentity(DEFAULT_IDENTITY);
    store.setGoal(DEFAULT_GOAL);
    store.setStructure({ campaigns: [{ key: "c1", adset_count: 1 }] });
    store.setCreatives({
      upload_id: "abc123",
      concepts: [
        {
          ref: "a.jpg",
          original_name: "a.jpg",
          size_bytes: 100,
          content_type: "image/jpeg",
          campaign_keys: ["c1"],
        },
      ],
      copies_per_concept: null,
    });
  };

  beforeEach(() => {
    // Сброс store перед каждым тестом
    useWizardStore.getState().reset();
  });

  // Дефолтный шаг = 1
  it("начальный шаг = 1", () => {
    expect(useWizardStore.getState().currentStep).toBe(1);
  });

  // goNext увеличивает шаг
  it("goNext увеличивает шаг", () => {
    act(() => useWizardStore.getState().goNext());
    expect(useWizardStore.getState().currentStep).toBe(2);
  });

  // goPrev не уходит ниже 1
  it("goPrev не уходит ниже шага 1", () => {
    act(() => useWizardStore.getState().goPrev());
    expect(useWizardStore.getState().currentStep).toBe(1);
  });

  // goNext не уходит выше 7
  it("goNext не уходит выше шага 7", () => {
    const store = useWizardStore.getState();
    act(() => store.goTo(7));
    act(() => store.goNext());
    expect(useWizardStore.getState().currentStep).toBe(7);
  });

  // setIdentity обновляет только указанные поля
  it("setIdentity обновляет offer_code", () => {
    act(() => useWizardStore.getState().setIdentity({ offer_code: "DRC_CR" }));
    expect(useWizardStore.getState().identity.offer_code).toBe("DRC_CR");
  });

  it("помечает локальное представление черновика изменённым без localStorage authority", () => {
    act(() => useWizardStore.getState().setIdentity({ offer_code: "DRC_CR" }));

    expect(useWizardStore.getState().draftVersion).toBe(1);
    expect(window.localStorage.getItem("fb-agent-campaign-draft")).toBeNull();
  });

  it("applyPreset копирует только повторяемые поля и сохраняет identity", () => {
    act(seedBuildableDraft);
    act(() => useWizardStore.getState().applyPreset(PRESET_WITH_URL_TAGS));
    const { identity, goal } = useWizardStore.getState();
    expect(identity).toEqual(DEFAULT_IDENTITY);
    expect(goal.countries).toEqual(["US", "CA"]);
    expect(goal.genders).toEqual(["female"]);
    expect(goal.placements).toEqual(["facebook", "instagram"]);
    expect(goal.daily_budget).toBe("250.00");
    expect(useWizardStore.getState().buildConfig().url_tags).toBe("sub2={{ad.id}}");
  });

  it("после применения позволяет изменить подставленные значения", () => {
    act(seedBuildableDraft);
    act(() => useWizardStore.getState().applyPreset(PRESET_WITH_URL_TAGS));
    act(() =>
      useWizardStore.getState().setGoal({
        countries: ["BR"],
        daily_budget: "300.00",
        url_tags_template: "utm_source=manual",
      }),
    );

    const config = useWizardStore.getState().buildConfig();
    expect(config.countries).toEqual(["BR"]);
    expect(config.daily_budget).toBe("300.00");
    expect(config.url_tags).toBe("utm_source=manual");
  });

  // reset сбрасывает store в initial state
  it("reset сбрасывает store", () => {
    act(() => {
      useWizardStore.getState().setIdentity({ act_id: "act_changed" });
      useWizardStore.getState().goTo(5);
    });
    act(() => useWizardStore.getState().reset());
    const state = useWizardStore.getState();
    expect(state.currentStep).toBe(1);
    expect(state.identity.act_id).toBe("");
  });

  // buildConfig собирает корректный конфиг
  it("buildConfig включает обязательные поля", () => {
    act(() => {
      const store = useWizardStore.getState();
      store.setIdentity(DEFAULT_IDENTITY);
      store.setGoal(DEFAULT_GOAL);
      store.setStructure({
        campaigns: [{ key: "image1", adset_count: 3 }],
      });
      store.setCreatives({
        upload_id: "up123",
        concepts: [
          {
            ref: "a.jpg",
            original_name: "a.jpg",
            size_bytes: 100,
            content_type: "image/jpeg",
            campaign_keys: ["image1"],
          },
        ],
        copies_per_concept: null,
      });
      store.setPreview({ plan: null });
    });
    const config = useWizardStore.getState().buildConfig();
    expect(config.act_id).toBe("123");
    expect(config.offer_code).toBe("GH_CR2");
    expect(config).not.toHaveProperty("launch_state");
    expect(config.campaigns).toHaveLength(1);
    expect(config.creo_root).toBe("up123");
    expect(config.daily_budget).toBe("200.00");
    expect(config.bid_amount).toBe("5.00");
    expect(config).not.toHaveProperty("timezone_name");
    expect(config).not.toHaveProperty("currency");
    expect(config.bid_strategy).toBe("COST_CAP");
  });

  it("не отправляет изменяемый launch_state", () => {
    act(seedBuildableDraft);
    const config = useWizardStore.getState().buildConfig();
    expect(config).not.toHaveProperty("launch_state");
  });

  it("buildConfig fail-closed без upload и распределения", () => {
    expect(() => useWizardStore.getState().buildConfig()).toThrow(
      "Сначала загрузите и распределите креативы",
    );

    act(() => {
      const store = useWizardStore.getState();
      store.setIdentity(DEFAULT_IDENTITY);
      store.setStructure({ campaigns: [{ key: "c1", adset_count: 1 }] });
      store.setCreatives({
        upload_id: "abc123",
        concepts: [],
        copies_per_concept: null,
      });
    });
    expect(() => useWizardStore.getState().buildConfig()).toThrow("не назначен ни один креатив");
  });
});

// ─── API helpers: RUN_STATUS_LABELS ──────────────────────────────────────────

describe("RUN_STATUS_LABELS", () => {
  it("все статусы имеют русские лейблы", async () => {
    const { RUN_STATUS_LABELS } = await import("@/lib/api/campaigns");
    const statuses = [
      "queued",
      "uniquifying",
      "uploading",
      "creating",
      "succeeded",
      "failed",
      "cancelled",
    ];
    for (const s of statuses) {
      expect(RUN_STATUS_LABELS[s as keyof typeof RUN_STATUS_LABELS]).toBeTruthy();
    }
  });
});
