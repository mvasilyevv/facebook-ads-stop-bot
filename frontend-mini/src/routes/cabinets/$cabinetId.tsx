import { createFileRoute } from "@tanstack/react-router";

import { OperatorMiniCabinetDashboard } from "@/features/operator/OperatorMiniCabinetDashboard";

export const Route = createFileRoute("/cabinets/$cabinetId")({
  component: CabinetRoute,
});

function CabinetRoute() {
  const { cabinetId } = Route.useParams();
  return <OperatorMiniCabinetDashboard cabinetId={cabinetId} />;
}
