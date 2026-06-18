import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { RouterProvider, createRouter } from "@tanstack/react-router";
import { QueryClient, QueryClientProvider, MutationCache } from "@tanstack/react-query";

import { useAuthStore } from "@/stores/auth";
import { toast } from "@/components/ui/Toast";

import "@fontsource/jetbrains-mono/400.css";
import "@fontsource/jetbrains-mono/500.css";
import "@fontsource/jetbrains-mono/600.css";
import "@fontsource/inter-tight/400.css";
import "@fontsource/inter-tight/500.css";
import "@fontsource/inter-tight/600.css";
import "./styles/app.css";

import { routeTree } from "./routeTree.gen";

const router = createRouter({ routeTree, defaultPreload: "intent" });

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}

// H5: bootstrap X-API-Key из VITE_API_KEY (проброс из API_KEY бэка). Без этого apiKey=null
// → 401 на ВСЕХ write-мутациях при REQUIRE_API_KEY=true (весь money-контроль из UI мёртв).
// Не затираем ключ, введённый вручную в Settings (persist в localStorage) — только дефолт.
const envApiKey = import.meta.env.VITE_API_KEY;
if (envApiKey && !useAuthStore.getState().apiKey) {
  useAuthStore.getState().setApiKey(envApiKey);
}

// Расширяем meta-тип мутаций: suppressGlobalError отключает глобальный toast там,
// где компонент уже показывает свою ошибку (settings-вкладки) — против двойного тоста.
declare module "@tanstack/react-query" {
  interface Register {
    mutationMeta: { suppressGlobalError?: boolean };
  }
}

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 10_000, refetchOnWindowFocus: false } },
  // L1/M3: глобальный обработчик ошибок мутаций. Раньше money-пути (disable/delete/
  // draft-confirm) падали МОЛЧА — оператор не узнавал о провале стопа. Теперь любая
  // упавшая мутация без локального хендлера показывает toast.error.
  mutationCache: new MutationCache({
    onError: (error, _vars, _ctx, mutation) => {
      if (mutation.options.meta?.suppressGlobalError) return;
      const msg = error instanceof Error ? error.message : String(error);
      toast.error("Ошибка операции", msg);
    },
  }),
});

const rootEl = document.getElementById("root");
if (!rootEl) throw new Error("#root не найден");

createRoot(rootEl).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </StrictMode>,
);
