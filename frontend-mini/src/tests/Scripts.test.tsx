/**
 * Тест ScriptsPage: выбор папки → план кампании.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import type { ScriptFolder, ScriptPlan } from "@/lib/api";

// Мок роутера
vi.mock("@tanstack/react-router", () => ({
  createFileRoute: () => ({ component: (c: unknown) => c }),
  useNavigate: () => vi.fn(),
}));

// Мок TG
vi.mock("@/lib/tg", () => ({
  haptic: { impact: vi.fn(), notify: vi.fn(), selection: vi.fn() },
  tgConfirm: vi.fn().mockResolvedValue(true),
}));

// Мок MiniHeader
vi.mock("@/components/layout/MiniHeader", () => ({
  MiniHeader: ({ eyebrow, title }: { eyebrow?: string; title: string }) => (
    <header>
      {eyebrow ? <span>{eyebrow}</span> : null}
      <span>{title}</span>
    </header>
  ),
}));

const MOCK_FOLDERS: ScriptFolder[] = [
  {
    name: "GH_AVI_2026-06-08",
    path: "/home/user/FB_Agent_Creo/GH_AVI_2026-06-08",
    adset_count: 3,
    creative_count: 9,
    media_type: "VIDEO",
    updated_at: Date.now() / 1000,
    is_valid: true,
    validation_error: "",
  },
  {
    name: "NG_CR2_INVALID",
    path: "/home/user/FB_Agent_Creo/NG_CR2_INVALID",
    adset_count: 0,
    creative_count: 0,
    media_type: "IMAGE",
    updated_at: Date.now() / 1000,
    is_valid: false,
    validation_error: "Нет файлов в папке",
  },
];

const MOCK_PLAN: ScriptPlan = {
  campaign_name: "GH | AVI | MV | 2026-06-08",
  offer_code: "GH_AVI",
  offer_country_name: "Ghana",
  creative_folder_name: "GH_AVI_2026-06-08",
  creative_folder_path: "/home/user/FB_Agent_Creo/GH_AVI_2026-06-08",
  conversion_event: "PURCHASE",
  cabinet_id: "act_12345678",
  sub2: "MV",
  media_type: "VIDEO",
  adset_count: 3,
  ad_count: 9,
  adsets: [],
  location_plan: {},
  manual_guide: [
    {
      title: "Основные параметры",
      items: [
        { label: "Кампания", value: "GH | AVI | MV | 2026-06-08", copyable: true },
        { label: "Событие", value: "PURCHASE", copyable: false },
      ],
    },
  ],
  safety_notes: ["Проверьте пиксель перед заливом"],
};

const mockUseScriptFolders = vi.fn();
const mockUseScriptPlan = vi.fn();

vi.mock("@/lib/api", () => ({
  useScriptFolders: () => mockUseScriptFolders(),
  useScriptPlan: () => mockUseScriptPlan(),
}));

import ScriptsTestWrapper from "./Scripts.test.helper";

describe("ScriptsPage", () => {
  const mutateAsync = vi.fn();

  beforeEach(() => {
    mutateAsync.mockResolvedValue(MOCK_PLAN);
    mockUseScriptFolders.mockReturnValue({
      data: MOCK_FOLDERS,
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    });
    mockUseScriptPlan.mockReturnValue({
      mutateAsync,
      isPending: false,
    });
  });

  // Список папок отображается
  it("показывает список папок с креативами", () => {
    render(<ScriptsTestWrapper />);
    expect(screen.getByText("GH_AVI_2026-06-08")).toBeInTheDocument();
    expect(screen.getByText("NG_CR2_INVALID")).toBeInTheDocument();
  });

  // Невалидная папка помечена
  it("невалидная папка показывает ошибку", () => {
    render(<ScriptsTestWrapper />);
    expect(screen.getByText("Нет файлов в папке")).toBeInTheDocument();
  });

  // Клик по папке открывает форму параметров
  it("клик по папке открывает форму с полями параметров", () => {
    render(<ScriptsTestWrapper />);
    fireEvent.click(screen.getByText("GH_AVI_2026-06-08"));
    expect(screen.getByLabelText(/Код оффера/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/Страна/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/ID рекламного кабинета/i)).toBeInTheDocument();
  });

  // Заполнение формы и отправка вызывает mutateAsync
  it("заполнение формы и нажатие 'Построить план' вызывает mutateAsync", async () => {
    render(<ScriptsTestWrapper />);
    fireEvent.click(screen.getByText("GH_AVI_2026-06-08"));

    fireEvent.change(screen.getByLabelText(/Код оффера/i), { target: { value: "GH_AVI" } });
    fireEvent.change(screen.getByLabelText(/Страна/i), { target: { value: "Ghana" } });
    fireEvent.change(screen.getByLabelText(/ID рекламного кабинета/i), { target: { value: "act_12345678" } });

    fireEvent.click(screen.getByText("Построить план"));

    await waitFor(() => {
      expect(mutateAsync).toHaveBeenCalledWith(
        expect.objectContaining({
          offer_code: "GH_AVI",
          offer_country_name: "Ghana",
          cabinet_id: "act_12345678",
          folder_name: "GH_AVI_2026-06-08",
        }),
      );
    });
  });

  // После получения плана отображается имя кампании
  it("после построения плана показывает имя кампании", async () => {
    render(<ScriptsTestWrapper />);
    fireEvent.click(screen.getByText("GH_AVI_2026-06-08"));

    fireEvent.change(screen.getByLabelText(/Код оффера/i), { target: { value: "GH_AVI" } });
    fireEvent.change(screen.getByLabelText(/Страна/i), { target: { value: "Ghana" } });
    fireEvent.change(screen.getByLabelText(/ID рекламного кабинета/i), { target: { value: "act_12345678" } });
    fireEvent.click(screen.getByText("Построить план"));

    await waitFor(() => {
      // Имя кампании появляется в title sheet и в теле — берём первый элемент
      expect(screen.getAllByText("GH | AVI | MV | 2026-06-08").length).toBeGreaterThan(0);
    });
  });

  // При пустых данных показывается EmptyState
  it("при пустом списке папок показывает EmptyState", () => {
    mockUseScriptFolders.mockReturnValue({ data: [], isLoading: false, isError: false, refetch: vi.fn() });
    render(<ScriptsTestWrapper />);
    expect(screen.getByText("Папок с креативами нет")).toBeInTheDocument();
  });
});
