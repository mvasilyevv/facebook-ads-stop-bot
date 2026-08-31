import { createFileRoute } from "@tanstack/react-router";

import { MiniIncidentDetail } from "@/features/operator/OperatorIncidentDetail";

export const Route = createFileRoute("/incidents/$incidentId")({
  component: MiniIncidentDetailRoute,
});

function MiniIncidentDetailRoute() {
  const { incidentId } = Route.useParams();
  return <MiniIncidentDetail incidentId={incidentId} />;
}
