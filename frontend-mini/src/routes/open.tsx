import { createFileRoute } from "@tanstack/react-router";
import { lazy, Suspense, useSyncExternalStore } from "react";

import { Skeleton } from "@/components/ui";
import {
  readResolvedNavigationState,
  subscribeResolvedNavigation,
} from "@/lib/transientNavigation";

// Экран цели грузится по факту разрешения ссылки: до него неизвестно, какой из
// трёх нужен, а статический импорт всех трёх держал их — вместе с путём команды
// объявления — в стартовом чанке мини-приложения (issue #349). Сами экраны
// живут вне `routes/`: пока они лежали в route-файлах, routeTree.gen.ts
// статически импортировал те же модули, и вынести их не получалось.
const MiniAdDetail = lazy(async () => ({
  default: (await import("@/features/operator/OperatorAdDetail")).MiniAdDetail,
}));
const MiniActionDetail = lazy(async () => ({
  default: (await import("@/features/operator/OperatorActionDetail"))
    .MiniActionDetail,
}));
const MiniIncidentDetail = lazy(async () => ({
  default: (await import("@/features/operator/OperatorIncidentDetail"))
    .MiniIncidentDetail,
}));

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
  return (
    <Suspense fallback={<OpenTargetSkeleton />}>
      {target.target_kind === "ad" ? (
        <MiniAdDetail fbAdId={target.target_id} />
      ) : target.target_kind === "action" ? (
        <MiniActionDetail actionId={target.target_id} />
      ) : (
        <MiniIncidentDetail incidentId={target.target_id} />
      )}
    </Suspense>
  );
}

function OpenTargetSkeleton() {
  return (
    <div role="status" aria-label="Загрузка" className="grid gap-3 p-4">
      <Skeleton className="h-36 w-full" />
      <Skeleton className="h-52 w-full" />
    </div>
  );
}
