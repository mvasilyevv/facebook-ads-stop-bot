/**
 * Helper для теста ScriptsPage — обёртка с QueryClient.
 * Зеркалит структуру routes/scripts/index.tsx (кнопки-строки, Sheet, формы).
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MiniHeader } from "@/components/layout/MiniHeader";
import { Button, Badge, Skeleton, EmptyState, Sheet, Input } from "@/components/ui";
import { useScriptFolders, useScriptPlan, type ScriptFolder, type ScriptPlan } from "@/lib/api";
import { haptic } from "@/lib/tg";
import { useState } from "react";

function PlanForm({
  folder,
  onResult,
  onClose,
}: {
  folder: ScriptFolder;
  onResult: (plan: ScriptPlan) => void;
  onClose: () => void;
}) {
  const buildPlan = useScriptPlan();
  const [offerCode, setOfferCode] = useState("");
  const [countryName, setCountryName] = useState("");
  const [cabinetId, setCabinetId] = useState("");
  const [sub2, setSub2] = useState("MV");
  const [error, setError] = useState<string | null>(null);

  async function handleBuild(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (!offerCode.trim() || !countryName.trim() || !cabinetId.trim()) {
      setError("Заполните код оффера, страну и ID кабинета");
      return;
    }
    try {
      const plan = await buildPlan.mutateAsync({
        offer_code: offerCode.trim().toUpperCase(),
        offer_country_name: countryName.trim(),
        cabinet_id: cabinetId.trim(),
        sub2: sub2 || "MV",
        folder_name: folder.name,
      });
      haptic.notify("success");
      onResult(plan);
    } catch (err) {
      setError((err as Error).message);
    }
  }

  return (
    <form onSubmit={(e) => void handleBuild(e)} className="flex flex-col gap-4 px-4 pb-6">
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
      {error !== null && <p className="text-[12px] text-danger">{error}</p>}
      <Button type="submit" loading={buildPlan.isPending} fullWidth>
        Построить план
      </Button>
      <Button type="button" variant="ghost" fullWidth onClick={onClose}>
        Отмена
      </Button>
    </form>
  );
}

function PlanResult({ plan }: { plan: ScriptPlan }) {
  return (
    <div className="flex flex-col gap-4 px-4 pb-6">
      <p className="font-display text-[14px] text-accent">{plan.campaign_name}</p>
      {plan.manual_guide.map((section) => (
        <div key={section.title} className="border border-bg-5 bg-bg-1">
          <p className="text-[11px] uppercase tracking-[0.08em] text-bg-8 px-3 pt-2">
            {section.title}
          </p>
          {section.items.map((item) => (
            <div key={item.label} className="flex gap-2 px-3 py-2">
              <span className="text-[11px] text-bg-8">{item.label}</span>
              <span className="text-[12px] font-display">{item.value}</span>
            </div>
          ))}
        </div>
      ))}
      {plan.safety_notes.map((note, i) => (
        <p key={i} className="text-[12px] text-warning">
          {note}
        </p>
      ))}
    </div>
  );
}

function TestScriptsPage() {
  const { data: folders, isLoading, isError, refetch } = useScriptFolders();
  const [selected, setSelected] = useState<ScriptFolder | null>(null);
  const [plan, setPlan] = useState<ScriptPlan | null>(null);
  const [sheetOpen, setSheetOpen] = useState(false);

  const folderList = folders ?? [];

  return (
    <div>
      <MiniHeader
        eyebrow="ИНСТРУМЕНТЫ · СКРИПТЫ"
        title="Скрипты"
        right={
          <button
            type="button"
            aria-label="Обновить список"
            onClick={() => void refetch()}
            disabled={isLoading}
            className="w-11 h-11"
          >
            ↺
          </button>
        }
      />

      <div className="flex flex-col gap-3 p-4">
        {isLoading &&
          Array.from({ length: 3 }, (_, i) => <Skeleton key={i} className="h-[72px]" />)}

        {isError && !isLoading && (
          <EmptyState
            title="Не удалось загрузить папки"
            description="Проверьте соединение и повторите"
          />
        )}

        {!isLoading && !isError && folderList.length === 0 && (
          <EmptyState
            title="Папок с креативами нет"
            description="Скопируйте папку с креативами в ~/Documents/FB_Agent_Creo"
          />
        )}

        {!isLoading &&
          !isError &&
          folderList.map((folder) => (
            <button
              key={folder.path}
              type="button"
              className="w-full text-left bg-bg-1 px-3.5 py-3 min-h-[44px] flex items-start justify-between gap-3"
              onClick={() => {
                setSelected(folder);
                setPlan(null);
                setSheetOpen(true);
              }}
            >
              <div className="flex-1 min-w-0">
                <p className="font-display text-[13px] text-bg-11 truncate">{folder.name}</p>
                <p className="font-display text-[11px] text-bg-9 mt-0.5">
                  {folder.adset_count} адс · {folder.creative_count} крео · {folder.media_type}
                </p>
                {!folder.is_valid && folder.validation_error !== "" && (
                  <p className="text-[11px] text-danger mt-1">{folder.validation_error}</p>
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

      <Sheet
        open={sheetOpen}
        onClose={() => setSheetOpen(false)}
        eyebrow={plan !== null ? "РЕЗУЛЬТАТ" : "ПАРАМЕТРЫ"}
        title={
          plan !== null
            ? plan.campaign_name
            : selected !== null
              ? selected.name
              : ""
        }
      >
        {selected !== null && plan === null && (
          <PlanForm
            folder={selected}
            onResult={(p) => setPlan(p)}
            onClose={() => setSheetOpen(false)}
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

const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });

export default function ScriptsTestWrapper() {
  return (
    <QueryClientProvider client={qc}>
      <TestScriptsPage />
    </QueryClientProvider>
  );
}
