import { createFileRoute } from "@tanstack/react-router";

import { PullToRefresh } from "@/components/layout/PullToRefresh";
import { OperatorMiniDashboard } from "@/features/operator/OperatorMiniDashboard";
import { useOperatorSnapshot } from "@/lib/operatorApi";

export const Route = createFileRoute("/")({ component: HomePage });

function HomePage() {
  // Тот же queryKey ({window: "today"}), что использует OperatorMiniDashboard —
  // react-query делит один кэш, refetch() здесь обновляет и его подписку.
  const snapshotQuery = useOperatorSnapshot({ window: "today" });
  return (
    <PullToRefresh onRefresh={() => snapshotQuery.refetch()}>
      <OperatorMiniDashboard />
    </PullToRefresh>
  );
}
