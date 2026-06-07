/**
 * ScriptsPage — выбор папки с креативами, генерация плана кампании.
 * Вторичный экран («Ещё»). Канон: Dashboard эталон (MiniHeader, Eyebrow,
 * bg-bg-1 border-bg-5, Skeleton, EmptyState, bottom-sheet Sheet).
 */
import { useState } from "react";
import { createFileRoute } from "@tanstack/react-router";
import { Copy, AlertTriangle } from "lucide-react";
import { MiniHeader } from "@/components/layout/MiniHeader";
import { Eyebrow } from "@/components/data";
import {
  Button,
  Badge,
  Input,
  Sheet,
  Skeleton,
  EmptyState,
} from "@/components/ui";
import {
  useScriptFolders,
  useScriptPlan,
  type ScriptFolder,
  type ScriptPlan,
} from "@/lib/api";
import { haptic } from "@/lib/tg";
import { cn } from "@/lib/cn";

export const Route = createFileRoute("/scripts/")({
  component: ScriptsPage,
});

// ─── Форма параметров плана ───────────────────────────────────────────────

interface PlanFormProps {
  folder: ScriptFolder;
  onResult: (plan: ScriptPlan) => void;
  onClose: () => void;
}

function PlanForm({ folder, onResult, onClose }: PlanFormProps) {
  const buildPlan = useScriptPlan();

  const [offerCode, setOfferCode] = useState("");
  const [countryName, setCountryName] = useState("");
  const [cabinetId, setCabinetId] = useState("");
  const [sub2, setSub2] = useState("MV");
  const [genDate, setGenDate] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function handleBuild(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    haptic.impact("medium");

    if (!offerCode.trim() || !countryName.trim() || !cabinetId.trim()) {
      setError("Заполните код оффера, страну и ID кабинета");
      return;
    }

    try {
      const plan = await buildPlan.mutateAsync({
        offer_code: offerCode.trim().toUpperCase(),
        offer_country_name: countryName.trim(),
        cabinet_id: cabinetId.trim(),
        sub2: sub2.trim() || "MV",
        folder_name: folder.name,
        generation_date: genDate.trim() || null,
      });
      haptic.notify("success");
      onResult(plan);
    } catch (err) {
      haptic.notify("error");
      setError((err as Error).message);
    }
  }

  return (
    <form onSubmit={(e) => void handleBuild(e)} className="flex flex-col gap-4 px-4 pb-6">
      {/* Выбранная папка */}
      <div className="border border-bg-5 bg-bg-1 p-3">
        <Eyebrow>ПАПКА</Eyebrow>
        <p className="font-display text-[14px] text-bg-11 mt-1 truncate">{folder.name}</p>
        <p className="font-display tabular-nums text-[12px] text-bg-9 mt-0.5">
          {folder.adset_count} адс · {folder.creative_count} крео · {folder.media_type}
        </p>
      </div>

      <Input
        label="Код оффера"
        placeholder="GH_AVI"
        value={offerCode}
        onChange={(e) => setOfferCode(e.target.value.toUpperCase())}
      />
      <Input
        label="Страна"
        placeholder="Ghana"
        value={countryName}
        onChange={(e) => setCountryName(e.target.value)}
      />
      <Input
        label="ID рекламного кабинета"
        placeholder="act_12345678"
        value={cabinetId}
        onChange={(e) => setCabinetId(e.target.value)}
      />
      <Input
        label="sub2 (опционально)"
        placeholder="MV"
        value={sub2}
        onChange={(e) => setSub2(e.target.value)}
      />
      <Input
        label="Дата генерации (опционально)"
        placeholder="2026-06-08"
        value={genDate}
        onChange={(e) => setGenDate(e.target.value)}
      />

      {error !== null && (
        <p className="text-[12px] text-danger">{error}</p>
      )}

      <Button type="submit" loading={buildPlan.isPending} fullWidth>
        Построить план
      </Button>
      <Button type="button" variant="ghost" fullWidth onClick={onClose}>
        Отмена
      </Button>
    </form>
  );
}

// ─── Результат плана ──────────────────────────────────────────────────────

function PlanResult({ plan }: { plan: ScriptPlan }) {
  const [copied, setCopied] = useState<string | null>(null);

  async function copyValue(text: string) {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(text);
      haptic.notify("success");
      setTimeout(() => setCopied(null), 2000);
    } catch {
      haptic.notify("error");
    }
  }

  return (
    <div className="flex flex-col gap-4 px-4 pb-6">
      {/* Ручной гайд */}
      {plan.manual_guide.map((section) => (
        <section key={section.title}>
          <div className="flex items-center gap-2 mb-2">
            <Eyebrow>{section.title.toUpperCase()}</Eyebrow>
          </div>
          <div className="border border-bg-5 bg-bg-1 divide-y divide-bg-5">
            {section.items.map((item) => (
              <div
                key={item.label}
                className="flex items-start justify-between gap-3 px-3 py-2.5 min-h-[44px]"
              >
                <div className="flex-1 min-w-0">
                  <p className="text-[10px] uppercase tracking-[0.08em] text-bg-8 mb-0.5">
                    {item.label}
                  </p>
                  <p
                    className={cn(
                      "font-display tabular-nums text-[13px] break-all",
                      item.copyable ? "text-accent" : "text-bg-11",
                    )}
                  >
                    {item.value}
                  </p>
                </div>
                {item.copyable && (
                  <button
                    type="button"
                    aria-label={`Скопировать ${item.label}`}
                    onClick={() => void copyValue(item.value)}
                    className={cn(
                      "shrink-0 inline-flex items-center justify-center w-8 h-8 mt-0.5 border",
                      copied === item.value
                        ? "border-success text-success bg-success-bg"
                        : "border-bg-5 text-bg-9 active:bg-bg-2",
                    )}
                  >
                    <Copy size={14} strokeWidth={1.8} />
                  </button>
                )}
              </div>
            ))}
          </div>
        </section>
      ))}

      {/* Safety notes */}
      {plan.safety_notes.length > 0 && (
        <section>
          <Eyebrow className="mb-2">ВАЖНО</Eyebrow>
          <div className="border border-warning bg-warning-bg p-3 flex flex-col gap-2">
            {plan.safety_notes.map((note, i) => (
              <div key={i} className="flex items-start gap-2">
                <AlertTriangle
                  size={13}
                  strokeWidth={1.8}
                  className="text-warning shrink-0 mt-0.5"
                  aria-hidden
                />
                <span className="text-[12px] text-warning leading-snug">{note}</span>
              </div>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}

// ─── ScriptsPage ──────────────────────────────────────────────────────────

function ScriptsPage() {
  const { data: folders, isLoading, isError, refetch } = useScriptFolders();
  const [selected, setSelected] = useState<ScriptFolder | null>(null);
  const [plan, setPlan] = useState<ScriptPlan | null>(null);
  const [sheetOpen, setSheetOpen] = useState(false);

  function openFolder(folder: ScriptFolder) {
    setSelected(folder);
    setPlan(null);
    setSheetOpen(true);
    haptic.selection();
  }

  function handleClose() {
    setSheetOpen(false);
  }

  function handlePlanResult(p: ScriptPlan) {
    setPlan(p);
  }

  const folderList = folders ?? [];

  return (
    <div className="flex flex-col min-h-full pb-20">
      {/* Шапка */}
      <MiniHeader
        eyebrow="ИНСТРУМЕНТЫ · СКРИПТЫ"
        title="Скрипты"
        right={
          <button
            type="button"
            aria-label="Обновить список"
            onClick={() => { haptic.selection(); void refetch(); }}
            disabled={isLoading}
            className="inline-flex items-center justify-center w-11 h-11 text-bg-9 active:text-bg-11 disabled:opacity-40"
          >
            <span className="font-display text-[16px]">↺</span>
          </button>
        }
      />

      <div className="flex flex-col gap-3 p-4">
        <Eyebrow num="01">ПАПКИ С КРЕАТИВАМИ</Eyebrow>

        {/* Loading */}
        {isLoading && (
          <div className="flex flex-col gap-3 mt-1">
            {Array.from({ length: 3 }, (_, i) => (
              <Skeleton key={i} className="h-[72px]" />
            ))}
          </div>
        )}

        {/* Ошибка */}
        {isError && !isLoading && (
          <EmptyState
            title="Не удалось загрузить папки"
            description="Проверьте соединение и повторите"
          />
        )}

        {/* Пусто */}
        {!isLoading && !isError && folderList.length === 0 && (
          <EmptyState
            title="Папок с креативами нет"
            description="Скопируйте папку с креативами в ~/Documents/FB_Agent_Creo"
          />
        )}

        {/* Список папок */}
        {!isLoading && !isError && folderList.length > 0 && (
          <div className="flex flex-col gap-0 border border-bg-5 divide-y divide-bg-5">
            {folderList.map((folder) => (
              <button
                key={folder.path}
                type="button"
                className="w-full text-left bg-bg-1 px-3.5 py-3 min-h-[44px] flex items-start justify-between gap-3 active:bg-bg-2"
                onClick={() => openFolder(folder)}
              >
                <div className="flex-1 min-w-0">
                  <p className="font-display text-[13px] text-bg-11 truncate leading-snug">
                    {folder.name}
                  </p>
                  <p className="font-display tabular-nums text-[11px] text-bg-9 mt-0.5">
                    {folder.adset_count} адс · {folder.creative_count} крео · {folder.media_type}
                  </p>
                  {!folder.is_valid && folder.validation_error !== "" && (
                    <p className="text-[11px] text-danger mt-1 leading-snug">
                      {folder.validation_error}
                    </p>
                  )}
                </div>
                <div className="shrink-0 mt-0.5">
                  {folder.is_valid ? (
                    <Badge variant="done">готова</Badge>
                  ) : (
                    <Badge variant="failed">ошибка</Badge>
                  )}
                </div>
              </button>
            ))}
          </div>
        )}
      </div>

      {/* Bottom-sheet: форма или результат плана */}
      <Sheet
        open={sheetOpen}
        onClose={handleClose}
        eyebrow={plan !== null ? "РЕЗУЛЬТАТ" : "ПАРАМЕТРЫ"}
        title={
          plan !== null
            ? plan.campaign_name
            : selected !== null
              ? selected.name
              : ""
        }
        className="max-h-[92vh] overflow-y-auto"
      >
        {selected !== null && plan === null && (
          <PlanForm
            folder={selected}
            onResult={handlePlanResult}
            onClose={handleClose}
          />
        )}
        {plan !== null && (
          <>
            <PlanResult plan={plan} />
            <div className="px-4 pb-6">
              <Button variant="secondary" fullWidth onClick={() => setPlan(null)}>
                Изменить параметры
              </Button>
            </div>
          </>
        )}
      </Sheet>
    </div>
  );
}
