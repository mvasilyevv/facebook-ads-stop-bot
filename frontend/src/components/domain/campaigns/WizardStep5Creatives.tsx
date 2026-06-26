/**
 * Шаг 5 — Концепты креативов.
 *
 * Drag&drop загрузка файлов → POST /tools/campaigns/upload → upload_id.
 * Привязка концептов к кампаниям — формат «колонки-кампании»: каждая кампания это
 * колонка со своим набором концептов.
 *
 * Модель: concept.campaign_keys — ЯВНЫЙ список кампаний концепта. Пустой массив =
 * концепт не распределён (лежит в пуле «не распределены», не удалён). buildConfig
 * фильтрует концепты по includes(ключ кампании).
 *
 * Быстрое распределение: «Поровну» (round-robin по одной кампании на концепт),
 * «В каждую» (во все кампании), «Очистить» (всё в пул). Точечно: ✕ убирает концепт
 * из кампании (в пул), «+ добавить» / чипы пула — назначают.
 */

import { type FC, useRef, useState } from "react";
import { Upload, X, Film, Image, AlertCircle, Plus, Trash2 } from "lucide-react";
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

// Палитра цветов кампаний (циклическая) — единый цвет кампании в колонке и чипах.
const CAMPAIGN_COLORS = [
  { dot: "bg-blue-400", ring: "border-blue-500/40", soft: "bg-blue-500/[0.07]", chip: "border-blue-500/40 text-blue-300 hover:bg-blue-500/15" },
  { dot: "bg-purple-400", ring: "border-purple-500/40", soft: "bg-purple-500/[0.07]", chip: "border-purple-500/40 text-purple-300 hover:bg-purple-500/15" },
  { dot: "bg-emerald-400", ring: "border-emerald-500/40", soft: "bg-emerald-500/[0.07]", chip: "border-emerald-500/40 text-emerald-300 hover:bg-emerald-500/15" },
  { dot: "bg-amber-400", ring: "border-amber-500/40", soft: "bg-amber-500/[0.07]", chip: "border-amber-500/40 text-amber-300 hover:bg-amber-500/15" },
  { dot: "bg-pink-400", ring: "border-pink-500/40", soft: "bg-pink-500/[0.07]", chip: "border-pink-500/40 text-pink-300 hover:bg-pink-500/15" },
  { dot: "bg-cyan-400", ring: "border-cyan-500/40", soft: "bg-cyan-500/[0.07]", chip: "border-cyan-500/40 text-cyan-300 hover:bg-cyan-500/15" },
];

function campaignColor(index: number) {
  return CAMPAIGN_COLORS[index % CAMPAIGN_COLORS.length]!;
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

  const allKeys = campaigns.map((c) => c.key);
  const colorByKey: Record<string, ReturnType<typeof campaignColor>> = {};
  campaigns.forEach((c, i) => (colorByKey[c.key] = campaignColor(i)));

  const isAttached = (c: UploadedConcept, key: string): boolean =>
    c.campaign_keys.includes(key);

  // Концепт не распределён, если не привязан ни к одной существующей кампании.
  const isUnassigned = (c: UploadedConcept): boolean =>
    !c.campaign_keys.some((k) => allKeys.includes(k));

  const handleFiles = async (files: FileList | File[]) => {
    const arr = Array.from(files);
    if (!arr.length) return;

    setUploading(true);
    setUploadError(null);
    try {
      const result = await uploadConcepts(arr);
      // Новые концепты по умолчанию идут во ВСЕ текущие кампании (явные ключи).
      // Если кампаний нет — пустой список (попадут в пул).
      const newConcepts: UploadedConcept[] = result.concepts.map((c) => ({
        ...c,
        campaign_keys: [...allKeys],
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
  };

  const handleInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files?.length) {
      void handleFiles(e.target.files);
    }
    e.target.value = "";
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragOver(false);
    if (e.dataTransfer.files.length) {
      void handleFiles(e.dataTransfer.files);
    }
  };

  const setKeys = (ref: string, keys: string[]) =>
    onChange({
      concepts: values.concepts.map((c) => (c.ref === ref ? { ...c, campaign_keys: keys } : c)),
    });

  const removeConcept = (ref: string) =>
    onChange({ concepts: values.concepts.filter((c) => c.ref !== ref) });

  const detachFromCampaign = (ref: string, key: string) => {
    const c = values.concepts.find((x) => x.ref === ref);
    if (!c) return;
    setKeys(ref, c.campaign_keys.filter((k) => k !== key));
  };

  const attachToCampaign = (ref: string, key: string) => {
    const c = values.concepts.find((x) => x.ref === ref);
    if (!c) return;
    setKeys(ref, Array.from(new Set([...c.campaign_keys, key])));
  };

  // Быстрое распределение.
  const distributeAll = () =>
    onChange({ concepts: values.concepts.map((c) => ({ ...c, campaign_keys: [...allKeys] })) });

  const distributeEven = () => {
    if (allKeys.length === 0) return;
    onChange({
      concepts: values.concepts.map((c, i) => ({
        ...c,
        campaign_keys: [allKeys[i % allKeys.length]!],
      })),
    });
  };

  const clearAll = () =>
    onChange({ concepts: values.concepts.map((c) => ({ ...c, campaign_keys: [] })) });

  // Сводки.
  const poolImg = values.concepts.filter((c) => !isVideo(c.content_type)).length;
  const poolVid = values.concepts.filter((c) => isVideo(c.content_type)).length;
  const unassigned = values.concepts.filter(isUnassigned);

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

      {/* Тулбар распределения + сводка пула */}
      {values.concepts.length > 0 && campaigns.length > 0 && (
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
          <div className="flex items-center gap-2 text-[12px] text-bg-9">
            <span className="font-display text-[10px] tracking-[0.14em] uppercase text-bg-7">
              Загружено
            </span>
            <span className="text-bg-11 font-medium">{values.concepts.length}</span>
            <span className="text-bg-7">·</span>
            <span>{poolImg} фото</span>
            <span className="text-bg-7">·</span>
            <span>{poolVid} видео</span>
          </div>
          <div className="flex items-center gap-2 ml-auto">
            <span className="text-[11px] text-bg-7">Распределить:</span>
            <DistributeButton onClick={distributeEven}>Поровну</DistributeButton>
            <DistributeButton onClick={distributeAll}>В каждую</DistributeButton>
            <button
              type="button"
              onClick={clearAll}
              className="text-[12px] text-bg-7 hover:text-bg-10 transition-colors px-1"
            >
              Очистить
            </button>
          </div>
        </div>
      )}

      {/* Пул не распределённых концептов */}
      {campaigns.length > 0 && unassigned.length > 0 && (
        <div className="rounded-[var(--radius-3)] border border-dashed border-[var(--hairline-strong)] bg-bg-1 p-3">
          <div className="flex items-center gap-2 mb-2">
            <AlertCircle size={12} className="text-amber-400 shrink-0" />
            <span className="font-display text-[11px] uppercase tracking-wider text-bg-8">
              Не распределены ({unassigned.length})
            </span>
            <span className="text-[11px] text-bg-7">— нажмите кампанию, чтобы добавить</span>
          </div>
          <div className="flex flex-wrap gap-2">
            {unassigned.map((c) => (
              <PoolCard
                key={c.ref}
                concept={c}
                campaigns={campaigns}
                colorByKey={colorByKey}
                onAssign={(key) => attachToCampaign(c.ref, key)}
                onDelete={() => removeConcept(c.ref)}
              />
            ))}
          </div>
        </div>
      )}

      {/* Привязка по кампаниям — колонки */}
      {values.concepts.length > 0 && campaigns.length > 0 && (
        <div
          className="grid gap-3 items-start"
          style={{ gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 280px), 1fr))" }}
        >
          {campaigns.map((campaign) => (
            <CampaignColumn
              key={campaign.key}
              campaign={campaign}
              color={colorByKey[campaign.key]!}
              attached={values.concepts.filter((c) => isAttached(c, campaign.key))}
              pool={values.concepts.filter((c) => !isAttached(c, campaign.key))}
              onDetach={(ref) => detachFromCampaign(ref, campaign.key)}
              onAttach={(ref) => attachToCampaign(ref, campaign.key)}
            />
          ))}
        </div>
      )}

      {/* Нет кампаний — простой пул (распределить можно после шага 4) */}
      {values.concepts.length > 0 && campaigns.length === 0 && (
        <div className="space-y-2">
          <div className="text-[12px] text-bg-8">
            Добавьте кампании на шаге 4, чтобы распределить концепты.
          </div>
          {values.concepts.map((c) => (
            <PoolRow key={c.ref} concept={c} onRemove={() => removeConcept(c.ref)} />
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

// ─── DistributeButton ─────────────────────────────────────────────────────────

const DistributeButton: FC<{ onClick: () => void; children: React.ReactNode }> = ({
  onClick,
  children,
}) => (
  <button
    type="button"
    onClick={onClick}
    className="font-display text-[12px] px-2.5 py-1 rounded-[var(--radius-2)] border border-[var(--hairline-strong)] text-bg-10 hover:border-accent hover:text-accent transition-colors"
  >
    {children}
  </button>
);

// ─── PoolCard — концепт в пуле «не распределены» ──────────────────────────────

interface PoolCardProps {
  concept: UploadedConcept;
  campaigns: CampaignStructure[];
  colorByKey: Record<string, ReturnType<typeof campaignColor>>;
  onAssign: (key: string) => void;
  onDelete: () => void;
}

const PoolCard: FC<PoolCardProps> = ({ concept, campaigns, colorByKey, onAssign, onDelete }) => {
  const video = isVideo(concept.content_type);
  return (
    <div className="flex items-center gap-2 rounded-[var(--radius-2)] border border-[var(--hairline)] bg-bg-2 pl-2 pr-1.5 py-1.5">
      <span
        className={cn(
          "size-6 shrink-0 rounded-[var(--radius-1)] flex items-center justify-center",
          video ? "bg-purple-500/10 text-purple-400" : "bg-blue-500/10 text-blue-400",
        )}
        aria-hidden="true"
      >
        {video ? <Film size={12} /> : <Image size={12} />}
      </span>
      <span
        className="text-[12px] text-bg-11 truncate max-w-[120px]"
        title={concept.original_name}
      >
        {concept.original_name}
      </span>
      {/* Чипы-кампании: клик добавляет концепт в кампанию */}
      <div className="flex items-center gap-1">
        {campaigns.map((c) => {
          const color = colorByKey[c.key]!;
          return (
            <button
              key={c.key}
              type="button"
              onClick={() => onAssign(c.key)}
              aria-label={`Добавить ${concept.original_name} в ${c.key}`}
              title={`Добавить в ${c.key}`}
              className={cn(
                "font-display text-[10px] px-1.5 py-0.5 rounded border bg-bg-1 transition-colors flex items-center gap-1",
                color.chip,
              )}
            >
              <span className={cn("size-1.5 rounded-full", color.dot)} aria-hidden="true" />
              {c.key}
            </button>
          );
        })}
      </div>
      <button
        type="button"
        aria-label={`Удалить ${concept.original_name}`}
        title="Удалить из загрузки"
        onClick={onDelete}
        className="shrink-0 size-5 flex items-center justify-center text-bg-7 hover:text-danger transition-colors"
      >
        <Trash2 size={12} />
      </button>
    </div>
  );
};

// ─── CampaignColumn — колонка одной кампании ──────────────────────────────────

interface CampaignColumnProps {
  campaign: CampaignStructure;
  color: ReturnType<typeof campaignColor>;
  attached: UploadedConcept[];
  pool: UploadedConcept[];
  onDetach: (ref: string) => void;
  onAttach: (ref: string) => void;
}

const CampaignColumn: FC<CampaignColumnProps> = ({
  campaign,
  color,
  attached,
  pool,
  onDetach,
  onAttach,
}) => {
  const [picking, setPicking] = useState(false);

  const img = attached.filter((c) => !isVideo(c.content_type)).length;
  const vid = attached.filter((c) => isVideo(c.content_type)).length;
  const ads = campaign.adset_count * attached.length;

  return (
    <div className={cn("rounded-[var(--radius-3)] border bg-bg-1 flex flex-col", color.ring)}>
      {/* Хедер */}
      <div className={cn("px-3 py-2.5 border-b border-[var(--hairline)] rounded-t-[var(--radius-3)]", color.soft)}>
        <div className="flex items-center gap-2">
          <span className={cn("size-2.5 rounded-full shrink-0", color.dot)} aria-hidden="true" />
          <span className="font-display text-[13px] text-bg-11 truncate">{campaign.key}</span>
          <span className="text-[11px] text-bg-7 shrink-0">· {campaign.adset_count} adset</span>
        </div>
      </div>

      {/* Карточки концептов */}
      <div className="p-2 space-y-1.5">
        {attached.length === 0 ? (
          <div className="flex items-center gap-2 text-[11px] text-amber-400/90 px-1 py-2">
            <AlertCircle size={12} className="shrink-0" />
            Нет концептов — кампания не зальётся
          </div>
        ) : (
          attached.map((c) => (
            <ConceptCard
              key={c.ref}
              concept={c}
              onRemove={() => onDetach(c.ref)}
              removeLabel={`Убрать ${c.original_name} из ${campaign.key}`}
            />
          ))
        )}

        {/* + добавить из пула/других кампаний */}
        {pool.length > 0 && (
          <div>
            <button
              type="button"
              onClick={() => setPicking((v) => !v)}
              aria-expanded={picking}
              className={cn(
                "w-full flex items-center justify-center gap-1.5 rounded-[var(--radius-2)] border border-dashed py-1.5 text-[12px] transition-colors",
                picking
                  ? "border-accent text-accent bg-accent-bg/40"
                  : "border-[var(--hairline-strong)] text-bg-8 hover:text-bg-11 hover:border-accent",
              )}
            >
              <Plus size={13} />
              добавить
            </button>

            {picking && (
              <div className="mt-1.5 space-y-1 rounded-[var(--radius-2)] border border-[var(--hairline)] bg-bg-2 p-1.5">
                <div className="text-[10px] uppercase tracking-wider text-bg-7 px-1 pb-0.5">
                  Доступно ({pool.length})
                </div>
                {pool.map((c) => {
                  const video = isVideo(c.content_type);
                  return (
                    <button
                      key={c.ref}
                      type="button"
                      onClick={() => onAttach(c.ref)}
                      className="w-full flex items-center gap-2 rounded-[var(--radius-1)] px-1.5 py-1 text-left hover:bg-bg-3 transition-colors"
                      title={`Добавить ${c.original_name}`}
                    >
                      <span
                        className={cn(
                          "size-5 shrink-0 rounded-[var(--radius-1)] flex items-center justify-center",
                          video ? "bg-purple-500/10 text-purple-400" : "bg-blue-500/10 text-blue-400",
                        )}
                        aria-hidden="true"
                      >
                        {video ? <Film size={11} /> : <Image size={11} />}
                      </span>
                      <span className="text-[11px] text-bg-10 truncate flex-1">
                        {c.original_name}
                      </span>
                      <Plus size={12} className="text-bg-7 shrink-0" />
                    </button>
                  );
                })}
              </div>
            )}
          </div>
        )}
      </div>

      {/* Футер — итог по кампании */}
      <div className="px-3 py-2 border-t border-[var(--hairline)] mt-auto">
        {attached.length > 0 ? (
          <div className="text-[11px] text-bg-9">
            {img} фото + {vid} видео →{" "}
            <b className="text-bg-11">
              {ads} {adWord(ads)}
            </b>
          </div>
        ) : (
          <div className="text-[11px] text-bg-7">0 объявлений</div>
        )}
      </div>
    </div>
  );
};

// ─── ConceptCard — карточка концепта внутри колонки ───────────────────────────

interface ConceptCardProps {
  concept: UploadedConcept;
  onRemove: () => void;
  removeLabel: string;
}

const ConceptCard: FC<ConceptCardProps> = ({ concept, onRemove, removeLabel }) => {
  const video = isVideo(concept.content_type);
  return (
    <div className="flex items-center gap-2 rounded-[var(--radius-2)] border border-[var(--hairline)] bg-bg-2 px-2 py-1.5">
      <span
        className={cn(
          "size-6 shrink-0 rounded-[var(--radius-1)] flex items-center justify-center",
          video ? "bg-purple-500/10 text-purple-400" : "bg-blue-500/10 text-blue-400",
        )}
        aria-hidden="true"
      >
        {video ? <Film size={12} /> : <Image size={12} />}
      </span>
      <div className="flex-1 min-w-0">
        <div className="text-[12px] text-bg-11 truncate" title={concept.original_name}>
          {concept.original_name}
        </div>
        <div className="text-[10px] text-bg-7">{formatBytes(concept.size_bytes)}</div>
      </div>
      <button
        type="button"
        aria-label={removeLabel}
        title={removeLabel}
        onClick={onRemove}
        className="shrink-0 size-5 flex items-center justify-center text-bg-7 hover:text-danger transition-colors"
      >
        <X size={12} />
      </button>
    </div>
  );
};

// ─── PoolRow — строка концепта без кампаний (нет шага 4) ───────────────────────

const PoolRow: FC<{ concept: UploadedConcept; onRemove: () => void }> = ({ concept, onRemove }) => {
  const video = isVideo(concept.content_type);
  return (
    <div className="border border-[var(--hairline)] rounded-[var(--radius-2)] px-3 py-2.5 bg-bg-1 flex items-center gap-3">
      <div
        className={cn(
          "size-7 shrink-0 rounded-[var(--radius-1)] flex items-center justify-center",
          video ? "bg-purple-500/10 text-purple-400" : "bg-blue-500/10 text-blue-400",
        )}
        aria-hidden="true"
      >
        {video ? <Film size={13} /> : <Image size={13} />}
      </div>
      <div className="flex-1 min-w-0">
        <div className="text-[12px] text-bg-11 truncate" title={concept.original_name}>
          {concept.original_name}
        </div>
        <div className="text-[11px] text-bg-7">{formatBytes(concept.size_bytes)}</div>
      </div>
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
