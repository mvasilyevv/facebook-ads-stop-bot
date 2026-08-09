import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";

/**
 * UI-store: cross-page UI state, persistent в localStorage.
 *
 * Что хранится:
 * - sidebarCollapsed — режим сайдбара (icon-only 64px vs default 240px).
 *
 * Что НЕ хранится: server data, включая timezone профиля владельца (TanStack Query),
 * form state (react-hook-form),
 * URL state (filters/sort — TanStack Router search params).
 */

interface UiState {
  sidebarCollapsed: boolean;
  setSidebarCollapsed: (collapsed: boolean) => void;
  toggleSidebar: () => void;
}

export const useUiStore = create<UiState>()(
  persist(
    (set, get) => ({
      sidebarCollapsed: false,
      setSidebarCollapsed: (collapsed) => set({ sidebarCollapsed: collapsed }),
      toggleSidebar: () => set({ sidebarCollapsed: !get().sidebarCollapsed }),
    }),
    {
      name: "fb-ui-v2",
      storage: createJSONStorage(() => localStorage),
    },
  ),
);
