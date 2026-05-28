/**
 * Dashboard placeholder.
 * Полная реализация — Round 8.X.
 */

import { createFileRoute } from "@tanstack/react-router";
import { PageHeader, HeaderSep } from "@/components/layout/PageHeader";
import { EmptyState } from "@/components/ui/EmptyState";
import { Button } from "@/components/ui/Button";
import { RefreshCcw, Activity } from "lucide-react";
import { useDashboardSocket } from "@/lib/websocket/useDashboardSocket";

export const Route = createFileRoute("/")({
  component: DashboardPage,
});

function DashboardPage() {
  const socket = useDashboardSocket();

  return (
    <>
      <PageHeader
        eyebrowNum="01"
        eyebrow="OVERVIEW · OBSERVE · OPERATE"
        title="Dashboard."
        displayNumber="01"
        subtitle={
          <>
            <span>
              <span
                aria-hidden="true"
                className="inline-block size-1.5 rounded-full bg-success mr-1.5 align-middle pulse-dot"
              />
              Observer pending
            </span>
            <HeaderSep />
            <span>WS: {socket.status}</span>
            {socket.pollingFallback ? (
              <>
                <HeaderSep />
                <span className="text-warning">polling fallback</span>
              </>
            ) : null}
          </>
        }
        action={
          <Button variant="primary" leftIcon={<RefreshCcw size={14} aria-hidden="true" />}>
            Scan now
          </Button>
        }
      />

      <EmptyState
        icon={<Activity size={40} strokeWidth={1.25} aria-hidden="true" />}
        title="Coming soon"
        description="Round 8.X will implement KPI strip, spend chart, incidents feed and task queues."
      />
    </>
  );
}
