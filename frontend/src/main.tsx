import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { RouterProvider, createRouter } from "@tanstack/react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

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

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 10_000, refetchOnWindowFocus: false } },
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
