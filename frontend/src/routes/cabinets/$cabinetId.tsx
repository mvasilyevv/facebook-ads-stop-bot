import { createFileRoute } from "@tanstack/react-router";

import { OperatorCabinetDashboard } from "@/features/operator/OperatorDashboard";

export const Route = createFileRoute("/cabinets/$cabinetId")({
  component: CabinetDashboardRoute,
});

function CabinetDashboardRoute() {
  const { cabinetId } = Route.useParams();
  return <OperatorCabinetDashboard cabinetId={cabinetId} />;
}
