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

  const handleUploaded = (uploadId: string, newConcepts: UploadedConcept[]) => {
    onChange({ upload_id: uploadId, concepts: [...values.concepts, ...newConcepts] });
  };

  const handleConceptsChange = (concepts: UploadedConcept[]) => onChange({ concepts });

  const removeConcept = (ref: string) =>
    onChange({ concepts: values.concepts.filter((c) => c.ref !== ref) });

  return (
    <div className="space-y-6">
      {/* Заголовок */}
      <div>
        <div className="font-display text-[10px] tracking-[0.14em] uppercase text-bg-7 mb-1">
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

      <CreativeUploadZone allCampaignKeys={allKeys} onUploaded={handleUploaded} />

      <ConceptCampaignMatrix
        concepts={values.concepts}
        campaigns={campaigns}
        onConceptsChange={handleConceptsChange}
        onRemoveConcept={removeConcept}
      />

      {/* upload_id badge */}
      {values.upload_id && (
        <div className="text-[11px] text-bg-7">
          upload_id: <span className="font-mono text-bg-9">{values.upload_id}</span>
        </div>
      )}

      {errors && (
        <span role="alert" className="text-[11px] text-danger font-display">
          {errors}
        </span>
      )}
    </div>
  );
};

// ─── Валидация ────────────────────────────────────────────────────────────────

export function validateCreatives(values: WizardCreatives): string | null {
  if (values.concepts.length === 0) return "Загрузите хотя бы один концепт";
  if (!values.upload_id) return "Концепты не загружены на сервер";
  return null;
}
