/**
 * /settings/observer — полноэкранный экран настроек Observer.
 * Раньше жил внутри общего Sheet «Ещё» (тройной вложенный скролл: страница →
 * Sheet → внутренний список). Detail-роут — как /desktop, /analytics и т.д.
 */
import { createFileRoute } from "@tanstack/react-router";

import { MiniHeader } from "@/components/layout/MiniHeader";
import { ObserverSettings } from "@/features/settings/ObserverSettings";
import { getStoredRole } from "@/lib/auth";

export const Route = createFileRoute("/settings/observer")({
  component: ObserverSettingsPage,
});

function ObserverSettingsPage() {
  const canEdit = getStoredRole() === "owner";
  return (
    <div className="flex min-h-full flex-col pb-[max(96px,var(--tg-content-safe-bottom,0px),env(safe-area-inset-bottom))]">
      <MiniHeader
        eyebrowNum="05"
        eyebrow="СИСТЕМА · НАСТРОЙКИ"
        title="Observer"
      />
      <div className="px-4 py-5">
        <ObserverSettings canEdit={canEdit} />
      </div>
    </div>
  );
}
