import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";

/**
 * Auth-store: хранит API-ключ для X-API-Key header'а.
 * В первой итерации — только ключ. Полноценный OAuth/JWT — отдельная история.
 */

interface AuthState {
  apiKey: string | null;
  setApiKey: (key: string | null) => void;
  clear: () => void;
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      apiKey: null,
      setApiKey: (key) => set({ apiKey: key }),
      clear: () => set({ apiKey: null }),
    }),
    {
      name: "fb-auth",
      storage: createJSONStorage(() => localStorage),
    },
  ),
);
