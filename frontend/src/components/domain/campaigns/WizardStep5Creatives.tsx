/**
 * Шаг 5 — Концепты креативов.
 *
 * Орекстрирует загрузку и привязку концептов к кампаниям, вынесенные в
 * под-компоненты (god-component >600 строк разнесён на части):
 *   - CreativeUploadZone     — drag&drop загрузка → POST /tools/campaigns/upload
 *   - ConceptCampaignMatrix  — привязка концептов к кампаниям («колонки-кампании»)
 *
 * Модель: concept.campaign_keys — ЯВНЫЙ список кампаний концепта. Пустой массив =
 * концепт не распределён (лежит в пуле «не распределены», не удалён). buildConfig
 * фильтрует концепты по includes(ключ кампании).
 */

import { type FC } from "react";
import { validateCampaignCreatives } from "@fb/features/campaigns";
import type { WizardCreatives, UploadedConcept } from "@/stores/campaignWizard";
import type { CampaignStructure } from "@/lib/api/campaigns";
import { CreativeUploadZone } from "./CreativeUploadZone";
import { ConceptCampaignMatrix } from "./ConceptCampaignMatrix";

interface WizardStep5CreativesProps {
  values: WizardCreatives;
  campaigns: CampaignStructure[];
  onChange: (v: Partial<WizardCreatives>) => void;
  errors?: string;
}

export const WizardStep5Creatives: FC<WizardStep5CreativesProps> = ({
  values,
  campaigns,
  onChange,
  errors,
}) => {
  const allKeys = campaigns.map((c) => c.key);

  const handleUploaded = (
    uploadId: string,
    serverConcepts: UploadedConcept[],
    addedRefs: string[],
  ) => {
    const currentByRef = new Map(values.concepts.map((concept) => [concept.ref, concept]));
    const added = new Set(addedRefs);
    const reconciled = serverConcepts.flatMap((concept) => {
      const current = currentByRef.get(concept.ref);
      if (current) return [{ ...concept, campaign_keys: current.campaign_keys }];
      if (added.has(concept.ref)) return [concept];
      // Файл физически остался в upload-папке, но пользователь ранее убрал его
      // из UI. Не возвращаем логически удалённый концепт при следующей дозагрузке.
      return [];
    });
    // Ответ upload содержит весь фактический серверный набор. Это одновременно
    // добавляет новые файлы и вычищает stale refs из сохранённого браузерного черновика.
    onChange({ upload_id: uploadId, concepts: reconciled });
  };

  const handleConceptsChange = (concepts: UploadedConcept[]) => onChange({ concepts });

  const removeConcept = (ref: string) =>
    onChange({ concepts: values.concepts.filter((c) => c.ref !== ref) });

  return (
    <div className="space-y-6">
      {/* Заголовок */}
      <div>
        <div className="font-display text-[12px] tracking-[0.14em] uppercase text-bg-8 mb-1">
          ШАГ 5 · КОНЦЕПТЫ
        </div>
        <h2 className="font-display text-[20px] font-medium text-bg-11 leading-tight m-0">
          Загрузка креативов
        </h2>
        <p className="text-[13px] text-bg-9 mt-1">
          Загрузите концепты (фото и видео можно вместе), затем распределите по кампаниям. Кнопка
          «Поровну» раскидывает по одной кампании на концепт, «В каждую» — во все. Каждый adset
          кампании получит уникализированную копию каждого её концепта.
        </p>
      </div>

      <CreativeUploadZone
        allCampaignKeys={allKeys}
        uploadId={values.upload_id}
        onUploaded={handleUploaded}
      />

      <ConceptCampaignMatrix
        concepts={values.concepts}
        campaigns={campaigns}
        onConceptsChange={handleConceptsChange}
        onRemoveConcept={removeConcept}
      />

      {/* upload_id badge */}
      {values.upload_id && (
        <div className="text-[12px] text-bg-8">
          upload_id: <span className="font-mono text-bg-9">{values.upload_id}</span>
        </div>
      )}

      {errors && (
        <span role="alert" className="text-[12px] text-danger font-display">
          {errors}
        </span>
      )}
    </div>
  );
};

// ─── Валидация ────────────────────────────────────────────────────────────────

export function validateCreatives(values: WizardCreatives): string | null {
  return validateCampaignCreatives(values);
}
