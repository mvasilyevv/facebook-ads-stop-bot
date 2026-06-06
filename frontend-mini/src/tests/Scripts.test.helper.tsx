/**
 * Helper для теста ScriptsPage — обёртка с QueryClient.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MiniHeader } from "@/components/layout/MiniHeader";
import { Card, Button, Badge, Skeleton, EmptyState, ErrorState, Sheet, Input } from "@/components/ui";
import { useScriptFolders, useScriptPlan, type ScriptFolder, type ScriptPlan } from "@/lib/api";
import { haptic } from "@/lib/tg";
import { useState } from "react";

function PlanForm({ folder, onResult, onClose }: {
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
      setError("Заполните все поля"); return;
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
    } catch (err) { setError((err as Error).message); }
  }

  return (
    <form onSubmit={(e) => void handleBuild(e)} className="flex flex-col gap-4 pb-4">
      <Input label="Код оффера" placeholder="GH_AVI" value={offerCode}
        onChange={(e) => setOfferCode(e.target.value.toUpperCase())} />
      <Input label="Страна" placeholder="Ghana" value={countryName}
        onChange={(e) => setCountryName(e.target.value)} />
      <Input label="ID рекламного кабинета" placeholder="act_12345678" value={cabinetId}
        onChange={(e) => setCabinetId(e.target.value)} />
      <Input label="Sub2 (опционально)" placeholder="MV" value={sub2}
        onChange={(e) => setSub2(e.target.value)} />
      {error && <p className="text-[12px] text-[var(--color-danger)]">{error}</p>}
      <Button type="submit" loading={buildPlan.isPending} fullWidth>Построить план</Button>
      <Button type="button" variant="ghost" fullWidth onClick={onClose}>Отмена</Button>
    </form>
  );
}

function PlanResult({ plan }: { plan: ScriptPlan }) {
  return (
    <div className="flex flex-col gap-4 pb-4">
      <p className="text-[14px] font-mono text-[var(--color-accent)]">{plan.campaign_name}</p>
      {plan.manual_guide.map((section) => (
        <Card key={section.title} title={section.title} padding="sm">
          {section.items.map((item) => (
            <div key={item.label} className="flex gap-2">
              <span className="text-[11px] text-[var(--color-bg-8)]">{item.label}</span>
              <span className="text-[12px] font-mono">{item.value}</span>
            </div>
          ))}
        </Card>
      ))}
      {plan.safety_notes.map((note, i) => (
        <p key={i} className="text-[12px] text-[var(--color-warning)]">{note}</p>
      ))}
    </div>
  );
}

function TestScriptsPage() {
  const { data: folders, isLoading, isError, refetch } = useScriptFolders();
  const [selected, setSelected] = useState<ScriptFolder | null>(null);
  const [plan, setPlan] = useState<ScriptPlan | null>(null);
  const [sheetOpen, setSheetOpen] = useState(false);

  return (
    <div>
      <MiniHeader eyebrow="Автоматизация" title="Создание кампании"
        right={<Button size="sm" variant="ghost" onClick={() => void refetch()}>↺</Button>}
      />
      <div className="p-4 flex flex-col gap-3">
        {isLoading && Array.from({ length: 3 }, (_, i) => <Skeleton key={i} className="h-16" />)}
        {isError && <ErrorState message="Не удалось загрузить папки" onRetry={() => void refetch()} />}
        {!isLoading && !isError && (folders ?? []).length === 0 && (
          <EmptyState title="Папок с креативами нет" description="Скопируйте папку в ~/Documents/FB_Agent_Creo" />
        )}
        {!isLoading && !isError && (folders ?? []).map((folder) => (
          <Card key={folder.path} padding="sm" onClick={() => { setSelected(folder); setPlan(null); setSheetOpen(true); }}
            className="cursor-pointer">
            <div className="flex items-start justify-between gap-2">
              <div className="flex-1 min-w-0">
                <p className="text-[13px] font-semibold font-mono">{folder.name}</p>
                <p className="text-[11px] text-[var(--color-bg-8)]">
                  {folder.adset_count} адс · {folder.creative_count} крео
                </p>
              </div>
              <Badge variant={folder.is_valid ? "normal" : "failed"}>
                {folder.is_valid ? "Готова" : "Ошибка"}
              </Badge>
            </div>
            {!folder.is_valid && folder.validation_error && (
              <p className="text-[11px] text-[var(--color-danger)] mt-2">{folder.validation_error}</p>
            )}
          </Card>
        ))}
      </div>
      <Sheet open={sheetOpen} onClose={() => setSheetOpen(false)}
        eyebrow={plan ? "Готов" : "Параметры"}
        title={plan ? plan.campaign_name : `Папка: ${selected?.name ?? ""}`}
      >
        {selected && !plan && (
          <PlanForm folder={selected} onResult={(p) => setPlan(p)} onClose={() => setSheetOpen(false)} />
        )}
        {plan && <PlanResult plan={plan} />}
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
