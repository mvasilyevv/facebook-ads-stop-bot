import { createFileRoute } from "@tanstack/react-router";
import { lazy, Suspense, useSyncExternalStore } from "react";

import { Skeleton } from "@/components/ui";
import {
  readResolvedNavigationState,
  subscribeResolvedNavigation,
} from "@/lib/transientNavigation";
import { MiniAdDetail } from "@/routes/ads/$fbAdId";
import { MiniIncidentDetail } from "@/routes/incidents/$incidentId";

// MiniAdDetail/MiniIncidentDetail — статический импорт: оба тянут
// @/features/operator/OperatorAds, который и так статически нужен главному
// экрану (OperatorMiniDashboard). Ленивый chunk для них выходит ДОРОЖЕ по
// сумме gzip, чем общий поток с уже нужным дашборду кодом (см. отчёт по
// issue #349) — Rollup вынужден дробить общий код на отдельный чанк со
// своим сжатием, а не переиспользовать поток entry/дашборда.
//
// MiniActionDetail детали не зависит от OperatorAds вообще (только от
// actionLabels/viewModel), поэтому вынесен в отдельный не-route файл
// ActionDetailView.tsx (см. routeFileIgnorePattern в vite.config.ts) и
// грузится по-настоящему лениво — это чистая экономия на первом экране.
const MiniActionDetail = lazy(() =>
  import("@/routes/actions/ActionDetailView").then((module) => ({
    default: module.MiniActionDetail,
  })),
);

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
    return (
      <Suspense fallback={<OpenTargetSkeleton />}>
        <MiniActionDetail actionId={target.target_id} />
      </Suspense>
    );
  }
  return <MiniIncidentDetail incidentId={target.target_id} />;
}

function OpenTargetSkeleton() {
  return (
    <div role="status" aria-label="Загрузка" className="grid gap-3 p-4">
      <Skeleton className="h-36 w-full" />
      <Skeleton className="h-52 w-full" />
    </div>
  );
}
