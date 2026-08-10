import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { RouterProvider, createRouter } from "@tanstack/react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import "./styles/fonts.css";
import "./styles/app.css";

import { routeTree } from "./routeTree.gen";

// Mini App смонтирован под /tma/ (Vite base + router basepath).
const router = createRouter({
  routeTree,
  basepath: "/tma",
  defaultPreload: "intent",
});

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 10_000,
      refetchOnWindowFocus: false,
      retry: (failureCount) => failureCount < 2,
    },
  },
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
