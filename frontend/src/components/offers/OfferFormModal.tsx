/**
 * OfferFormModal — Modal с формой создания / редактирования оффера.
 * При edit: code иммутабелен (disabled). Валидация code: uppercase A-Z0-9_-.
 *
 * Паттерн сброса: key={editOffer?.id ?? "new"} на внутреннем компоненте,
 * чтобы React пересоздавал форму при смене оффера — без setState в useEffect.
 *
 * CPA: при создании — POST /offers → PUT /offers/{id}/rules.
 *       при редактировании — PUT rules вместе с остальными порогами.
 */

import { useEffect, useState } from "react";
import { Modal } from "@/components/ui/Modal";
import { Input } from "@/components/ui/Input";
import { Button } from "@/components/ui/Button";
import { toast } from "@/components/ui/Toast";
import {
  useCreateOffer,
  useUpdateOffer,
  useOfferRules,
  useUpsertOfferRules,
} from "@/lib/api/offers";
import { parseRuleField } from "./RulesForm";
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

interface FormState {
  code: string;
  is_active: boolean;
  cpa: string; // CPA, $ (строка, пусто = null)
}

function initForm(offer: Offer | null): FormState {
  if (!offer) return { code: "", is_active: true, cpa: "" };
  return {
    code: offer.code,
    is_active: offer.is_active,
    cpa: "", // заполняется в OfferForm через useOfferRules
  };
}

interface OfferFormProps {
  editOffer: Offer | null;
  onClose: () => void;
}

function OfferForm({ editOffer, onClose }: OfferFormProps) {
  const isEdit = !!editOffer;
  const [form, setForm] = useState<FormState>(() => initForm(editOffer));
  const [cpaInitialized, setCpaInitialized] = useState(false);
  const [codeError, setCodeError] = useState<string | undefined>();

  const createOffer = useCreateOffer();
  const updateOffer = useUpdateOffer();
  const upsertRules = useUpsertOfferRules();

  // При редактировании — загружаем правила для получения текущего CPA.
  // H7d: инициализация CPA в useEffect (не в теле компонента) — setState в render
  // вызывал лишний рендер и StrictMode-петлю.
  const rulesQuery = useOfferRules(isEdit ? editOffer.id : null);
  const rulesData = rulesQuery.data;
  useEffect(() => {
    if (isEdit && rulesData && !cpaInitialized) {
      setForm((p) => ({ ...p, cpa: rulesData.cpa_threshold ?? "" }));
      setCpaInitialized(true);
    }
  }, [isEdit, rulesData, cpaInitialized]);

  const busy = createOffer.isPending || updateOffer.isPending || upsertRules.isPending;

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
    // В edit-режиме не даём сохранять, пока не подгрузился текущий CPA: иначе saveCpa
    // отправил бы cpa_threshold=null и обнулил бы CPA → build_rule_context взял бы
    // fallback $100, пороги автостопа стали бы в разы мягче (money-риск, нашёл review).
    if (isEdit && !cpaInitialized) return false;
    // Если CPA задан — должен быть валидным числом >= 0.
    if (form.cpa.trim()) {
      const n = Number.parseFloat(form.cpa.trim());
      if (Number.isNaN(n) || n < 0) return false;
    }
    return true;
  }

  function saveCpa(offerId: string) {
    const cpaValue = parseRuleField(form.cpa);
    // Partial: шлём ТОЛЬКО CPA — частота и чувствительность не трогаем (backend partial-upsert).
    return upsertRules.mutateAsync({ id: offerId, data: { cpa_threshold: cpaValue } });
  }

  function handleSubmit() {
    if (!isValid() || busy) return;

    if (isEdit && editOffer) {
      updateOffer.mutate(
        {
          id: editOffer.id,
          data: {
            is_active: form.is_active,
          },
        },
        {
          onSuccess: async () => {
            // Сохраняем CPA отдельным PUT rules.
            try {
              await saveCpa(editOffer.id);
            } catch {
              toast.error("Ошибка сохранения CPA", "Оффер обновлён, но CPA не сохранён.");
              return;
            }
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
        },
        {
          onSuccess: async (newOffer) => {
            // Если CPA задан — PUT rules после создания оффера.
            if (form.cpa.trim()) {
              try {
                await saveCpa(newOffer.id);
              } catch {
                toast.error("Оффер создан, но CPA не сохранён", "Задайте CPA в настройках правил.");
                onClose();
                return;
              }
            }
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

      {/* CPA, $ */}
      <Input
        id="offer-cpa"
        type="number"
        min={0}
        step="any"
        label="CPA, $"
        placeholder="Не задано"
        value={form.cpa}
        onChange={(e) => setForm((p) => ({ ...p, cpa: e.target.value }))}
        helpText="Стоп при превышении стоимости целевого действия. Пустое поле — правило выключено."
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
