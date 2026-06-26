/**
 * Шаг 5 — Концепты креативов.
 *
 * Drag&drop загрузка файлов → POST /tools/campaigns/upload → upload_id.
 * Список загруженных концептов с привязкой к кампаниям (типонезависимой).
 * Сводка по кампаниям: adset'ы × привязанные концепты = объявления (копий на концепт
 * НЕ задаётся вручную — бэк делает по числу adset'ов КАЖДОЙ кампании).
 */

import { type FC, useRef, useState, useCallback } from "react";
import { Upload, X, Film, Image, AlertCircle } from "lucide-react";
import { cn } from "@/lib/utils/cn";
import { Spinner } from "@/components/ui/Spinner";
import { uploadConcepts } from "@/lib/api/campaigns";
import type { WizardCreatives, UploadedConcept } from "@/stores/campaignWizard";
import type { CampaignStructure } from "@/lib/api/campaigns";

interface WizardStep5CreativesProps {
  values: WizardCreatives;
  campaigns: CampaignStructure[];
  onChange: (v: Partial<WizardCreatives>) => void;
  errors?: string;
}

// ─── Утилиты ─────────────────────────────────────────────────────────────────

function formatBytes(b: number): string {
  if (b < 1024) return `${b} B`;
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)} KB`;
  return `${(b / (1024 * 1024)).toFixed(1)} MB`;
}

function isVideo(ct: string | null): boolean {
  return !!ct?.startsWith("video/");
}

/** Русское склонение для «объявление» (1 / 2-4 / 5+). */
function adWord(n: number): string {
  const m10 = n % 10;
  const m100 = n % 100;
  if (m10 === 1 && m100 !== 11) return "объявление";
  if (m10 >= 2 && m10 <= 4 && (m100 < 10 || m100 >= 20)) return "объявления";
  return "объявлений";
}

/** Считает кол-во фото/видео-концептов, привязанных к кампании с ключом `key`. */
function countMediaForCampaign(
  concepts: UploadedConcept[],
  key: string,
): { img: number; vid: number } {
  let img = 0;
  let vid = 0;
  for (const c of concepts) {
    const attached = c.campaign_keys.length === 0 || c.campaign_keys.includes(key);
    if (!attached) continue;
    if (isVideo(c.content_type)) {
      vid++;
    } else {
      img++;
    }
  }
  return { img, vid };
}

// Палитра цветов кампаний (циклическая) — единый цвет кампании в сводке и на чипах.
const CAMPAIGN_CHIP_COLORS = [
  { dot: "bg-blue-400", chip: "bg-blue-500/15 border-blue-500/40 text-blue-300" },
  { dot: "bg-purple-400", chip: "bg-purple-500/15 border-purple-500/40 text-purple-300" },
  { dot: "bg-emerald-400", chip: "bg-emerald-500/15 border-emerald-500/40 text-emerald-300" },
  { dot: "bg-amber-400", chip: "bg-amber-500/15 border-amber-500/40 text-amber-300" },
  { dot: "bg-pink-400", chip: "bg-pink-500/15 border-pink-500/40 text-pink-300" },
  { dot: "bg-cyan-400", chip: "bg-cyan-500/15 border-cyan-500/40 text-cyan-300" },
];

function campaignChipColor(index: number): { dot: string; chip: string } {
  return CAMPAIGN_CHIP_COLORS[index % CAMPAIGN_CHIP_COLORS.length]!;
}

// ─── Компонент ────────────────────────────────────────────────────────────────

export const WizardStep5Creatives: FC<WizardStep5CreativesProps> = ({
  values,
  campaigns,
  onChange,
  errors,
}) => {
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [isDragOver, setIsDragOver] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  // Медиа-состав по кампаниям + цвет/индекс кампании (для сводки и чипов).
  const campaignMediaCounts: Record<string, { img: number; vid: number }> = {};
  const campaignIndexByKey: Record<string, number> = {};
  campaigns.forEach((campaign, i) => {
    campaignMediaCounts[campaign.key] = countMediaForCampaign(values.concepts, campaign.key);
    campaignIndexByKey[campaign.key] = i;
  });

  const handleFiles = useCallback(
    async (files: FileList | File[]) => {
      const arr = Array.from(files);
      if (!arr.length) return;

      setUploading(true);
      setUploadError(null);
      try {
        const result = await uploadConcepts(arr);
        // Новые концепты — привязаны ко всем кампаниям по умолчанию
        const newConcepts: UploadedConcept[] = result.concepts.map((c) => ({
          ...c,
          campaign_keys: [],
        }));
        onChange({
          upload_id: result.upload_id,
          concepts: [...values.concepts, ...newConcepts],
        });
      } catch (e) {
        setUploadError(e instanceof Error ? e.message : "Ошибка загрузки");
      } finally {
        setUploading(false);
      }
    },
    [values.concepts, onChange],
  );

  const handleInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files?.length) {
      void handleFiles(e.target.files);
    }
    // сброс input для повторной загрузки тех же файлов
    e.target.value = "";
  };

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setIsDragOver(false);
      if (e.dataTransfer.files.length) {
        void handleFiles(e.dataTransfer.files);
      }
    },
    [handleFiles],
  );

  const removeConcept = (ref: string) => {
    onChange({ concepts: values.concepts.filter((c) => c.ref !== ref) });
  };

  const toggleCampaignKey = (conceptRef: string, key: string) => {
    onChange({
      concepts: values.concepts.map((c) => {
        if (c.ref !== conceptRef) return c;
        const keys = c.campaign_keys.includes(key)
          ? c.campaign_keys.filter((k) => k !== key)
          : [...c.campaign_keys, key];
        return { ...c, campaign_keys: keys };
      }),
    });
  };

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
          Перетащите или выберите концепты — фото и видео можно загружать вместе, привязка к
          кампании типонезависима. Каждый adset кампании получит уникализированную копию каждого
          привязанного концепта — итог по кампаниям ниже.
        </p>
      </div>

      {/* Dropzone */}
      <div
        onDragOver={(e) => {
          e.preventDefault();
          setIsDragOver(true);
        }}
        onDragLeave={() => setIsDragOver(false)}
        onDrop={handleDrop}
        className={cn(
          "border-2 border-dashed rounded-[var(--radius-3)] p-8 text-center transition-all duration-[120ms] cursor-pointer",
          isDragOver
            ? "border-accent bg-accent-bg"
            : "border-[var(--hairline-strong)] bg-bg-2 hover:border-accent hover:bg-accent-bg/50",
        )}
        onClick={() => inputRef.current?.click()}
        role="button"
        aria-label="Загрузить концепты"
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") inputRef.current?.click();
        }}
      >
        <input
          ref={inputRef}
          type="file"
          multiple
          accept="image/*,video/*"
          className="sr-only"
          onChange={handleInputChange}
          aria-hidden="true"
        />
        {uploading ? (
          <div className="flex flex-col items-center gap-2">
            <Spinner size={24} />
            <span className="text-[13px] text-bg-9">Загрузка на сервер...</span>
          </div>
        ) : (
          <div className="flex flex-col items-center gap-2">
            <Upload size={28} className="text-bg-7" />
            <span className="text-[13px] text-bg-9">
              Перетащите файлы или{" "}
              <span className="text-accent underline underline-offset-2">нажмите для выбора</span>
            </span>
            <span className="text-[11px] text-bg-7">
              JPG, PNG, MP4, MOV — до 500 МБ суммарно, до 50 файлов
            </span>
          </div>
        )}
      </div>

      {/* Ошибка upload */}
      {uploadError && (
        <div
          role="alert"
          className="flex items-center gap-2 text-[12px] text-danger bg-danger/10 border border-danger/30 rounded-[var(--radius-2)] px-3 py-2"
        >
          <AlertCircle size={13} className="shrink-0" />
          {uploadError}
        </div>
      )}

      {/* Сводка по кампаниям: adset'ы × привязанные концепты = объявления */}
      {values.concepts.length > 0 && campaigns.length > 0 && (
        <div>
          <div className="font-display text-[10px] tracking-[0.14em] uppercase text-bg-7 mb-2">
            ПО КАМПАНИЯМ
          </div>
          <div className="flex flex-wrap gap-2">
            {campaigns.map((c, i) => {
              const counts = campaignMediaCounts[c.key] ?? { img: 0, vid: 0 };
              const k = counts.img + counts.vid;
              const ads = c.adset_count * k;
              const color = campaignChipColor(i);
              return (
                <div
                  key={c.key}
                  className="flex-1 min-w-[200px] border border-[var(--hairline)] rounded-[var(--radius-2)] bg-bg-1 px-3 py-2.5"
                >
                  <div className="flex items-center gap-2 mb-1">
                    <span className={cn("size-2 rounded-full shrink-0", color.dot)} aria-hidden="true" />
                    <span className="font-display text-[12px] text-bg-11">{c.key}</span>
                    <span className="text-[11px] text-bg-7">· {c.adset_count} adset</span>
                  </div>
                  <div className="text-[11px] text-bg-9">
                    {counts.img} фото + {counts.vid} видео →{" "}
                    <b className="text-bg-11">
                      {ads} {adWord(ads)}
                    </b>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Список загруженных концептов */}
      {values.concepts.length > 0 && (
        <div className="space-y-2">
          <div className="font-display text-[10px] tracking-[0.14em] uppercase text-bg-7">
            КОНЦЕПТЫ ({values.concepts.length})
          </div>
          {values.concepts.map((concept) => (
            <ConceptRow
              key={concept.ref}
              concept={concept}
              campaigns={campaigns}
              campaignMediaCounts={campaignMediaCounts}
              campaignIndexByKey={campaignIndexByKey}
              onRemove={() => removeConcept(concept.ref)}
              onToggleCampaign={(key) => toggleCampaignKey(concept.ref, key)}
            />
          ))}
        </div>
      )}

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

// ─── ConceptRow ───────────────────────────────────────────────────────────────

interface ConceptRowProps {
  concept: UploadedConcept;
  campaigns: CampaignStructure[];
  /** Медиа-состав по ключу кампании: сколько фото/видео привязано к каждой кампании. */
  campaignMediaCounts: Record<string, { img: number; vid: number }>;
  /** Индекс кампании по ключу — для согласованного цвета чипа со сводкой. */
  campaignIndexByKey: Record<string, number>;
  onRemove: () => void;
  onToggleCampaign: (key: string) => void;
}

const ConceptRow: FC<ConceptRowProps> = ({
  concept,
  campaigns,
  campaignMediaCounts,
  campaignIndexByKey,
  onRemove,
  onToggleCampaign,
}) => {
  const video = isVideo(concept.content_type);

  return (
    <div className="border border-[var(--hairline)] rounded-[var(--radius-2)] px-3 py-2.5 bg-bg-1 flex items-center gap-3">
      {/* Иконка */}
      <div
        className={cn(
          "size-7 shrink-0 rounded-[var(--radius-1)] flex items-center justify-center",
          video ? "bg-purple-500/10 text-purple-400" : "bg-blue-500/10 text-blue-400",
        )}
        aria-hidden="true"
      >
        {video ? <Film size={13} /> : <Image size={13} />}
      </div>

      {/* Имя и размер */}
      <div className="flex-1 min-w-0">
        <div className="text-[12px] text-bg-11 truncate" title={concept.original_name}>
          {concept.original_name}
        </div>
        <div className="text-[11px] text-bg-7">{formatBytes(concept.size_bytes)}</div>
      </div>

      {/* Привязка к кампаниям (если их > 1) — цвет чипа = цвет кампании в сводке */}
      {campaigns.length > 1 && (
        <div className="flex items-center gap-1.5 shrink-0">
          <span className="text-[10px] text-bg-7 font-display uppercase tracking-wider">
            кампании:
          </span>
          {campaigns.map((c) => {
            const isActive =
              concept.campaign_keys.length === 0 || concept.campaign_keys.includes(c.key);
            const color = campaignChipColor(campaignIndexByKey[c.key] ?? 0);
            return (
              <button
                key={c.key}
                type="button"
                onClick={() => onToggleCampaign(c.key)}
                className={cn(
                  "font-display text-[10px] px-1.5 py-0.5 rounded border transition-colors",
                  isActive
                    ? color.chip
                    : "bg-bg-2 border-[var(--hairline)] text-bg-7 hover:border-[var(--hairline-strong)]",
                )}
                aria-pressed={isActive}
                title={(() => {
                  const counts = campaignMediaCounts[c.key];
                  if (!counts) return `${c.key} · ${c.adset_count} adsets`;
                  return `${c.key} · ${counts.img} фото + ${counts.vid} видео · ${c.adset_count} adsets`;
                })()}
              >
                {c.key}
              </button>
            );
          })}
        </div>
      )}

      {/* Удалить */}
      <button
        type="button"
        aria-label={`Удалить ${concept.original_name}`}
        onClick={onRemove}
        className="shrink-0 size-6 flex items-center justify-center text-bg-7 hover:text-danger transition-colors"
      >
        <X size={12} />
      </button>
    </div>
  );
};

// ─── Валидация ────────────────────────────────────────────────────────────────

export function validateCreatives(values: WizardCreatives): string | null {
  if (values.concepts.length === 0) return "Загрузите хотя бы один концепт";
  if (!values.upload_id) return "Концепты не загружены на сервер";
  return null;
}
