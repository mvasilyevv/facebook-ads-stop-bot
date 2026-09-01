import { createFileRoute } from "@tanstack/react-router";

import { MiniDecisionsPage } from "@/features/decisions/DecisionsFeed";

// Файловый роут: TanStack Router автоматически code-splitting'ит его в
// отдельный production-чанк (autoCodeSplitting в vite.config.ts), поэтому
// лента «Решения» не ложится в начальный чанк дашборда (`/`), несмотря на
// то, что стала главной вкладкой (issue #338, PR4). Аналог — /incidents и
// /actions, которые уже сегодня сплитятся тем же механизмом.
export const Route = createFileRoute("/decisions/")({
  component: MiniDecisionsPage,
});
