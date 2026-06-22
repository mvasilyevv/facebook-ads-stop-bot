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

vi.mock("@/lib/api", () => ({
  useCampaignPresets: () => mockUseCampaignPresets(),
  useUploadConcepts: () => mockUseUploadConcepts(),
  useValidateCampaign: () => mockUseValidateCampaign(),
  useLaunchCampaign: () => mockUseLaunchCampaign(),
  useCampaignRun: () => mockUseCampaignRun(),
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

describe("StepIdentity — валидация", () => {
  beforeEach(() => {
    useWizardStore.getState().reset();
    useWizardStore.getState().setStep("identity");
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

  it("сохраняет offer_code в uppercase в store", async () => {
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
