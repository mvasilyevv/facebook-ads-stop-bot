import { createFileRoute } from "@tanstack/react-router";

import { MiniActionDetail } from "@/routes/actions/ActionDetailView";

export const Route = createFileRoute("/actions/$actionId")({
  component: MiniActionDetailRoute,
});

function MiniActionDetailRoute() {
  const { actionId } = Route.useParams();
  return <MiniActionDetail actionId={actionId} />;
}
