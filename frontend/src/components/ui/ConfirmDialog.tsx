/**
 * ConfirmDialog — деструктивное действие с typed-confirmation или обычный approve.
 * Confirm-кнопка активна только если typed === confirmWord.
 * Variants: danger (default, красная) / primary (одобрение).
 */
import { useState } from "react";
import { Modal, ModalFooter } from "./Modal";
import { Button } from "./Button";
import { Input } from "./Input";

interface ConfirmDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description: string;
  /** Строка, которую юзер должен напечатать для активации confirm. */
  confirmWord?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  /** danger — для деструктива, primary — для обычного approve. */
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

  function handleClose(next: boolean) {
    if (!next) setTyped("");
    onOpenChange(next);
  }

  async function handleConfirm() {
    if (!ok || busy) return;
    setBusy(true);
    try {
      await onConfirm();
      handleClose(false); // закрываем ТОЛЬКО при успехе
    } catch {
      // L1: ошибку покажет глобальный MutationCache.onError (toast). Диалог НЕ закрываем —
      // оператор видит, что money-действие не выполнилось, и может повторить. Глотаем тут,
      // чтобы rejection не всплыл как unhandled из `void handleConfirm()`.
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open={open}
      onOpenChange={handleClose}
      title={title}
      description={description}
      size="sm"
    >
      {confirmWord ? (
        <div className="mb-6">
          <Input
            label={`Введите "${confirmWord}" для подтверждения`}
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            placeholder={confirmWord}
            onKeyDown={(e) => {
              if (e.key === "Enter" && ok) void handleConfirm();
            }}
            // autoFocus через data-attribute чтобы не ломать a11y
            autoFocus
          />
        </div>
      ) : null}
      <ModalFooter>
        <Button variant="ghost" onClick={() => handleClose(false)}>
          {cancelLabel}
        </Button>
        <Button
          variant={confirmVariant}
          disabled={!ok}
          loading={busy}
          onClick={() => void handleConfirm()}
        >
          {confirmLabel}
        </Button>
      </ModalFooter>
    </Modal>
  );
}
