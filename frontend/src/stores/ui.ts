import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";

/**
 * UI-store: cross-page UI state, persistent в localStorage.
 *
 * Что хранится:
 * - sidebarCollapsed — режим сайдбара (icon-only 64px vs default 240px).
 * - density — высота строк таблиц. Канон design_handoff: три уровня
 *   comfortable (44px) | compact (34px) | dense (28px), которые в tokens.css
 *   маппятся через атрибут [data-density] на --row-h / --row-fs / --row-px.
 *
 * Что НЕ хранится: server data (TanStack Query), form state (react-hook-form),
 * URL state (filters/sort — TanStack Router search params).
 */

export type Density = "comfortable" | "compact" | "dense";

/** Порядок переключения по кругу для toggleDensity. */
const DENSITY_CYCLE: Density[] = ["comfortable", "compact", "dense"];

interface UiState {
  sidebarCollapsed: boolean;
  density: Density;
  displayTimeZone: "auto" | string;
  setSidebarCollapsed: (collapsed: boolean) => void;
  toggleSidebar: () => void;
  setDensity: (density: Density) => void;
  /** Переключает плотность по кругу comfortable → compact → dense → comfortable. */
  toggleDensity: () => void;
  setDisplayTimeZone: (timeZone: "auto" | string) => void;
}

/**
 * Высота строки таблицы в пикселях по текущей density (канон 44/34/28).
 * Совпадает с --row-h из tokens.css [data-density]. Используется generic
 * DataTable (он индексируется числом, а не CSS-переменной).
 */
export const DENSITY_ROW_HEIGHT: Record<Density, number> = {
  comfortable: 44,
  compact: 34,
  dense: 28,
};

export const useUiStore = create<UiState>()(
  persist(
    (set, get) => ({
      sidebarCollapsed: false,
      density: "comfortable",
      displayTimeZone: "auto",
      setSidebarCollapsed: (collapsed) => set({ sidebarCollapsed: collapsed }),
      toggleSidebar: () => set({ sidebarCollapsed: !get().sidebarCollapsed }),
      setDensity: (density) => {
        set({ density });
        applyDensity(density);
      },
      toggleDensity: () => {
        const idx = DENSITY_CYCLE.indexOf(get().density);
        const next = DENSITY_CYCLE[(idx + 1) % DENSITY_CYCLE.length]!;
        set({ density: next });
        applyDensity(next);
      },
      setDisplayTimeZone: (displayTimeZone) => set({ displayTimeZone }),
    }),
    {
      name: "fb-ui",
      storage: createJSONStorage(() => localStorage),
      onRehydrateStorage: () => (state) => {
        // Старое сохранённое значение могло быть вне нового union — нормализуем.
        const d = state?.density;
        const safe: Density = d && DENSITY_CYCLE.includes(d) ? d : "comfortable";
        if (state) state.density = safe;
        applyDensity(safe);
      },
    },
  ),
);

/**
 * Применяет density: ставит атрибут data-density на <html> (tokens.css
 * читает [data-density] → --row-h/--row-fs/--row-px) и дублирует
 * --table-row-height числом для generic DataTable.
 */
function applyDensity(density: Density): void {
  if (typeof document === "undefined") return;
  const root = document.documentElement;
  root.setAttribute("data-density", density);
  root.style.setProperty("--table-row-height", `${DENSITY_ROW_HEIGHT[density]}px`);
}
