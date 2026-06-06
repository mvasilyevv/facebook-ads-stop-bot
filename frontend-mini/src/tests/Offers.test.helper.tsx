/**
 * Helper для теста OffersPage — обёртка с QueryClient.
 * Импортирует компонент напрямую, не через createFileRoute.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MiniHeader } from "@/components/layout/MiniHeader";
import {
  Card, Sheet, Button, Badge, Input, Skeleton, EmptyState, ErrorState,
} from "@/components/ui";
import {
  useOffers, useCreateOffer, useUpdateOffer, useDeleteOffer,
  useOfferRules, useUpdateOfferRules,
  type OfferCreatePayload, type OfferUpdatePayload,
} from "@/lib/api";
import { haptic, tgConfirm } from "@/lib/tg";
import { useState } from "react";
import type { Offer, OfferRules } from "@fb/shared";

const THRESHOLD_FIELDS: Array<{ key: keyof OfferRules; label: string; hint: string }> = [
  { key: "spend_no_event_threshold", label: "Spend без события ($)", hint: "" },
  { key: "cpa_threshold", label: "CPA порог ($)", hint: "" },
  { key: "cpm_threshold", label: "CPM порог ($)", hint: "" },
  { key: "ctr_threshold", label: "CTR порог (%)", hint: "" },
  { key: "frequency_threshold", label: "Frequency порог", hint: "" },
  { key: "funnel_ratio_threshold", label: "Funnel ratio (%)", hint: "" },
];

function OfferForm({ offer, onClose }: { offer: Offer | null; onClose: () => void }) {
  const isEdit = !!offer;
  const [code, setCode] = useState(offer?.code ?? "");
  const [vertical, setVertical] = useState(offer?.vertical ?? "");
  const [error, setError] = useState<string | null>(null);
  const create = useCreateOffer();
  const update = useUpdateOffer();
  const saving = create.isPending || update.isPending;

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (!code.trim()) { setError("Код оффера обязателен"); return; }
    try {
      if (isEdit && offer) {
        const payload: OfferUpdatePayload = { vertical: vertical.trim() || null };
        await update.mutateAsync({ id: offer.id, payload });
      } else {
        const payload: OfferCreatePayload = { code: code.trim().toUpperCase(), name: code.trim().toUpperCase(), vertical: vertical.trim() || null };
        await create.mutateAsync(payload);
      }
      onClose();
    } catch (err) { setError((err as Error).message); }
  }

  return (
    <form onSubmit={(e) => void handleSubmit(e)} className="flex flex-col gap-4 pb-4">
      <Input label="Код оффера" placeholder="CR2_GH" value={code}
        onChange={(e) => setCode(e.target.value.toUpperCase())} disabled={isEdit} errorMessage={error ?? undefined} />
      <Input label="Вертикаль (необязательно)" placeholder="gambling" value={vertical}
        onChange={(e) => setVertical(e.target.value)} />
      <Button type="submit" loading={saving} fullWidth>
        {isEdit ? "Сохранить" : "Создать оффер"}
      </Button>
    </form>
  );
}

function ThresholdsForm({ offerId, onClose }: { offerId: string; onClose: () => void }) {
  const { data: rules, isLoading } = useOfferRules(offerId);
  const updateRules = useUpdateOfferRules();
  const [values, setValues] = useState<Record<string, string>>({});
  const [initialized, setInitialized] = useState(false);
  if (rules && !initialized) {
    const init: Record<string, string> = {};
    for (const f of THRESHOLD_FIELDS) { const v = rules[f.key]; init[f.key] = v != null ? String(v) : ""; }
    setValues(init);
    setInitialized(true);
  }
  async function handleSave() {
    const payload: Partial<OfferRules> = {};
    for (const f of THRESHOLD_FIELDS) {
      const raw = values[f.key]?.trim();
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      (payload as any)[f.key] = raw ? Number(raw) : null;
    }
    await updateRules.mutateAsync({ offerId, payload });
    onClose();
  }
  if (isLoading) return <div className="flex flex-col gap-3 pb-4">{Array.from({ length: 6 }, (_, i) => <Skeleton key={i} className="h-12" />)}</div>;
  return (
    <div className="flex flex-col gap-3 pb-4">
      {THRESHOLD_FIELDS.map((f) => (
        <Input key={f.key} label={f.label} placeholder="—" type="number" min="0" step="any"
          value={values[f.key] ?? ""} onChange={(e) => setValues((prev) => ({ ...prev, [f.key]: e.target.value }))} />
      ))}
      <Button onClick={() => void handleSave()} loading={updateRules.isPending} fullWidth>Сохранить пороги</Button>
    </div>
  );
}

function OfferDetail({ offer, onEdit, onThresholds, onDelete, onToggleActive }: {
  offer: Offer; onEdit: () => void; onThresholds: () => void; onDelete: () => void; onToggleActive: () => void;
}) {
  return (
    <div className="flex flex-col gap-4 pb-4">
      <div className="flex items-center gap-3">
        <span className="text-[20px] font-semibold">{offer.code}</span>
        <Badge variant={offer.is_active ? "normal" : "neutral"}>{offer.is_active ? "Активен" : "Выключен"}</Badge>
      </div>
      <div className="grid grid-cols-2 gap-2">
        <Button variant="secondary" onClick={onEdit}>Редактировать</Button>
        <Button variant="secondary" onClick={onThresholds}>Пороги</Button>
        <Button variant="secondary" onClick={onToggleActive}>{offer.is_active ? "Выключить" : "Включить"}</Button>
        <Button variant="danger" onClick={onDelete}>Удалить</Button>
      </div>
    </div>
  );
}

type SheetMode = "detail" | "edit" | "create" | "thresholds" | null;

function TestOffersPage() {
  const { data: offers, isLoading, isError, refetch } = useOffers();
  const updateOffer = useUpdateOffer();
  const deleteOffer = useDeleteOffer();
  const [selected, setSelected] = useState<Offer | null>(null);
  const [sheetMode, setSheetMode] = useState<SheetMode>(null);

  function openDetail(offer: Offer) { setSelected(offer); setSheetMode("detail"); haptic.selection(); }
  function closeSheet() { setSheetMode(null); }

  async function handleDelete() {
    if (!selected) return;
    const ok = await tgConfirm(`Удалить оффер ${selected.code}?`);
    if (!ok) return;
    await deleteOffer.mutateAsync({ id: selected.id });
    closeSheet();
  }

  async function handleToggleActive() {
    if (!selected) return;
    await updateOffer.mutateAsync({ id: selected.id, payload: { is_active: !selected.is_active } });
    closeSheet();
  }

  const sheetTitle: Record<NonNullable<SheetMode>, string> = {
    detail: selected?.code ?? "", edit: `Редактировать ${selected?.code ?? ""}`,
    create: "Новый оффер", thresholds: `Пороги: ${selected?.code ?? ""}`,
  };

  return (
    <div>
      <MiniHeader eyebrow="Офферы" title="Офферы"
        right={<Button size="sm" variant="secondary" onClick={() => { setSelected(null); setSheetMode("create"); }}>+ Новый</Button>}
      />
      <div className="p-4 flex flex-col gap-3">
        {isLoading && Array.from({ length: 3 }, (_, i) => <Skeleton key={i} className="h-16" />)}
        {isError && <ErrorState message="Ошибка" onRetry={() => void refetch()} />}
        {!isLoading && !isError && (offers ?? []).length === 0 && <EmptyState title="Офферов нет" />}
        {!isLoading && !isError && (offers ?? []).map((offer) => (
          <Card key={offer.id} padding="sm" onClick={() => openDetail(offer)} className="cursor-pointer">
            <div className="flex items-center justify-between gap-2">
              <p className="text-[14px] font-semibold font-mono">{offer.code}</p>
              <Badge variant={offer.is_active ? "normal" : "neutral"}>{offer.is_active ? "Активен" : "Выкл"}</Badge>
            </div>
          </Card>
        ))}
      </div>
      <Sheet open={sheetMode !== null} onClose={closeSheet}
        eyebrow={sheetMode ? { detail: "Детали", edit: "Редактирование", create: "Создание", thresholds: "Пороги" }[sheetMode] : undefined}
        title={sheetMode !== null ? sheetTitle[sheetMode] : undefined}
      >
        {sheetMode === "detail" && selected && (
          <OfferDetail offer={selected} onEdit={() => setSheetMode("edit")} onThresholds={() => setSheetMode("thresholds")}
            onDelete={() => void handleDelete()} onToggleActive={() => void handleToggleActive()} />
        )}
        {(sheetMode === "edit" || sheetMode === "create") && (
          <OfferForm offer={sheetMode === "edit" ? selected : null} onClose={closeSheet} />
        )}
        {sheetMode === "thresholds" && selected && (
          <ThresholdsForm offerId={selected.id} onClose={() => setSheetMode("detail")} />
        )}
      </Sheet>
    </div>
  );
}

const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });

export default function OffersTestWrapper() {
  return (
    <QueryClientProvider client={qc}>
      <TestOffersPage />
    </QueryClientProvider>
  );
}
