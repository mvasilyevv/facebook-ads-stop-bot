/**
 * /settings/vision — полноэкранный экран настроек Vision и desktop.
 * Раньше жил внутри общего Sheet «Ещё» (тройной вложенный скролл: страница →
 * Sheet → внутренний список). Detail-роут — как /desktop, /analytics и т.д.
 */
import { createFileRoute } from "@tanstack/react-router";

import { MiniHeader } from "@/components/layout/MiniHeader";
import { VisionSettings } from "@/features/settings/VisionSettings";
import { getStoredRole } from "@/lib/auth";

export const Route = createFileRoute("/settings/vision")({
  component: VisionSettingsPage,
});

function VisionSettingsPage() {
  const canEdit = getStoredRole() === "owner";
  return (
    <div className="flex min-h-full flex-col pb-[max(96px,var(--tg-content-safe-bottom,0px),env(safe-area-inset-bottom))]">
      <MiniHeader
        eyebrowNum="05"
        eyebrow="СИСТЕМА · НАСТРОЙКИ"
        title="Vision и desktop"
      />
      <div className="px-4 py-5">
        <VisionSettings canEdit={canEdit} />
      </div>
    </div>
  );
}
