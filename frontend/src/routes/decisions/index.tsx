/**
 * Маршрут `/decisions` (issue #338) — единственное место с primary-
 * действиями ленты «Решения». Судьба соседних экранов не меняется:
 * `/incidents` остаётся журналом-архивом, `/actions` — логом команд.
 */
import { createFileRoute } from "@tanstack/react-router";

import { snapshotForRealtimeState } from "@fb/shared/operator/viewModel";
import { useOperatorRealtimeStatus } from "@fb/operator-api";

import { OperatorListSkeleton, OperatorUnavailableState } from "@/components/layout/OperatorPageBoundary";
import { DecisionsFeed } from "@/features/decisions/DecisionsFeed";
import { operatorProblemMessage, useOperatorSnapshot } from "@/lib/api/operator";

export const Route = createFileRoute("/decisions/")({
  component: DecisionsPage,
});

function DecisionsPage() {
  const realtimeStatus = useOperatorRealtimeStatus();
  const snapshotQuery = useOperatorSnapshot({ window: "today" });

  if (snapshotQuery.isLoading && !snapshotQuery.data) {
    return (
      <div className="mx-auto max-w-6xl">
        <OperatorListSkeleton label="Загрузка решений" />
      </div>
    );
  }

  if (snapshotQuery.isError || !snapshotQuery.data) {
    return (
      <div className="mx-auto max-w-6xl">
        <OperatorUnavailableState
          title="Лента решений недоступна"
          resource="ленту решений"
          details={operatorProblemMessage(snapshotQuery.error)}
          onRetry={() => void snapshotQuery.refetch()}
        />
      </div>
    );
  }

  // Кэшированный HTTP-снимок не считается текущим, пока realtime-канал не
  // сверился (тот же приём, что и на дашборде, `OperatorDashboard.tsx`):
  // ready-секции понижаются до stale, пока `realtimeStatus !== "connected"`.
  const snapshot = snapshotForRealtimeState(snapshotQuery.data, realtimeStatus === "connected");
  return <DecisionsFeed snapshot={snapshot} realtimeConnected={realtimeStatus === "connected"} />;
}
