import { createFileRoute } from "@tanstack/react-router";
import { useSyncExternalStore } from "react";

import {
  readResolvedNavigationState,
  subscribeResolvedNavigation,
} from "@/lib/transientNavigation";
import { MiniActionDetail } from "@/routes/actions/$actionId";
import { MiniAdDetail } from "@/routes/ads/$fbAdId";
import { MiniIncidentDetail } from "@/routes/incidents/$incidentId";

export const Route = createFileRoute("/open")({ component: OpaqueTargetPage });

export function OpaqueTargetPage() {
  const resolution = useSyncExternalStore(
    subscribeResolvedNavigation,
    readResolvedNavigationState,
    readResolvedNavigationState,
  );
  if (resolution.status === "resolving") {
    return (
      <div
        role="status"
        aria-live="polite"
        className="m-4 rounded-[var(--radius-2)] border border-warning/30 bg-warning-bg p-4 text-[14px] text-bg-11"
      >
        Проверяем ссылку и право доступа…
      </div>
    );
  }
  if (resolution.status !== "resolved") {
    return (
      <div
        role="alert"
        className="m-4 rounded-[var(--radius-2)] bg-danger-bg p-4 text-[14px] text-danger"
      >
        Ссылка недействительна, истекла или уже использована.
      </div>
    );
  }
  const target = resolution.target;
  if (target.target_kind === "ad")
    return <MiniAdDetail fbAdId={target.target_id} />;
  if (target.target_kind === "action") {
    return <MiniActionDetail actionId={target.target_id} />;
  }
  return <MiniIncidentDetail incidentId={target.target_id} />;
}
