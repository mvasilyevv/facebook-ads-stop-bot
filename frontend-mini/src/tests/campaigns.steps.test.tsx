/**
 * Тесты компонентов шагов визарда создания кампаний.
 * Валидация, навигация, передача данных в store.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { useWizardStore } from "@/routes/campaigns/-wizardStore";
import { TestProviders } from "./campaigns.steps.test.helper";

// ─── Моки ─────────────────────────────────────────────────────────────────

vi.mock("@tanstack/react-router", () => ({
  createFileRoute: () => ({ component: (c: unknown) => c }),
  useNavigate: () => vi.fn(),
}));

vi.mock("@/lib/tg", () => ({
  haptic: { impact: vi.fn(), notify: vi.fn(), selection: vi.fn() },
  tgConfirm: vi.fn().mockResolvedValue(true),
}));

vi.mock("@/components/layout/MiniHeader", () => ({
  MiniHeader: ({ title }: { title: string }) => <header><span>{title}</span></header>,
}));

const mockUseCampaignPresets = vi.fn();
const mockUseUploadConcepts = vi.fn();
const mockUseValidateCampaign = vi.fn();
const mockUseLaunchCampaign = vi.fn();
const mockUseCampaignRun = vi.fn();
const mockUseAdAccountTimezone = vi.fn();
const mockUseAdAccountPages = vi.fn();
const mockUseOffers = vi.fn();

vi.mock("@/lib/api", () => ({
  useCampaignPresets: () => mockUseCampaignPresets(),
  useUploadConcepts: () => mockUseUploadConcepts(),
  useValidateCampaign: () => mockUseValidateCampaign(),
  useLaunchCampaign: () => mockUseLaunchCampaign(),
  useCampaignRun: () => mockUseCampaignRun(),
  useAdAccountTimezone: () => mockUseAdAccountTimezone(),
  useAdAccountPages: () => mockUseAdAccountPages(),
  useOffers: () => mockUseOffers(),
  useCloneRun: () => ({ mutate: vi.fn() }),
  useCancelRun: () => ({ mutate: vi.fn() }),
  useCleanupRun: () => ({ mutate: vi.fn() }),
  useCampaignRuns: () => ({ data: [], isLoading: false, isError: false, refetch: vi.fn() }),
  TERMINAL_STATUSES: new Set(["succeeded", "failed", "cancelled"]),
  RUN_STATUS_LABEL: {
    queued: "В очереди",
    uniquifying: "Уникализация",
    uploading: "Загрузка",
    creating: "Создание",
    succeeded: "Готово",
    failed: "Ошибка",
    cancelled: "Отменён",
  },
  WIZARD_STEPS: ["start", "identity", "config", "structure", "creatives", "preview", "launch"],
  WIZARD_STEP_LABEL: {
    start: "Старт", identity: "Идентичность", config: "Параметры",
    structure: "Структура", creatives: "Креативы", preview: "Превью", launch: "Запуск",
  },
}));

// ─── Импорт компонентов (после мока api) ──────────────────────────────────

import { StepIdentity } from "@/routes/campaigns/StepIdentity";
import { StepConfig } from "@/routes/campaigns/StepConfig";
import { StepStructure } from "@/routes/campaigns/StepStructure";
import { StepCreatives } from "@/routes/campaigns/StepCreatives";
import { StepStart } from "@/routes/campaigns/StepStart";

// ─── StepIdentity ──────────────────────────────────────────────────────────

// Дефолтные ответы хуков TZ/страниц/офферов (idle — фетч ещё не запускался).
const idleTz = { data: undefined, isError: false, isFetching: false } as const;
// По умолчанию страницы не подтянуты → page_id вводится вручную (ручной Input).
const emptyPages = { data: { pages: [] }, isError: false, isFetching: false } as const;
const emptyOffers = { data: [], isLoading: false, isError: false } as const;
// TZ успешно подтянута — гард шага пропускает дальше.
const okTz = {
  data: { tz_offset_hours: 3, tz_offset_str: "+03:00", timezone_name: "Europe/Moscow" },
  isError: false,
  isFetching: false,
} as const;

describe("StepIdentity — валидация", () => {
  beforeEach(() => {
    useWizardStore.getState().reset();
    useWizardStore.getState().setStep("identity");
    mockUseAdAccountTimezone.mockReturnValue(idleTz);
    mockUseAdAccountPages.mockReturnValue(emptyPages);
    mockUseOffers.mockReturnValue(emptyOffers);
  });

  function renderIdentity() {
    return render(<TestProviders><StepIdentity /></TestProviders>);
  }

  it("рендерится без краша", () => {
    renderIdentity();
    expect(screen.getByLabelText(/ID рекламного кабинета/i)).toBeTruthy();
  });

  it("показывает ошибку при пустом act_id", () => {
    renderIdentity();
    fireEvent.click(screen.getByRole("button", { name: /далее/i }));
    expect(screen.getByText(/Укажите ID рекламного кабинета/i)).toBeTruthy();
  });

  it("переходит на следующий шаг после заполнения всех полей", async () => {
    mockUseAdAccountTimezone.mockReturnValue(okTz); // TZ подтверждена → гард пропускает
    renderIdentity();
    fireEvent.change(screen.getByLabelText(/ID рекламного кабинета/i), {
      target: { value: "act_111" },
    });
    fireEvent.change(screen.getByLabelText(/ID страницы/i), {
      target: { value: "page_222" },
    });
    fireEvent.change(screen.getByLabelText(/ID пикселя/i), {
      target: { value: "pixel_333" },
    });
    fireEvent.change(screen.getByLabelText(/Код оффера/i), {
      target: { value: "GH_AVI" },
    });

    fireEvent.click(screen.getByRole("button", { name: /далее/i }));

    await waitFor(() => {
      expect(useWizardStore.getState().step).toBe("config");
    });
  });

  // Деньги: без подтверждённой TZ (idleTz) гард не пускает дальше.
  it("не пускает дальше без подтверждённой таймзоны", () => {
    renderIdentity(); // idleTz из beforeEach → TZ не подтянута
    fireEvent.change(screen.getByLabelText(/ID рекламного кабинета/i), { target: { value: "act_1" } });
    fireEvent.change(screen.getByLabelText(/ID страницы/i), { target: { value: "2" } });
    fireEvent.change(screen.getByLabelText(/ID пикселя/i), { target: { value: "3" } });
    fireEvent.change(screen.getByLabelText(/^Код оффера$/i), { target: { value: "GH_X" } });
    fireEvent.click(screen.getByRole("button", { name: /далее/i }));
    expect(screen.getByText(/Дождитесь подтягивания/i)).toBeTruthy();
    expect(useWizardStore.getState().step).toBe("identity");
  });

  it("сохраняет offer_code в uppercase в store", async () => {
    mockUseAdAccountTimezone.mockReturnValue(okTz); // TZ подтверждена → гард пропускает
    renderIdentity();
    fireEvent.change(screen.getByLabelText(/ID рекламного кабинета/i), {
      target: { value: "act_111" },
    });
    fireEvent.change(screen.getByLabelText(/ID страницы/i), {
      target: { value: "222" },
    });
    fireEvent.change(screen.getByLabelText(/ID пикселя/i), {
      target: { value: "333" },
    });
    fireEvent.change(screen.getByLabelText(/Код оффера/i), {
      target: { value: "gh_avi" },
    });

    fireEvent.click(screen.getByRole("button", { name: /далее/i }));

    await waitFor(() => {
      expect(useWizardStore.getState().config.offer_code).toBe("GH_AVI");
    });
  });

  // Успешный фетч TZ → tz_offset (число, м.б. отрицательное) и имя в store
  it("успешный фетч TZ записывает отрицательный tz_offset и имя в store", async () => {
    mockUseAdAccountTimezone.mockReturnValue({
      data: { tz_offset_hours: -7, tz_offset_str: "-07:00", timezone_name: "America/Hermosillo" },
      isError: false,
      isFetching: false,
    });
    renderIdentity();
    await waitFor(() => {
      expect(useWizardStore.getState().config.tz_offset).toBe(-7);
      expect(useWizardStore.getState().config.timezone_name).toBe("America/Hermosillo");
    });
  });

  // Read-only показ таймзоны формата «UTC±HH:00 · name»
  it("показывает таймзону в формате UTC-07:00 · name (read-only)", async () => {
    mockUseAdAccountTimezone.mockReturnValue({
      data: { tz_offset_hours: -7, tz_offset_str: "-07:00", timezone_name: "America/Hermosillo" },
      isError: false,
      isFetching: false,
    });
    renderIdentity();
    await waitFor(() => {
      expect(screen.getByText(/UTC-07:00 · America\/Hermosillo/)).toBeTruthy();
    });
  });

  // Спиннер во время фетча
  it("во время фетча TZ показывает спиннер", () => {
    mockUseAdAccountTimezone.mockReturnValue({ data: undefined, isError: false, isFetching: true });
    renderIdentity();
    expect(screen.getByLabelText(/Загрузка таймзоны/i)).toBeTruthy();
  });

  // Ошибка фетча → ручной фолбэк с полным диапазоном UTC (−12..+14)
  it("при ошибке фетча TZ показывает ручной Select с полным диапазоном UTC", async () => {
    mockUseAdAccountTimezone.mockReturnValue({ data: undefined, isError: true, isFetching: false });
    renderIdentity();
    const select = (await screen.findByLabelText(/Таймзона кабинета \(ручной выбор\)/i)) as HTMLSelectElement;
    // −12..+14 = 27 опций
    expect(select.querySelectorAll("option").length).toBe(27);
    fireEvent.change(select, { target: { value: "-7" } });
    await waitFor(() => {
      expect(useWizardStore.getState().config.tz_offset).toBe(-7);
    });
  });

  // offer_code — комбобокс с подсказками из активных офферов (datalist)
  it("offer_code комбобокс предлагает коды активных офферов", () => {
    mockUseOffers.mockReturnValue({
      data: [
        { id: "1", code: "GH_AVI", name: "Aviator", is_active: true },
        { id: "2", code: "GH_CR2", name: "Crash", is_active: true },
      ],
      isLoading: false,
      isError: false,
    });
    renderIdentity();
    const datalist = document.getElementById("offer-code-list") as HTMLDataListElement;
    const values = Array.from(datalist.querySelectorAll("option")).map((o) => o.value);
    expect(values).toEqual(["GH_AVI", "GH_CR2"]);
  });

  // Свободный ввод в комбобокс uppercase'ится
  it("свободный ввод в offer_code uppercase'ится", async () => {
    mockUseAdAccountTimezone.mockReturnValue(okTz); // TZ подтверждена → гард пропускает
    renderIdentity();
    fireEvent.change(screen.getByLabelText(/ID рекламного кабинета/i), { target: { value: "act_1" } });
    fireEvent.change(screen.getByLabelText(/ID страницы/i), { target: { value: "2" } });
    fireEvent.change(screen.getByLabelText(/ID пикселя/i), { target: { value: "3" } });
    fireEvent.change(screen.getByLabelText(/^Код оффера$/i), { target: { value: "custom_x" } });
    fireEvent.click(screen.getByRole("button", { name: /далее/i }));
    await waitFor(() => {
      expect(useWizardStore.getState().config.offer_code).toBe("CUSTOM_X");
    });
  });

  // Страницы подтянулись → page_id выбирается дропдауном (label '{name} — {id}')
  it("страницы подтянулись → дропдаун с опцией, выбор пишет page_id", async () => {
    mockUseAdAccountTimezone.mockReturnValue(okTz);
    mockUseAdAccountPages.mockReturnValue({
      data: { pages: [{ id: "111", name: "Aviator Page" }, { id: "222", name: "Crash Page" }] },
      isError: false,
      isFetching: false,
    });
    renderIdentity();
    const pageSelect = screen.getByLabelText(/ID страницы Facebook/i) as HTMLSelectElement;
    // Опция-плейсхолдер + 2 страницы
    const labels = Array.from(pageSelect.querySelectorAll("option")).map((o) => o.textContent);
    expect(labels).toContain("Aviator Page — 111");
    expect(labels).toContain("Crash Page — 222");
    fireEvent.change(pageSelect, { target: { value: "222" } });
    // Заполняем остальное и проходим дальше — page_id из дропдауна попадает в store
    fireEvent.change(screen.getByLabelText(/ID рекламного кабинета/i), { target: { value: "act_1" } });
    fireEvent.change(screen.getByLabelText(/ID пикселя/i), { target: { value: "3" } });
    fireEvent.change(screen.getByLabelText(/^Код оффера$/i), { target: { value: "GH_X" } });
    fireEvent.click(screen.getByRole("button", { name: /далее/i }));
    await waitFor(() => {
      expect(useWizardStore.getState().config.page_id).toBe("222");
    });
  });

  // Спиннер при загрузке страниц
  it("во время фетча страниц показывает спиннер", () => {
    mockUseAdAccountPages.mockReturnValue({ data: undefined, isError: false, isFetching: true });
    renderIdentity();
    expect(screen.getByLabelText(/Загрузка страниц/i)).toBeTruthy();
  });
});

// ─── StepConfig ────────────────────────────────────────────────────────────

describe("StepConfig — валидация", () => {
  beforeEach(() => {
    useWizardStore.getState().reset();
    useWizardStore.getState().setStep("config");
  });

  function renderConfig() {
    return render(<TestProviders><StepConfig /></TestProviders>);
  }

  it("рендерится без краша", () => {
    renderConfig();
    expect(screen.getByLabelText(/Ссылка назначения/i)).toBeTruthy();
  });

  it("показывает ошибку при пустом destination_link", () => {
    renderConfig();
    fireEvent.click(screen.getByRole("button", { name: /далее/i }));
    expect(screen.getByText(/Укажите ссылку назначения/i)).toBeTruthy();
  });

  it("ошибка при бюджете > $100 000 (hard-cap)", () => {
    renderConfig();
    fireEvent.change(screen.getByLabelText(/Ссылка назначения/i), {
      target: { value: "https://example.com" },
    });
    fireEvent.change(screen.getByLabelText(/Дневной бюджет/i), {
      target: { value: "200000" },
    });
    fireEvent.click(screen.getByRole("button", { name: /далее/i }));
    expect(screen.getByText(/превышает \$100 000/i)).toBeTruthy();
  });

  it("переходит на structure после валидного конфига", async () => {
    renderConfig();
    fireEvent.change(screen.getByLabelText(/Ссылка назначения/i), {
      target: { value: "https://trk.example.com/click" },
    });
    // Бюджет обязателен (минимум $1) — заполняем
    fireEvent.change(screen.getByLabelText(/Дневной бюджет/i), {
      target: { value: "50" },
    });
    fireEvent.click(screen.getByRole("button", { name: /далее/i }));
    await waitFor(() => {
      expect(useWizardStore.getState().step).toBe("structure");
    });
  });

  it("сохраняет destination_link в store", async () => {
    renderConfig();
    fireEvent.change(screen.getByLabelText(/Ссылка назначения/i), {
      target: { value: "https://trk.example.com" },
    });
    // Бюджет обязателен (минимум $1) — заполняем
    fireEvent.change(screen.getByLabelText(/Дневной бюджет/i), {
      target: { value: "50" },
    });
    fireEvent.click(screen.getByRole("button", { name: /далее/i }));
    await waitFor(() => {
      expect(useWizardStore.getState().config.destination_link).toBe("https://trk.example.com");
    });
  });
});

// ─── StepStructure ─────────────────────────────────────────────────────────

describe("StepStructure — управление кампаниями", () => {
  beforeEach(() => {
    useWizardStore.getState().reset();
    useWizardStore.getState().setStep("structure");
  });

  function renderStructure() {
    return render(<TestProviders><StepStructure /></TestProviders>);
  }

  it("рендерится с одной кампанией по умолчанию", () => {
    renderStructure();
    // Есть хотя бы поле для ключа кампании
    expect(screen.getByDisplayValue("camp_1")).toBeTruthy();
  });

  it("добавляет кампанию по кнопке", () => {
    renderStructure();
    fireEvent.click(screen.getByRole("button", { name: /Добавить кампанию/i }));
    // Появляется второй ключ
    expect(screen.getByDisplayValue("camp_2")).toBeTruthy();
  });

  it("переходит на creatives с валидной структурой", async () => {
    renderStructure();
    fireEvent.click(screen.getByRole("button", { name: /далее/i }));
    await waitFor(() => {
      expect(useWizardStore.getState().step).toBe("creatives");
    });
  });

  it("показывает ошибку при пустом ключе кампании", () => {
    renderStructure();
    // Очищаем поле ключа
    const keyInput = screen.getByDisplayValue("camp_1");
    fireEvent.change(keyInput, { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: /далее/i }));
    expect(screen.getByText(/Ключи не могут быть пустыми/i)).toBeTruthy();
  });

  it("сохраняет campaigns в store", async () => {
    renderStructure();
    fireEvent.click(screen.getByRole("button", { name: /далее/i }));
    await waitFor(() => {
      const cfg = useWizardStore.getState().config;
      expect(cfg.campaigns).toHaveLength(1);
      expect(cfg.campaigns?.[0]?.key).toBe("camp_1");
    });
  });
});

// ─── StepCreatives ─────────────────────────────────────────────────────────

describe("StepCreatives — загрузка файлов", () => {
  beforeEach(() => {
    useWizardStore.getState().reset();
    useWizardStore.getState().setStep("creatives");
    mockUseUploadConcepts.mockReturnValue({
      mutateAsync: vi.fn().mockResolvedValue({
        upload_id: "upload-xyz",
        upload_dir: "/tmp/upload-xyz",
        concepts: [
          {
            ref: "photo1.jpg",
            original_name: "photo1.jpg",
            size_bytes: 204800,
            content_type: "image/jpeg",
          },
        ],
        total_bytes: 204800,
      }),
      isPending: false,
    });
  });

  function renderCreatives() {
    return render(<TestProviders><StepCreatives /></TestProviders>);
  }

  it("рендерится без краша", () => {
    renderCreatives();
    expect(screen.getByText(/Выбрать файлы/i)).toBeTruthy();
  });

  it("кнопка Далее отключена если концептов нет (защита money)", () => {
    renderCreatives();
    const btn = screen.getByRole("button", { name: /далее/i });
    // Кнопка disabled — нельзя нажать без концептов
    expect((btn as HTMLButtonElement).disabled).toBe(true);
  });

  it("после загрузки показывает концепт в списке", async () => {
    renderCreatives();

    // Симулируем выбор файла через input
    const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
    const file = new File(["data"], "photo1.jpg", { type: "image/jpeg" });
    Object.defineProperty(fileInput, "files", { value: [file] });
    fireEvent.change(fileInput);

    await waitFor(() => {
      expect(screen.getByText("photo1.jpg")).toBeTruthy();
    });
  });

  it("upload_id сохраняется в store после загрузки", async () => {
    renderCreatives();

    const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
    const file = new File(["data"], "photo1.jpg", { type: "image/jpeg" });
    Object.defineProperty(fileInput, "files", { value: [file] });
    fireEvent.change(fileInput);

    await waitFor(() => {
      expect(useWizardStore.getState().uploadId).toBe("upload-xyz");
    });
  });
});

// ─── StepStart ─────────────────────────────────────────────────────────────

describe("StepStart — выбор режима", () => {
  beforeEach(() => {
    useWizardStore.getState().reset();
    mockUseCampaignPresets.mockReturnValue({
      data: [
        {
          id: "p1",
          name: "Preset Ghana",
          act_id: "act_111",
          page_id: "111",
          pixel_id: "111",
          tz_offset: 0,
          offer_code: "GH_AVI",
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
          created_at: "2026-06-22T00:00:00Z",
          updated_at: "2026-06-22T00:00:00Z",
        },
      ],
      isLoading: false,
      isError: false,
    });
  });

  function renderStart() {
    return render(<TestProviders><StepStart onCloneRun={vi.fn()} /></TestProviders>);
  }

  it("рендерится без краша", () => {
    renderStart();
    expect(screen.getByText(/Новая кампания/i)).toBeTruthy();
  });

  it("кнопка Новая кампания переходит к identity", async () => {
    renderStart();
    fireEvent.click(screen.getByText(/Новая кампания/i));
    await waitFor(() => {
      expect(useWizardStore.getState().step).toBe("identity");
    });
  });

  it("показывает пресет из списка", () => {
    renderStart();
    expect(screen.getByText("Preset Ghana")).toBeTruthy();
  });

  it("клик по пресету заполняет конфиг и переходит к identity", async () => {
    renderStart();
    fireEvent.click(screen.getByText("Preset Ghana"));
    await waitFor(() => {
      expect(useWizardStore.getState().step).toBe("identity");
      expect(useWizardStore.getState().config.act_id).toBe("act_111");
    });
  });

  it("кнопка Клон из истории вызывает onCloneRun", () => {
    const onCloneRun = vi.fn();
    render(<TestProviders><StepStart onCloneRun={onCloneRun} /></TestProviders>);
    fireEvent.click(screen.getByText(/Клон из истории/i));
    expect(onCloneRun).toHaveBeenCalled();
  });
});
