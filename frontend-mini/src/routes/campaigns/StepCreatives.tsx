/**
 * StepCreatives — шаг 5 визарда: загрузка концептов.
 * file input (выбор с телефона) → POST /tools/campaigns/upload → показываем список.
 * Тач ≥ 44px, поддержка мультивыбора.
 */
import { useRef, useState } from "react";
import { Upload, X, Image as ImageIcon, Film } from "lucide-react";
import { Button, Skeleton } from "@/components/ui";
import { Eyebrow } from "@/components/data";
import { haptic } from "@/lib/tg";
import { useUploadConcepts } from "@/lib/api";
import type { UploadedConcept } from "@/lib/campaignTypes";
import { useWizardStore } from "./-wizardStore";
import { cn } from "@/lib/cn";

function formatBytes(n: number): string {
  if (n < 1024) return `${n} Б`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} КБ`;
  return `${(n / 1024 / 1024).toFixed(1)} МБ`;
}

function ConceptItem({
  concept,
  onRemove,
}: {
  concept: UploadedConcept;
  onRemove?: () => void;
}) {
  const isVideo = concept.content_type?.startsWith("video") ?? false;
  const Icon = isVideo ? Film : ImageIcon;
  return (
    <div className="flex items-center gap-3 px-3.5 py-3 min-h-[52px]">
      <Icon size={18} strokeWidth={1.5} className="text-bg-8 shrink-0" aria-hidden />
      <div className="flex-1 min-w-0">
        <p className="font-display text-[13px] text-bg-11 truncate leading-snug">
          {concept.original_name}
        </p>
        <p className="font-display tabular-nums text-[11px] text-bg-8 mt-0.5">
          {formatBytes(concept.size_bytes)}
          {concept.content_type ? ` · ${concept.content_type.split("/")[1]}` : ""}
        </p>
      </div>
      {onRemove && (
        <button
          type="button"
          aria-label={`Убрать ${concept.original_name}`}
          onClick={onRemove}
          className="shrink-0 inline-flex items-center justify-center w-8 h-8 text-bg-7 active:text-danger"
        >
          <X size={15} strokeWidth={2} aria-hidden />
        </button>
      )}
    </div>
  );
}

export function StepCreatives() {
  const { uploadId, concepts, setUpload, updateConfig, nextStep, prevStep } = useWizardStore();
  const upload = useUploadConcepts();
  const fileRef = useRef<HTMLInputElement>(null);
  const [error, setError] = useState<string | null>(null);
  const [localConcepts, setLocalConcepts] = useState<UploadedConcept[]>(concepts);
  const [localUploadId, setLocalUploadId] = useState<string | null>(uploadId);

  async function handleFiles(files: FileList | null) {
    if (!files || files.length === 0) return;
    setError(null);
    haptic.impact("light");

    const formData = new FormData();
    for (const f of files) {
      formData.append("files", f);
    }

    try {
      const result = await upload.mutateAsync(formData);
      setLocalUploadId(result.upload_id);
      setLocalConcepts(result.concepts);
      setUpload(result.upload_id, result.concepts);
      haptic.notify("success");
    } catch (err) {
      haptic.notify("error");
      setError((err as Error).message);
    }
  }

  function removeConcept(ref: string) {
    haptic.selection();
    setLocalConcepts((prev) => prev.filter((c) => c.ref !== ref));
  }

  function handleNext() {
    setError(null);
    if (!localUploadId) {
      setError("Загрузите хотя бы один концепт");
      return;
    }
    if (localConcepts.length === 0) {
      setError("Нет концептов для загрузки");
      return;
    }
    updateConfig({ creo_root: localUploadId });
    nextStep();
  }

  return (
    <div className="flex flex-col gap-4 p-4 pb-8">
      <Eyebrow num="05">КОНЦЕПТЫ КРЕАТИВОВ</Eyebrow>

      {/* Зона выбора файлов */}
      <input
        ref={fileRef}
        type="file"
        multiple
        accept="image/*,video/*"
        className="sr-only"
        aria-label="Выбрать файлы концептов"
        onChange={(e) => void handleFiles(e.target.files)}
      />

      <button
        type="button"
        onClick={() => fileRef.current?.click()}
        disabled={upload.isPending}
        className={cn(
          "w-full min-h-[100px] flex flex-col items-center justify-center gap-2",
          "border border-dashed border-[var(--hairline)] rounded-[var(--radius-3)]",
          "text-bg-8 active:bg-bg-2",
          upload.isPending && "opacity-40 cursor-not-allowed",
        )}
      >
        <Upload size={24} strokeWidth={1.4} aria-hidden />
        <p className="text-[13px] text-bg-9">
          {upload.isPending ? "Загрузка..." : "Выбрать файлы"}
        </p>
        <p className="text-[11px] text-bg-7">Изображения и видео (мультивыбор)</p>
      </button>

      {/* Прогресс загрузки */}
      {upload.isPending && (
        <div className="flex flex-col gap-2">
          <Skeleton className="h-[52px]" />
          <Skeleton className="h-[52px]" />
        </div>
      )}

      {/* Список загруженных */}
      {!upload.isPending && localConcepts.length > 0 && (
        <div className="border border-[var(--hairline)] divide-y divide-[var(--hairline)] rounded-[var(--radius-3)] overflow-hidden">
          {localConcepts.map((c) => (
            <ConceptItem
              key={c.ref}
              concept={c}
              onRemove={() => removeConcept(c.ref)}
            />
          ))}
        </div>
      )}

      {/* upload_id как hint */}
      {localUploadId && (
        <div className="border border-[var(--hairline)] bg-bg-1 px-3.5 py-2.5 rounded-[var(--radius-2)]">
          <p className="text-[10px] uppercase tracking-[0.08em] text-bg-7 mb-1">UPLOAD ID</p>
          <p className="font-mono text-[12px] text-bg-9 truncate">{localUploadId}</p>
        </div>
      )}

      {error !== null && (
        <p className="text-[12px] text-[var(--color-danger)]">{error}</p>
      )}

      {/* Кнопки */}
      <div className="flex flex-col gap-3 mt-2">
        <Button fullWidth onClick={handleNext} disabled={localConcepts.length === 0}>
          Далее ({localConcepts.length} концептов)
        </Button>
        <Button variant="ghost" fullWidth onClick={() => { haptic.selection(); prevStep(); }}>
          Назад
        </Button>
      </div>
    </div>
  );
}
