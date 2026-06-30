/**
 * CreativeUploadZone — drag&drop загрузка концептов креативов на шаге 5 визарда.
 *
 * POST /tools/campaigns/upload → upload_id + concepts. Новые концепты по умолчанию
 * привязываются к ВСЕМ текущим кампаниям (явные ключи campaign_keys); если кампаний
 * нет — пустой список (попадут в пул нераспределённых).
 *
 * Выделено из WizardStep5Creatives.tsx (было >600 строк в одном файле — god-component).
 */
import { useRef, useState, type FC } from "react";
import { Upload, AlertCircle } from "lucide-react";
import { cn } from "@/lib/utils/cn";
import { Spinner } from "@/components/ui/Spinner";
import { uploadConcepts } from "@/lib/api/campaigns";
import type { UploadedConcept } from "@/stores/campaignWizard";

interface CreativeUploadZoneProps {
  /** Ключи всех текущих кампаний — новые концепты привязываются к ним по умолчанию. */
  allCampaignKeys: string[];
  onUploaded: (uploadId: string, newConcepts: UploadedConcept[]) => void;
}

export const CreativeUploadZone: FC<CreativeUploadZoneProps> = ({
  allCampaignKeys,
  onUploaded,
}) => {
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [isDragOver, setIsDragOver] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

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
        campaign_keys: [...allCampaignKeys],
      }));
      onUploaded(result.upload_id, newConcepts);
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

  return (
    <>
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

      {/* Fragment не оборачивает в DOM-узел — родительский space-y-6 видит этот блок
          как обычного следующего сиблинга и сам выставляет отступ, как раньше. */}
      {uploadError && (
        <div
          role="alert"
          className="flex items-center gap-2 text-[12px] text-danger bg-danger/10 border border-danger/30 rounded-[var(--radius-2)] px-3 py-2"
        >
          <AlertCircle size={13} className="shrink-0" />
          {uploadError}
        </div>
      )}
    </>
  );
};
