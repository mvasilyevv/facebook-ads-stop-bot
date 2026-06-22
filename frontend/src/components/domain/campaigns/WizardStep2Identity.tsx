/**
 * Шаг 2 — Идентичность + Оффер.
 *
 * Поля: act_id, page_id, pixel_id, tz_offset, offer_code, byer_tag.
 * Если шаг 1 был "preset" — поля предзаполнены из пресета.
 */

import { type FC } from "react";
import { Input } from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";
import type { WizardIdentity } from "@/stores/campaignWizard";

interface WizardStep2IdentityProps {
  values: WizardIdentity;
  onChange: (v: Partial<WizardIdentity>) => void;
  /** Ошибки валидации по именам полей. */
  errors?: Partial<Record<keyof WizardIdentity, string>>;
}

const TZ_OPTIONS = [
  { value: "0", label: "UTC+0" },
  { value: "1", label: "UTC+1" },
  { value: "2", label: "UTC+2" },
  { value: "3", label: "UTC+3 (МСК)" },
  { value: "4", label: "UTC+4" },
  { value: "5", label: "UTC+5" },
  { value: "6", label: "UTC+6" },
  { value: "7", label: "UTC+7" },
  { value: "8", label: "UTC+8" },
];

export const WizardStep2Identity: FC<WizardStep2IdentityProps> = ({
  values,
  onChange,
  errors = {},
}) => {
  return (
    <div className="space-y-6">
      {/* Заголовок */}
      <div>
        <div className="font-display text-[10px] tracking-[0.14em] uppercase text-bg-7 mb-1">
          ШАГ 2 · ИДЕНТИЧНОСТЬ
        </div>
        <h2 className="font-display text-[20px] font-medium text-bg-11 leading-tight m-0">
          Кабинет и оффер
        </h2>
        <p className="text-[13px] text-bg-9 mt-1">
          Укажите ID рекламного кабинета, страницы, пикселя и код оффера.
        </p>
      </div>

      {/* Кабинет */}
      <div>
        <div className="font-display text-[10px] tracking-[0.14em] uppercase text-bg-7 mb-3">
          РЕКЛАМНЫЙ КАБИНЕТ
        </div>
        <div className="grid grid-cols-2 gap-4">
          <Input
            label="Ad Account ID"
            placeholder="act_123456789"
            value={values.act_id}
            onChange={(e) => onChange({ act_id: e.target.value })}
            errorMessage={errors.act_id}
            helpText="Числовой ID с префиксом act_ или без"
          />
          <Select
            label="Timezone"
            options={TZ_OPTIONS}
            value={String(values.tz_offset)}
            onChange={(e) => onChange({ tz_offset: Number(e.target.value) })}
          />
        </div>
      </div>

      {/* Страница и пиксель */}
      <div>
        <div className="font-display text-[10px] tracking-[0.14em] uppercase text-bg-7 mb-3">
          СТРАНИЦА И ПИКСЕЛЬ
        </div>
        <div className="grid grid-cols-2 gap-4">
          <Input
            label="Facebook Page ID"
            placeholder="123456789"
            value={values.page_id}
            onChange={(e) => onChange({ page_id: e.target.value })}
            errorMessage={errors.page_id}
          />
          <Input
            label="FB Pixel ID"
            placeholder="123456789"
            value={values.pixel_id}
            onChange={(e) => onChange({ pixel_id: e.target.value })}
            errorMessage={errors.pixel_id}
          />
        </div>
      </div>

      {/* Оффер */}
      <div>
        <div className="font-display text-[10px] tracking-[0.14em] uppercase text-bg-7 mb-3">
          ОФФЕР И БАЙЕР
        </div>
        <div className="grid grid-cols-2 gap-4">
          <Input
            label="Код оффера"
            placeholder="GH_CR2"
            value={values.offer_code}
            onChange={(e) => onChange({ offer_code: e.target.value.toUpperCase() })}
            errorMessage={errors.offer_code}
            helpText="Войдёт в название кампании"
          />
          <Input
            label="Тег байера"
            placeholder="MV"
            value={values.byer_tag}
            onChange={(e) => onChange({ byer_tag: e.target.value.toUpperCase() })}
            errorMessage={errors.byer_tag}
            helpText="Opcionально — для фильтра owner_campaign_tag"
          />
        </div>
      </div>
    </div>
  );
};

// ─── Валидация ────────────────────────────────────────────────────────────────

export function validateIdentity(values: WizardIdentity): Partial<Record<keyof WizardIdentity, string>> {
  const errors: Partial<Record<keyof WizardIdentity, string>> = {};

  if (!values.act_id.trim()) errors.act_id = "Обязательное поле";
  if (!values.page_id.trim()) errors.page_id = "Обязательное поле";
  if (!values.pixel_id.trim()) errors.pixel_id = "Обязательное поле";
  if (!values.offer_code.trim()) errors.offer_code = "Обязательное поле";

  return errors;
}
