/**
 * Шаг 5 — Концепты креативов.
 *
 * Drag&drop загрузка файлов → POST /tools/campaigns/upload → upload_id.
 * Список загруженных концептов с привязкой к кампаниям.
 * Число copies_per_concept (advanced).
 */

import { type FC, useRef, useState, useCallback } from "react";
import { Upload, X, Film, Image, AlertCircle } from "lucide-react";
import { cn } from "@/lib/utils/cn";
import { Input } from "@/components/ui/Input";
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

  // Число adset'ов всего (дефолт copies_per_concept)
  const totalAdsets = campaigns.reduce((s, c) => s + c.adset_count, 0);

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
          Перетащите или выберите концепты (фото/видео). Бот автоматически создаст{" "}
          <b className="text-bg-11">{totalAdsets || "N"}</b> уникализаций каждого.
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
              onRemove={() => removeConcept(concept.ref)}
              onToggleCampaign={(key) => toggleCampaignKey(concept.ref, key)}
            />
          ))}
        </div>
      )}

      {/* Advanced: copies_per_concept */}
      {values.concepts.length > 0 && (
        <div>
          <div className="font-display text-[10px] tracking-[0.14em] uppercase text-bg-7 mb-3">
            ADVANCED — УНИКАЛИЗАЦИЯ
          </div>
          <div style={{ maxWidth: 200 }}>
            <Input
              label={`Копий на концепт (дефолт ${totalAdsets || "N"})`}
              type="number"
              min={1}
              max={100}
              placeholder={String(totalAdsets || "авто")}
              value={values.copies_per_concept != null ? String(values.copies_per_concept) : ""}
              onChange={(e) => {
                const v = e.target.value === "" ? null : parseInt(e.target.value, 10);
                onChange({ copies_per_concept: v && !isNaN(v) ? v : null });
              }}
              helpText="Пусто = авто (= числу adset'ов)"
            />
          </div>
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
  onRemove: () => void;
  onToggleCampaign: (key: string) => void;
}

const ConceptRow: FC<ConceptRowProps> = ({ concept, campaigns, onRemove, onToggleCampaign }) => {
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

      {/* Привязка к кампаниям (если их > 1) */}
      {campaigns.length > 1 && (
        <div className="flex items-center gap-1.5 shrink-0">
          <span className="text-[10px] text-bg-7 font-display uppercase tracking-wider">
            кампании:
          </span>
          {campaigns.map((c) => {
            const isActive =
              concept.campaign_keys.length === 0 || concept.campaign_keys.includes(c.key);
            return (
              <button
                key={c.key}
                type="button"
                onClick={() => onToggleCampaign(c.key)}
                className={cn(
                  "font-display text-[10px] px-1.5 py-0.5 rounded border transition-colors",
                  isActive
                    ? "bg-accent/15 border-accent/40 text-accent"
                    : "bg-bg-2 border-[var(--hairline)] text-bg-7 hover:border-[var(--hairline-strong)]",
                )}
                aria-pressed={isActive}
                title={`${c.kind} / ${c.adset_count} adsets`}
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
