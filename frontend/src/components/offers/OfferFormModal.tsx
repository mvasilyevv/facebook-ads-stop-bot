/**
 * OfferFormModal — Modal с формой создания / редактирования оффера.
 * При edit: code иммутабелен (disabled). Валидация code: uppercase A-Z0-9_-.
 *
 * Паттерн сброса: key={editOffer?.id ?? "new"} на внутреннем компоненте,
 * чтобы React пересоздавал форму при смене оффера — без setState в useEffect.
 */

import { useState } from "react";
import { Modal } from "@/components/ui/Modal";
import { Input } from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";
import { Button } from "@/components/ui/Button";
import { toast } from "@/components/ui/Toast";
import { useCreateOffer, useUpdateOffer } from "@/lib/api/offers";
import type { Offer } from "@/lib/types/api";

interface OfferFormModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** null/undefined — режим создания, Offer — режим редактирования. */
  editOffer?: Offer | null;
}

export function OfferFormModal({ open, onOpenChange, editOffer }: OfferFormModalProps) {
  return (
    <Modal
      open={open}
      onOpenChange={onOpenChange}
      title={editOffer ? `Редактировать — ${editOffer.code}` : "Новый оффер"}
      size="sm"
    >
      {/* key сбрасывает внутренний state при смене оффера */}
      <OfferForm
        key={editOffer?.id ?? "new"}
        editOffer={editOffer ?? null}
        onClose={() => onOpenChange(false)}
      />
    </Modal>
  );
}

// ─── Внутренняя форма ──────────────────────────────────────────────────────

const CODE_RE = /^[A-Z0-9_-]+$/;

export const VERTICAL_OPTIONS = [
  { value: "", label: "— Не задано —" },
  { value: "gambling", label: "Gambling" },
  { value: "nutra", label: "Nutra" },
  { value: "finance", label: "Finance" },
  { value: "crypto", label: "Crypto" },
  { value: "dating", label: "Dating" },
  { value: "other", label: "Other" },
];

/** Человекочитаемый лейбл вертикали (для карточек). */
export function verticalLabel(v: string | null | undefined): string {
  if (!v) return "";
  return VERTICAL_OPTIONS.find((o) => o.value === v)?.label ?? v;
}

interface FormState {
  code: string;
  vertical: string;
  is_active: boolean;
}

function initForm(offer: Offer | null): FormState {
  if (!offer) return { code: "", vertical: "", is_active: true };
  return {
    code: offer.code,
    vertical: offer.vertical ?? "",
    is_active: offer.is_active,
  };
}

interface OfferFormProps {
  editOffer: Offer | null;
  onClose: () => void;
}

function OfferForm({ editOffer, onClose }: OfferFormProps) {
  const isEdit = !!editOffer;
  const [form, setForm] = useState<FormState>(() => initForm(editOffer));
  const [codeError, setCodeError] = useState<string | undefined>();

  const createOffer = useCreateOffer();
  const updateOffer = useUpdateOffer();
  const busy = createOffer.isPending || updateOffer.isPending;

  function handleCodeChange(value: string) {
    const upper = value.toUpperCase();
    setForm((p) => ({ ...p, code: upper }));
    if (upper && !CODE_RE.test(upper)) {
      setCodeError("Только A-Z, 0-9, _ и - (без пробелов)");
    } else {
      setCodeError(undefined);
    }
  }

  function isValid(): boolean {
    if (!form.code || !CODE_RE.test(form.code)) return false;
    return true;
  }

  function handleSubmit() {
    if (!isValid() || busy) return;

    if (isEdit && editOffer) {
      updateOffer.mutate(
        {
          id: editOffer.id,
          data: {
            vertical: form.vertical || null,
            is_active: form.is_active,
          },
        },
        {
          onSuccess: () => {
            toast.success("Оффер обновлён", `${form.code} — изменения сохранены.`);
            onClose();
          },
          onError: (err) =>
            toast.error("Ошибка обновления", err instanceof Error ? err.message : String(err)),
        },
      );
    } else {
      createOffer.mutate(
        {
          code: form.code,
          vertical: form.vertical || null,
        },
        {
          onSuccess: () => {
            toast.success("Оффер создан", `${form.code} добавлен в каталог.`);
            onClose();
          },
          onError: (err) =>
            toast.error("Ошибка создания", err instanceof Error ? err.message : String(err)),
        },
      );
    }
  }

  return (
    <div className="flex flex-col gap-4">
      {/* Код оффера — иммутабелен при редактировании */}
      <Input
        id="offer-code"
        label="Код оффера *"
        placeholder="CR2, DRC_NUTRA, ..."
        value={form.code}
        onChange={(e) => handleCodeChange(e.target.value)}
        disabled={isEdit}
        errorMessage={codeError}
        helpText={
          !isEdit
            ? "Только буквы A-Z, цифры, _ и -. Нельзя изменить после создания."
            : undefined
        }
      />

      {/* Вертикаль */}
      <Select
        id="offer-vertical"
        label="Вертикаль"
        options={VERTICAL_OPTIONS}
        value={form.vertical}
        onChange={(e) => setForm((p) => ({ ...p, vertical: e.target.value }))}
      />

      {/* Статус (только при редактировании) */}
      {isEdit ? (
        <div>
          <label className="flex items-center gap-3 cursor-pointer select-none">
            <input
              type="checkbox"
              className="size-4 accent-accent"
              checked={form.is_active}
              onChange={(e) => setForm((p) => ({ ...p, is_active: e.target.checked }))}
            />
            <span className="text-[13px] text-bg-11">Активен</span>
          </label>
          <p className="text-[11px] text-bg-9 mt-1.5 ml-7">
            Снятие галочки переведёт оффер в неактивный — связанные объявления не затрагиваются.
          </p>
        </div>
      ) : null}

      {/* Кнопки */}
      <div className="flex justify-end gap-2 mt-2">
        <Button variant="ghost" onClick={onClose}>
          Отмена
        </Button>
        <Button
          variant="primary"
          loading={busy}
          disabled={!isValid()}
          onClick={handleSubmit}
        >
          {isEdit ? "Сохранить" : "Создать"}
        </Button>
      </div>
    </div>
  );
}
