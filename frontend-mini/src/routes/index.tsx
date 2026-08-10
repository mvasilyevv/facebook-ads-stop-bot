import { createFileRoute } from "@tanstack/react-router";

import { OperatorMiniDashboard } from "@/features/operator/OperatorMiniDashboard";

export const Route = createFileRoute("/")({ component: OperatorMiniDashboard });
