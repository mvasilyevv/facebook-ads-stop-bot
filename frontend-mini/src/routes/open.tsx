import { createFileRoute } from "@tanstack/react-router";
import { lazy, Suspense, useSyncExternalStore } from "react";

import {
  readResolvedNavigationState,
  subscribeResolvedNavigation,
} from "@/lib/transientNavigation";

// Экраны цели грузятся по факту разрешения ссылки: до него неизвестно, какой
// из трёх нужен, а статический импорт всех трёх тянул их в стартовый чанк
// мини-приложения (issue #349).
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
    return <OpaqueTargetPending />;
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
    <Suspense fallback={<OpaqueTargetPending text="Открываем экран…" />}>
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

function OpaqueTargetPending({
  text = "Проверяем ссылку и право доступа…",
}: {
  text?: string;
}) {
  return (
    <div
      role="status"
      aria-live="polite"
      className="m-4 rounded-[var(--radius-2)] border border-warning/30 bg-warning-bg p-4 text-[14px] text-bg-11"
    >
      {text}
    </div>
  );
}
