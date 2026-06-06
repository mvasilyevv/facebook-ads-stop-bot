import { createFileRoute } from "@tanstack/react-router";

// Заглушка Dashboard mini. Phase 4B заменит на реальный экран.
export const Route = createFileRoute("/")({
  component: DashboardPlaceholder,
});

function DashboardPlaceholder() {
  return (
    <div className="p-4">
      <h1 className="font-display text-title-3 text-bg-11">Dashboard</h1>
      <p className="mt-2 text-bg-9">Пересборка Mini App в процессе.</p>
    </div>
  );
}
