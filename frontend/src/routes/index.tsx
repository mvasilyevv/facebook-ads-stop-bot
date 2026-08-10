import { createFileRoute } from "@tanstack/react-router";

import { OperatorDashboard } from "@/features/operator/OperatorDashboard";

export const Route = createFileRoute("/")({ component: OperatorDashboard });
