import { createFileRoute } from "@tanstack/react-router";

import { MiniAdDetail } from "@/features/operator/OperatorAdDetail";

export const Route = createFileRoute("/ads/$fbAdId")({
  component: AdDetailRoute,
});

function AdDetailRoute() {
  const { fbAdId } = Route.useParams();
  return <MiniAdDetail fbAdId={fbAdId} />;
}
