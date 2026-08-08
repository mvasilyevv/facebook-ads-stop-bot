import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";

/**
 * UI-store: cross-page UI state, persistent в localStorage.
 *
 * Что хранится:
 * - sidebarCollapsed — режим сайдбара (icon-only 64px vs default 240px).
 * - displayTimeZone — операторская timezone для локального отображения.
 *
 * Что НЕ хранится: server data (TanStack Query), form state (react-hook-form),
 * URL state (filters/sort — TanStack Router search params).
 */

interface UiState {
  sidebarCollapsed: boolean;
  displayTimeZone: "auto" | string;
  setSidebarCollapsed: (collapsed: boolean) => void;
  toggleSidebar: () => void;
  setDisplayTimeZone: (timeZone: "auto" | string) => void;
}

export const useUiStore = create<UiState>()(
  persist(
    (set, get) => ({
      sidebarCollapsed: false,
      displayTimeZone: "auto",
      setSidebarCollapsed: (collapsed) => set({ sidebarCollapsed: collapsed }),
      toggleSidebar: () => set({ sidebarCollapsed: !get().sidebarCollapsed }),
      setDisplayTimeZone: (displayTimeZone) => set({ displayTimeZone }),
    }),
    {
      name: "fb-ui-v2",
      storage: createJSONStorage(() => localStorage),
    },
  ),
);
