import { createFileRoute } from "@tanstack/react-router";

// Заглушка Dashboard. Phase 4A заменит на реальный экран.
export const Route = createFileRoute("/")({
  component: DashboardPlaceholder,
});

function DashboardPlaceholder() {
  return (
    <div className="p-8">
      <h1 className="font-display text-title-2 text-bg-11">Dashboard</h1>
      <p className="mt-2 text-bg-9">Пересборка UI в процессе.</p>
    </div>
  );
}
