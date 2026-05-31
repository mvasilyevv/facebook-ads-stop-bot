/**
 * ConfirmDialog — destructive action с typed confirmation.
 * Confirm активен только когда input === target.
 */

import { useState } from "react";
import { Modal } from "./Modal";
import { Button } from "./Button";
import { Input } from "./Input";

interface ConfirmDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description: string;
  /** Строка, которую юзер должен напечатать чтобы активировать confirm. */
  confirmWord?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  /** Вариант кнопки подтверждения. danger — для деструктива (дефолт), primary — для обычного approve. */
  confirmVariant?: "danger" | "primary";
  onConfirm: () => void | Promise<void>;
}

export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmWord,
  confirmLabel = "Подтвердить",
  cancelLabel = "Отмена",
  confirmVariant = "danger",
  onConfirm,
}: ConfirmDialogProps) {
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const ok = confirmWord ? typed === confirmWord : true;

  async function handleConfirm() {
    if (!ok) return;
    setBusy(true);
    try {
      await onConfirm();
      onOpenChange(false);
      setTyped("");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open={open}
      onOpenChange={(o) => {
        onOpenChange(o);
        if (!o) setTyped("");
      }}
      title={title}
      description={description}
      size="sm"
    >
      {confirmWord ? (
        <div className="mb-6">
          <Input
            label={`Введите ${confirmWord} для подтверждения`}
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            placeholder={confirmWord}
            autoFocus
          />
        </div>
      ) : null}
      <div className="flex justify-end gap-2">
        <Button variant="ghost" onClick={() => onOpenChange(false)}>
          {cancelLabel}
        </Button>
        <Button variant={confirmVariant} disabled={!ok} loading={busy} onClick={handleConfirm}>
          {confirmLabel}
        </Button>
      </div>
    </Modal>
  );
}
