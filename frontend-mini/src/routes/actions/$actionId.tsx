import { createFileRoute } from "@tanstack/react-router";

import { MiniActionDetail } from "@/features/operator/OperatorActionDetail";

export const Route = createFileRoute("/actions/$actionId")({
  component: MiniActionDetailRoute,
});

function MiniActionDetailRoute() {
  const { actionId } = Route.useParams();
  return <MiniActionDetail actionId={actionId} />;
}
