/**
 * AdDisableButton — футер AdDrawer: MONEY-действие «Отключить объявление».
 * confirm-with-typing (ConfirmDialog требует напечатать DISABLE) + idempotency_token.
 *
 * Выделено из AdDrawer.tsx (было >600 строк в одном файле — god-component).
 * ConfirmDialog рендерится здесь же (не в Drawer) — Modal внутри использует
 * Radix Dialog.Portal, позиция в дереве React на DOM/z-index не влияет.
 */
import { useState } from "react";
import { Ban } from "lucide-react";

import { Button } from "@/components/ui/Button";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { toast } from "@/components/ui/Toast";
import { useBulkDisable } from "@/lib/api/ads";

interface AdDisableButtonProps {
  fbAdId: string;
  /** Объявление уже показано как «Выключено» (bot-disabled ИЛИ выключено в Ads Manager) — не предлагаем отключать снова. */
  alreadyDisabled: boolean;
  onDisabled: () => void;
}

export function AdDisableButton({ fbAdId, alreadyDisabled, onDisabled }: AdDisableButtonProps) {
  const [confirmOpen, setConfirmOpen] = useState(false);
  const bulkDisable = useBulkDisable();
  const pending = bulkDisable.isPending;

  // MONEY: disable одного — idempotency_token=randomUUID (отдельное поле).
  async function handleDisableConfirm() {
    if (!fbAdId) return;
    await bulkDisable.mutateAsync({
      fb_ad_ids: [fbAdId],
      idempotency_token: crypto.randomUUID(),
      reason: "manual disable via drawer",
    });
    toast.success("Создана disable-задача");
    onDisabled();
  }

  if (alreadyDisabled) {
    return (
      <div
        className="flex w-full items-center justify-center gap-2 py-1 text-[13px] text-bg-9"
        role="status"
      >
        <Ban size={14} aria-hidden="true" />
        Объявление отключено
      </div>
    );
  }

  return (
    <>
      <div className="flex w-full gap-3">
        <Button
          variant="danger"
          className="flex-1"
          leftIcon={<Ban size={15} aria-hidden="true" />}
          onClick={() => setConfirmOpen(true)}
          disabled={pending}
          loading={bulkDisable.isPending}
          aria-label="Отключить объявление вручную"
        >
          Отключить
        </Button>
      </div>

      {/* MONEY: confirm-with-typing DISABLE */}
      <ConfirmDialog
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
        title="Отключить объявление?"
        description={`Будет создана disable-задача для ${fbAdId}. Действие необратимо без ручного включения.`}
        confirmWord="DISABLE"
        confirmLabel="Отключить"
        confirmVariant="danger"
        onConfirm={handleDisableConfirm}
      />
    </>
  );
}
