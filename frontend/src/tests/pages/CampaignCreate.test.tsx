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

import { render, screen, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState, type ReactElement } from "react";

// ─── Моки ─────────────────────────────────────────────────────────────────────

// Мок TanStack Router (роут createFileRoute)
vi.mock("@tanstack/react-router", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@tanstack/react-router")>();
  return {
    ...actual,
    createFileRoute: () => ({ component: (c: unknown) => c }),
    Link: ({ children, to }: { children: React.ReactNode; to: string }) => (
      <a href={to}>{children}</a>
    ),
    useRouterState: () => ({ location: { pathname: "/campaigns/create" } }),
  };
});

// Мок API campaigns
vi.mock("@/lib/api/campaigns", () => ({
  usePresets: () => ({
    data: [
      {
        id: "preset-1",
        name: "Test Preset",
        act_id: "act_123",
        page_id: "p456",
        pixel_id: "px789",
        tz_offset: 3,
        offer_code: "GH_CR2",
        byer_tag: "MV",
        objective: "OUTCOME_SALES",
        optimization_goal: "OFFSITE_CONVERSIONS",
        custom_event_type: "PURCHASE",
        special_ad_categories: ["NONE"],
        cta: "PLAY_GAME",
        text_optimizations: "OPT_OUT",
        click_through_days: 1,
        view_through_days: 1,
        url_tags_template: null,
        naming_template: null,
        extra: {},
        created_at: "2026-06-01T00:00:00Z",
        updated_at: "2026-06-01T00:00:00Z",
      },
    ],
    isLoading: false,
    isError: false,
  }),
  useValidateConfig: () => ({
    mutate: vi.fn(),
    mutateAsync: vi.fn().mockResolvedValue({
      offer_code: "GH_CR2",
      launch_state: "campaign_paused",
      copies_per_concept: 3,
      campaign_count: 1,
      adset_count: 3,
      ad_count: 6,
      campaigns: [
        {
          key: "image1",
          name: "MV | GH_CR2 | Static | adset.pro | 2026-06-23",
          status: "PAUSED",
          adsets: [{ name: "adset-1", status: "ACTIVE", ad_count: 2 }],
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
    }),
    isPending: false,
    isError: false,
    error: null,
  }),
  useRuns: () => ({
    data: {
      data: [
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
      total: 2,
    },
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  }),
  useRunDetail: () => ({
    data: null,
    isLoading: false,
  }),
  useCloneRun: () => ({
    mutateAsync: vi.fn().mockResolvedValue({ run_id: "run-clone", task_id: null, status: "queued", idempotency_key: "" }),
    isPending: false,
  }),
  useCancelRun: () => ({
    mutateAsync: vi.fn().mockResolvedValue({ id: "run-1", status: "cancelled" }),
    isPending: false,
  }),
  useCleanupRun: () => ({
    mutateAsync: vi.fn().mockResolvedValue({ run_id: "run-2", meta_ids: {}, detail: "Нет объектов" }),
    mutate: vi.fn(),
    isPending: false,
    data: null,
  }),
  uploadConcepts: vi.fn().mockResolvedValue({
    upload_id: "abc123",
    upload_dir: "/tmp/abc123",
    concepts: [
      { ref: "test.jpg", original_name: "test.jpg", size_bytes: 1024, content_type: "image/jpeg" },
    ],
    total_bytes: 1024,
  }),
  useAdAccountTimezone: () => ({
    mutate: vi.fn(),
    mutateAsync: vi.fn().mockResolvedValue({
      tz_offset_hours: 0,
      tz_offset_str: "+00:00",
      timezone_name: "Etc/UTC",
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
  RUN_STATUS_LABELS: {
    queued: "В очереди",
    uniquifying: "Уникализация",
    uploading: "Загрузка",
    creating: "Создание",
    succeeded: "Готово",
    failed: "Ошибка",
    cancelled: "Отменено",
  },
  CANCELLABLE_RUN_STATUSES: ["queued", "uniquifying", "uploading"],
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

// Мок stores/auth
vi.mock("@/stores/auth", () => ({
  useAuthStore: { getState: () => ({ apiKey: null }) },
}));

// ─── Импорты после моков ───────────────────────────────────────────────────────

import { WizardStep1Start } from "@/components/domain/campaigns/WizardStep1Start";
import { WizardStep2Identity, validateIdentity } from "@/components/domain/campaigns/WizardStep2Identity";
import { validateGoal } from "@/components/domain/campaigns/WizardStep3Goal";
import { WizardStep4Structure, validateStructure } from "@/components/domain/campaigns/WizardStep4Structure";
import { WizardStep5Creatives, validateCreatives } from "@/components/domain/campaigns/WizardStep5Creatives";
import { CampaignRunsHistory } from "@/components/domain/campaigns/CampaignRunsHistory";
import { useWizardStore } from "@/stores/campaignWizard";
import type { WizardIdentity, WizardGoal, WizardCreatives } from "@/stores/campaignWizard";
import type { PresetOut } from "@/lib/api/campaigns";

// ─── Хелперы ──────────────────────────────────────────────────────────────────

function makeQC() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

function wrap(ui: ReactElement) {
  return <QueryClientProvider client={makeQC()}>{ui}</QueryClientProvider>;
}

const DEFAULT_IDENTITY: WizardIdentity = {
  act_id: "act_123",
  page_id: "456",
  pixel_id: "789",
  tz_offset: 0,
  timezone_name: "",
  offer_code: "GH_CR2",
  byer_tag: "MV",
};

const DEFAULT_GOAL: WizardGoal = {
  objective: "OUTCOME_SALES",
  optimization_goal: "OFFSITE_CONVERSIONS",
  custom_event_type: "PURCHASE",
  destination_link: "https://tracker.example.com",
  cta: "PLAY_GAME",
  text_optimizations: "OPT_OUT",
  // Дата старта — всегда в будущем (валидация отклоняет прошлое), вычисляем динамически.
  start_date: new Date(Date.now() + 7 * 86_400_000).toISOString().slice(0, 10),
  budget_level: "campaign",
  daily_budget_cents: 20000,
  bid_amount_cents: 500, // $5 целевой CPA (обязателен)
  bid_strategy: "COST_CAP",
  countries: ["US", "BR"],
  age_min: 21,
  age_max: 65,
  advantage_audience: true,
  click_through_days: 1,
  view_through_days: 1,
  ad_text_mode: "none",
  ad_text_primary: "",
};

// ─── ШАГ 1: WizardStep1Start ──────────────────────────────────────────────────

describe("WizardStep1Start", () => {
  // Рендерит три варианта: новый, пресет, клон
  it("рендерит 3 карточки-опции", () => {
    render(
      wrap(
        <WizardStep1Start mode="new" onChange={vi.fn()} />,
      ),
    );
    expect(screen.getByText("Новый залив")).toBeInTheDocument();
    expect(screen.getByText("Из пресета")).toBeInTheDocument();
    expect(screen.getByText("Клон запуска")).toBeInTheDocument();
  });

  // Клик на "Из пресета" вызывает onChange с mode=preset
  it("клик 'Из пресета' → onChange({mode:'preset'})", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(wrap(<WizardStep1Start mode="new" onChange={onChange} />));
    await user.click(screen.getByText("Из пресета"));
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ mode: "preset" }),
    );
  });

  // При mode=preset отображается список пресетов
  it("mode=preset показывает select с пресетом", () => {
    render(wrap(<WizardStep1Start mode="preset" onChange={vi.fn()} />));
    expect(screen.getByText("Test Preset (GH_CR2)")).toBeInTheDocument();
  });

  // При mode=clone отображается input для run_id
  it("mode=clone показывает поле Run ID", () => {
    render(wrap(<WizardStep1Start mode="clone" onChange={vi.fn()} />));
    expect(screen.getByPlaceholderText(/UUID запуска/)).toBeInTheDocument();
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
      act_id: "", page_id: "", pixel_id: "", tz_offset: 0, timezone_name: "",
      offer_code: "", byer_tag: "",
    };
    const errs = validateIdentity(empty);
    expect(errs.act_id).toBeTruthy();
    expect(errs.page_id).toBeTruthy();
    expect(errs.pixel_id).toBeTruthy();
    expect(errs.offer_code).toBeTruthy();
  });

  // Заполненные обязательные поля + подтверждённая TZ → нет ошибок
  it("заполненные поля → нет ошибок", () => {
    const errs = validateIdentity({ ...DEFAULT_IDENTITY, timezone_name: "Europe/Moscow" });
    expect(Object.keys(errs)).toHaveLength(0);
  });

  // Деньги: без подтверждённой TZ (timezone_name пусто) — ошибка, дальше не пускаем.
  it("без подтверждённой TZ → ошибка tz_offset", () => {
    const errs = validateIdentity({ ...DEFAULT_IDENTITY, timezone_name: "" });
    expect(errs.tz_offset).toBeTruthy();
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
    render(
      wrap(
        <WizardStep2Identity
          values={DEFAULT_IDENTITY}
          onChange={vi.fn()}
        />,
      ),
    );
    expect(screen.getByDisplayValue("act_123")).toBeInTheDocument();
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

  // После blur по Ad Account ID страницы подтянулись → page_id рендерится дропдаупом
  // с опцией "{name} — {id}" (а не свободным Input).
  it("страницы подтянулись → рендерится Select с опцией страницы", async () => {
    const user = userEvent.setup();
    render(
      wrap(
        <WizardStep2Identity values={DEFAULT_IDENTITY} onChange={vi.fn()} />,
      ),
    );
    // blur по Ad Account ID запускает фетч страниц (мок зовёт onSuccess синхронно).
    const actInput = screen.getByDisplayValue("act_123");
    await user.click(actInput);
    await user.tab();
    expect(screen.getByRole("option", { name: "Acme Page — 111" })).toBeInTheDocument();
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
    expect(lastIdentity.pixel_id).toBe("px555");
    // countries (ISO-2 upper) ушли в goal.
    expect(onGoalChange).toHaveBeenCalledWith(
      expect.objectContaining({ countries: ["BR", "DE"] }),
    );
  });
});

// ─── ШАГ 3: validateGoal ─────────────────────────────────────────────────────

describe("validateGoal", () => {
  // Пустая destination_link → ошибка
  it("пустой destination_link → ошибка", () => {
    const errs = validateGoal({ ...DEFAULT_GOAL, destination_link: "" });
    expect(errs.destination_link).toBeTruthy();
  });

  // Слишком маленький бюджет → ошибка
  it("бюджет < $1 → ошибка", () => {
    const errs = validateGoal({ ...DEFAULT_GOAL, daily_budget_cents: 50 });
    expect(errs.daily_budget_cents).toBeTruthy();
  });

  // Слишком большой бюджет → ошибка (hard-cap $100k)
  it("бюджет > $100k → ошибка", () => {
    const errs = validateGoal({ ...DEFAULT_GOAL, daily_budget_cents: 10_001_000 });
    expect(errs.daily_budget_cents).toBeTruthy();
  });

  // Нет стран → ошибка
  it("пустые countries → ошибка", () => {
    const errs = validateGoal({ ...DEFAULT_GOAL, countries: [] });
    expect(errs.countries).toBeTruthy();
  });

  // Целевой CPA не задан (0) → ошибка (COST_CAP требует bid_amount)
  it("bid_amount_cents = 0 → ошибка целевого CPA", () => {
    const errs = validateGoal({ ...DEFAULT_GOAL, bid_amount_cents: 0 });
    expect(errs.bid_amount_cents).toBeTruthy();
  });

  // Корректные данные → нет ошибок
  it("корректные данные → нет ошибок", () => {
    const errs = validateGoal(DEFAULT_GOAL);
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
    const err = validateStructure([
      { key: "image1", adset_count: 0, concept_refs: [] },
    ]);
    expect(err).toBeTruthy();
  });

  // Корректная структура → null
  it("корректная структура → null", () => {
    expect(
      validateStructure([
        { key: "image1", adset_count: 3, concept_refs: [] },
      ]),
    ).toBeNull();
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
    expect(onChange).toHaveBeenCalledWith([
      expect.objectContaining({ adset_count: 3 }),
    ]);
  });

  // Кнопка удалить уменьшает список
  it("кнопка «удалить» убирает кампанию", async () => {
    const user = userEvent.setup();
    const campaigns = [{ key: "image1", adset_count: 3, concept_refs: [] }];
    const onChange = vi.fn();
    render(wrap(<WizardStep4Structure campaigns={campaigns} onChange={onChange} />));
    await user.click(screen.getByRole("button", { name: /Удалить кампанию/ }));
    expect(onChange).toHaveBeenCalledWith([]);
  });

  // Итого adset'ов отображается
  it("итого adset'ов суммируется корректно", () => {
    const campaigns = [
      { key: "image1", adset_count: 3, concept_refs: [] },
      { key: "video1", adset_count: 5, concept_refs: [] },
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
      concepts: [{ ref: "a.jpg", original_name: "a.jpg", size_bytes: 100, content_type: "image/jpeg", campaign_keys: [] }],
      copies_per_concept: null,
    };
    expect(validateCreatives(c)).toBeTruthy();
  });

  // Всё заполнено → null
  it("концепты с upload_id → null", () => {
    const c: WizardCreatives = {
      upload_id: "abc",
      concepts: [{ ref: "a.jpg", original_name: "a.jpg", size_bytes: 100, content_type: "image/jpeg", campaign_keys: [] }],
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
      wrap(
        <WizardStep5Creatives
          values={emptyCreatives}
          campaigns={[]}
          onChange={vi.fn()}
        />,
      ),
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

  // Кнопка удалить концепт вызывает onChange без него
  it("удаление концепта вызывает onChange с пустым списком", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const c: WizardCreatives = {
      upload_id: "abc",
      concepts: [
        { ref: "photo.jpg", original_name: "photo.jpg", size_bytes: 1024, content_type: "image/jpeg", campaign_keys: [] },
      ],
      copies_per_concept: null,
    };
    render(wrap(<WizardStep5Creatives values={c} campaigns={[]} onChange={onChange} />));
    await user.click(screen.getByRole("button", { name: /Удалить photo.jpg/ }));
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ concepts: [] }));
  });

  // ── Колонки-кампании + распределение (новый формат) ──
  const twoCampaigns = [
    { key: "c1", adset_count: 2, concept_refs: [] },
    { key: "c2", adset_count: 2, concept_refs: [] },
  ];
  function oneConcept(keys: string[]): WizardCreatives {
    return {
      upload_id: "abc",
      concepts: [
        { ref: "a.jpg", original_name: "a.jpg", size_bytes: 100, content_type: "image/jpeg", campaign_keys: keys },
      ],
      copies_per_concept: null,
    };
  }
  function twoConcepts(k1: string[], k2: string[]): WizardCreatives {
    return {
      upload_id: "abc",
      concepts: [
        { ref: "a.jpg", original_name: "a.jpg", size_bytes: 100, content_type: "image/jpeg", campaign_keys: k1 },
        { ref: "b.jpg", original_name: "b.jpg", size_bytes: 100, content_type: "image/jpeg", campaign_keys: k2 },
      ],
      copies_per_concept: null,
    };
  }

  // Концепт, привязанный к обеим кампаниям, виден в каждой колонке
  it("концепт в обеих кампаниях виден в обеих колонках", () => {
    render(wrap(<WizardStep5Creatives values={oneConcept(["c1", "c2"])} campaigns={twoCampaigns} onChange={vi.fn()} />));
    expect(screen.getAllByText("a.jpg")).toHaveLength(2);
  });

  // ✕ убирает концепт из одной кампании — остаётся в другой
  it("убрать из одной кампании оставляет концепт в другой", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(wrap(<WizardStep5Creatives values={oneConcept(["c1", "c2"])} campaigns={twoCampaigns} onChange={onChange} />));
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
    render(wrap(<WizardStep5Creatives values={oneConcept(["c1"])} campaigns={twoCampaigns} onChange={onChange} />));
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
    render(wrap(<WizardStep5Creatives values={oneConcept([])} campaigns={twoCampaigns} onChange={onChange} />));
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
    render(wrap(<WizardStep5Creatives values={twoConcepts(["c1", "c2"], ["c1", "c2"])} campaigns={twoCampaigns} onChange={onChange} />));
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
    render(wrap(<WizardStep5Creatives values={twoConcepts(["c1"], [])} campaigns={twoCampaigns} onChange={onChange} />));
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
    render(wrap(<WizardStep5Creatives values={twoConcepts(["c1", "c2"], ["c1"])} campaigns={twoCampaigns} onChange={onChange} />));
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

// ─── История запусков ─────────────────────────────────────────────────────────

describe("CampaignRunsHistory", () => {
  // Рендерит список запусков из mock
  it("отображает запуски из истории", () => {
    render(wrap(<CampaignRunsHistory />));
    expect(screen.getByText("GH_CR2")).toBeInTheDocument();
    expect(screen.getByText("DRC_CR")).toBeInTheDocument();
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
    expect(screen.getByText("Meta API timeout")).toBeInTheDocument();
  });

  // Кнопки действий (копировать) присутствуют
  it("кнопки клона/отмены видны", () => {
    render(wrap(<CampaignRunsHistory />));
    const cloneButtons = screen.getAllByTitle("Клонировать");
    expect(cloneButtons.length).toBeGreaterThan(0);
  });
});

// ─── Wizard Store ─────────────────────────────────────────────────────────────

describe("useWizardStore", () => {
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

  // applyPreset заполняет identity из пресета
  it("applyPreset заполняет identity из пресета", () => {
    const preset: PresetOut = {
      id: "p1",
      name: "Test",
      act_id: "act_999",
      page_id: "pg1",
      pixel_id: "px1",
      tz_offset: 2,
      offer_code: "TEST_OFF",
      byer_tag: "AB",
      objective: "OUTCOME_SALES",
      optimization_goal: "OFFSITE_CONVERSIONS",
      custom_event_type: "PURCHASE",
      special_ad_categories: ["NONE"],
      cta: "PLAY_GAME",
      text_optimizations: "OPT_OUT",
      click_through_days: 1,
      view_through_days: 1,
      url_tags_template: "sub2={{ad.id}}",
      naming_template: null,
      extra: {},
      created_at: "2026-06-01T00:00:00Z",
      updated_at: "2026-06-01T00:00:00Z",
    };
    act(() => useWizardStore.getState().applyPreset(preset));
    const { identity } = useWizardStore.getState();
    expect(identity.act_id).toBe("act_999");
    expect(identity.offer_code).toBe("TEST_OFF");
    // url_tags убран из WizardGoal (вычисляется бэком по SOP, не редактируется пользователем)
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
        campaigns: [{ key: "image1", adset_count: 3, concept_refs: [] }],
      });
      store.setCreatives({ upload_id: "up123", concepts: [], copies_per_concept: null });
      store.setPreview({ launch_state: "campaign_paused", plan: null });
    });
    const config = useWizardStore.getState().buildConfig();
    expect(config.act_id).toBe("act_123");
    expect(config.offer_code).toBe("GH_CR2");
    expect(config.launch_state).toBe("campaign_paused");
    expect(config.campaigns).toHaveLength(1);
    expect(config.creo_root).toBe("up123");
    expect(config.daily_budget_cents).toBe(20000);
    // Целевой CPA (bid_amount) доходит до config для builder (COST_CAP)
    expect(config.bid_amount_cents).toBe(500);
    expect(config.bid_strategy).toBe("COST_CAP");
  });

  // launch_state default = campaign_paused
  it("дефолт launch_state = campaign_paused", () => {
    const config = useWizardStore.getState().buildConfig();
    expect(config.launch_state).toBe("campaign_paused");
  });
});

// ─── API helpers: RUN_STATUS_LABELS ──────────────────────────────────────────

describe("RUN_STATUS_LABELS", () => {
  it("все статусы имеют русские лейблы", async () => {
    const { RUN_STATUS_LABELS } = await import("@/lib/api/campaigns");
    const statuses = ["queued", "uniquifying", "uploading", "creating", "succeeded", "failed", "cancelled"];
    for (const s of statuses) {
      expect(RUN_STATUS_LABELS[s as keyof typeof RUN_STATUS_LABELS]).toBeTruthy();
    }
  });
});
