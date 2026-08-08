import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { RouterProvider, createRouter } from "@tanstack/react-router";
import { QueryClient, QueryClientProvider, MutationCache } from "@tanstack/react-query";
import { apiProblemMessage } from "@fb/operator-api";

import { toast } from "@/components/ui/toastStore";
import { shouldRetryApiQuery } from "@/lib/api/client";

import "./styles/fonts.css";
import "./styles/app.css";

import { routeTree } from "./routeTree.gen";

const router = createRouter({ routeTree, defaultPreload: "intent" });

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}

// Расширяем meta-тип мутаций: suppressGlobalError отключает глобальный toast там,
// где компонент уже показывает свою ошибку (settings-вкладки) — против двойного тоста.
declare module "@tanstack/react-query" {
  interface Register {
    mutationMeta: { suppressGlobalError?: boolean };
  }
}

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 10_000,
      refetchOnWindowFocus: false,
      retry: shouldRetryApiQuery,
    },
  },
  // L1/M3: глобальный обработчик ошибок мутаций. Раньше money-пути (disable/delete/
  // draft-confirm) падали МОЛЧА — оператор не узнавал о провале стопа. Теперь любая
  // упавшая мутация без локального хендлера показывает toast.error.
  mutationCache: new MutationCache({
    onError: (error, _vars, _ctx, mutation) => {
      if (mutation.options.meta?.suppressGlobalError) return;
      const msg = apiProblemMessage(error);
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
