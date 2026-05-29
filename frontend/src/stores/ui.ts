import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";

/**
 * UI-store: cross-page UI state, persistent в localStorage.
 *
 * Что хранится:
 * - sidebarCollapsed — режим сайдбара (icon-only 64px vs default 240px).
 * - density — высота строк таблиц ("comfortable" 32px | "compact" 24px).
 * - lastVisitedRoute — для редиректов после login (опционально).
 *
 * Что НЕ хранится: server data (TanStack Query), form state (react-hook-form),
 * URL state (filters/sort — TanStack Router search params).
 */

export type Density = "comfortable" | "compact";

interface UiState {
  sidebarCollapsed: boolean;
  density: Density;
  setSidebarCollapsed: (collapsed: boolean) => void;
  toggleSidebar: () => void;
  setDensity: (density: Density) => void;
  toggleDensity: () => void;
}

/** Высота строки таблицы в пикселях по текущей density. */
export const DENSITY_ROW_HEIGHT: Record<Density, number> = {
  comfortable: 32,
  compact: 24,
};

export const useUiStore = create<UiState>()(
  persist(
    (set, get) => ({
      sidebarCollapsed: false,
      density: "comfortable",
      setSidebarCollapsed: (collapsed) => set({ sidebarCollapsed: collapsed }),
      toggleSidebar: () => set({ sidebarCollapsed: !get().sidebarCollapsed }),
      setDensity: (density) => {
        set({ density });
        applyDensityCssVar(density);
      },
      toggleDensity: () => {
        const next: Density = get().density === "comfortable" ? "compact" : "comfortable";
        set({ density: next });
        applyDensityCssVar(next);
      },
    }),
    {
      name: "fb-ui",
      storage: createJSONStorage(() => localStorage),
      onRehydrateStorage: () => (state) => {
        if (state) applyDensityCssVar(state.density);
      },
    },
  ),
);

/** Прокидываем выбор density в CSS-переменную для таблиц. */
function applyDensityCssVar(density: Density): void {
  if (typeof document === "undefined") return;
  document.documentElement.style.setProperty(
    "--table-row-height",
    `${DENSITY_ROW_HEIGHT[density]}px`,
  );
}
