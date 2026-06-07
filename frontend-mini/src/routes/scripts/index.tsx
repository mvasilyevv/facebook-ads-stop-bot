/**
 * ScriptsPage — выбор папки с креативами, генерация плана кампании.
 * Использует /api/tools/campaign-create/folders (prod-safe).
 */
import { useState } from "react";
import { createFileRoute } from "@tanstack/react-router";
import { MiniHeader } from "@/components/layout/MiniHeader";
import {
  Card,
  Button,
  Badge,
  Skeleton,
  EmptyState,
  ErrorState,
  Sheet,
  Input,
} from "@/components/ui";
import { useScriptFolders, useScriptPlan, type ScriptFolder, type ScriptPlan } from "@/lib/api";
import { haptic } from "@/lib/tg";

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
      });
      haptic.notify("success");
      onResult(plan);
    } catch (err) {
      haptic.notify("error");
      setError((err as Error).message);
    }
  }

  return (
    <form onSubmit={(e) => void handleBuild(e)} className="flex flex-col gap-4 pb-4">
      <div className="bg-[var(--color-bg-2)] border border-[var(--color-bg-5)] p-3">
        <p className="text-[11px] uppercase tracking-[0.07em] text-[var(--color-bg-9)] font-mono">
          Папка
        </p>
        <p className="text-[14px] font-semibold font-mono text-[var(--color-bg-11)] mt-1">
          {folder.name}
        </p>
        <p className="text-[12px] text-[var(--color-bg-8)] mt-0.5">
          {folder.adset_count} адсетов · {folder.creative_count} креативов · {folder.media_type}
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
        label="Sub2 (опционально)"
        placeholder="MV"
        value={sub2}
        onChange={(e) => setSub2(e.target.value)}
      />
      {error && (
        <p className="text-[12px] text-[var(--color-danger)]">{error}</p>
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
  async function copyToClipboard(text: string) {
    try {
      await navigator.clipboard.writeText(text);
      haptic.notify("success");
    } catch {
      haptic.notify("error");
    }
  }

  return (
    <div className="flex flex-col gap-4 pb-4">
      {/* Заголовок */}
      <div>
        <p className="text-[11px] uppercase tracking-[0.07em] text-[var(--color-bg-9)] font-mono mb-1">
          Имя кампании
        </p>
        <button
          type="button"
          onClick={() => void copyToClipboard(plan.campaign_name)}
          className="text-[14px] font-mono text-[var(--color-accent)] text-left break-all active:opacity-70"
        >
          {plan.campaign_name}
        </button>
      </div>

      {/* Мета */}
      <div className="grid grid-cols-3 gap-2">
        <div className="bg-[var(--color-bg-2)] border border-[var(--color-bg-5)] p-2 text-center">
          <p className="text-[18px] font-semibold font-display text-[var(--color-bg-11)] tabular-nums">
            {plan.adset_count}
          </p>
          <p className="text-[10px] text-[var(--color-bg-8)] uppercase tracking-wide">Адсетов</p>
        </div>
        <div className="bg-[var(--color-bg-2)] border border-[var(--color-bg-5)] p-2 text-center">
          <p className="text-[18px] font-semibold font-display text-[var(--color-bg-11)] tabular-nums">
            {plan.ad_count}
          </p>
          <p className="text-[10px] text-[var(--color-bg-8)] uppercase tracking-wide">Объявлений</p>
        </div>
        <div className="bg-[var(--color-bg-2)] border border-[var(--color-bg-5)] p-2 text-center">
          <p className="text-[12px] font-mono font-semibold text-[var(--color-bg-11)]">
            {plan.media_type}
          </p>
          <p className="text-[10px] text-[var(--color-bg-8)] uppercase tracking-wide">Медиа</p>
        </div>
      </div>

      {/* Ручной гайд */}
      {plan.manual_guide.map((section) => (
        <Card key={section.title} eyebrow="Ручной гайд" title={section.title} padding="sm">
          <div className="flex flex-col gap-2 mt-2">
            {section.items.map((item) => (
              <div key={item.label} className="flex items-start gap-2">
                <span className="text-[11px] text-[var(--color-bg-8)] uppercase tracking-wide w-24 shrink-0 pt-0.5">
                  {item.label}
                </span>
                {item.copyable ? (
                  <button
                    type="button"
                    onClick={() => void copyToClipboard(item.value)}
                    className="text-[12px] font-mono text-[var(--color-accent)] text-left break-all active:opacity-70 flex-1"
                  >
                    {item.value}
                  </button>
                ) : (
                  <span className="text-[12px] font-mono text-[var(--color-bg-11)] break-all flex-1">
                    {item.value}
                  </span>
                )}
              </div>
            ))}
          </div>
        </Card>
      ))}

      {/* Safety notes */}
      {plan.safety_notes.length > 0 && (
        <Card eyebrow="Важно" padding="sm">
          <ul className="flex flex-col gap-1">
            {plan.safety_notes.map((note, i) => (
              <li key={i} className="text-[12px] text-[var(--color-warning)] flex gap-2">
                <span aria-hidden>⚠</span>
                <span>{note}</span>
              </li>
            ))}
          </ul>
        </Card>
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

  return (
    <div className="flex flex-col min-h-full pb-20">
      <MiniHeader
        eyebrow="Автоматизация"
        title="Создание кампании"
        right={
          <Button
            size="sm"
            variant="ghost"
            onClick={() => void refetch()}
            disabled={isLoading}
          >
            ↺
          </Button>
        }
      />

      <div className="p-4 flex flex-col gap-3">
        <p className="text-[12px] text-[var(--color-bg-9)]">
          Выберите папку с креативами для построения плана создания кампании.
        </p>

        {isLoading && (
          <>
            {Array.from({ length: 3 }, (_, i) => <Skeleton key={i} className="h-16" />)}
          </>
        )}
        {isError && (
          <ErrorState
            message="Не удалось загрузить папки"
            onRetry={() => void refetch()}
          />
        )}
        {!isLoading && !isError && (folders ?? []).length === 0 && (
          <EmptyState
            title="Папок с креативами нет"
            description="Скопируйте папку с креативами в ~/Documents/FB_Agent_Creo"
          />
        )}
        {!isLoading &&
          !isError &&
          (folders ?? []).map((folder) => (
            <Card
              key={folder.path}
              padding="sm"
              onClick={() => openFolder(folder)}
              className="cursor-pointer active:opacity-70"
            >
              <div className="flex items-start justify-between gap-2">
                <div className="flex-1 min-w-0">
                  <p className="text-[13px] font-semibold font-mono text-[var(--color-bg-11)] truncate">
                    {folder.name}
                  </p>
                  <p className="text-[11px] text-[var(--color-bg-8)] mt-0.5">
                    {folder.adset_count} адс · {folder.creative_count} крео · {folder.media_type}
                  </p>
                </div>
                <div className="flex flex-col items-end gap-1 shrink-0">
                  {!folder.is_valid && (
                    <Badge variant="failed">Ошибка</Badge>
                  )}
                  {folder.is_valid && (
                    <Badge variant="normal">Готова</Badge>
                  )}
                </div>
              </div>
              {!folder.is_valid && folder.validation_error && (
                <p className="text-[11px] text-[var(--color-danger)] mt-2">
                  {folder.validation_error}
                </p>
              )}
            </Card>
          ))}
      </div>

      {/* Bottom sheet: форма / результат плана */}
      <Sheet
        open={sheetOpen}
        onClose={handleClose}
        eyebrow={plan ? "Готов" : "Параметры"}
        title={plan ? plan.campaign_name : `Папка: ${selected?.name ?? ""}`}
        className="max-h-[90vh] overflow-y-auto"
      >
        {selected && !plan && (
          <PlanForm
            folder={selected}
            onResult={handlePlanResult}
            onClose={handleClose}
          />
        )}
        {plan && <PlanResult plan={plan} />}
        {plan && (
          <div className="mt-2 mb-4">
            <Button variant="secondary" fullWidth onClick={() => setPlan(null)}>
              Изменить параметры
            </Button>
          </div>
        )}
      </Sheet>
    </div>
  );
}
