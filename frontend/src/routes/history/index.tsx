import { createFileRoute, redirect } from "@tanstack/react-router";
import { analyticsRouteSearch } from "@/lib/analyticsSearch";

export const Route = createFileRoute("/history/")({
  beforeLoad: () => {
    throw redirect({
      to: "/analytics",
      search: analyticsRouteSearch({ tab: "events", period: "30d" }),
      replace: true,
    });
  },
});
