/**
 * Helper для теста OffersPage — обёртка с QueryClient.
 * Воспроизводит ту же логику, что и routes/offers/index.tsx,
 * без createFileRoute, чтобы vitest не спотыкался о роутер.
 * Синхронизировать с index.tsx при изменении логики.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Plus, ChevronRight } from "lucide-react";
import { MiniHeader } from "@/components/layout/MiniHeader";
import {
  Badge,
  Button,
  Input,
  Select,
  Switch,
  Sheet,
  Skeleton,
  EmptyState,
  Pill,
} from "@/components/ui";
import {
  useOffers,
  useCreateOffer,
  useUpdateOffer,
  useDeleteOffer,
  useOfferRules,
  useUpdateOfferRules,
  type OfferCreatePayload,
  type OfferUpdatePayload,
} from "@/lib/api";
import { haptic, tgConfirm } from "@/lib/tg";
import { useState, useId } from "react";
import type { Offer, OfferRules } from "@fb/shared";

// ─── Константы ───────────────────────────────────────────────────────────────

const VERTICAL_OPTIONS = [
  { value: "", label: "Не указана" },
  { value: "gambling", label: "Gambling" },
  { value: "nutra", label: "Nutra" },
  { value: "finance", label: "Finance" },
  { value: "dating", label: "Dating" },
  { value: "crypto", label: "Crypto" },
  { value: "other", label: "Другая" },
];

interface ThresholdField {
  key: keyof OfferRules;
  label: string;
  hint: string;
}

// Зеркало THRESHOLD_FIELDS из routes/offers/index.tsx — только рабочие пороги
// (spend_no_event/cpm/ctr/funnel_ratio убраны: evaluator их не читает).
const THRESHOLD_FIELDS: ThresholdField[] = [
  { key: "cpa_threshold", label: "CPA порог ($)", hint: "" },
  { key: "frequency_threshold", label: "Frequency порог", hint: "" },
];

// ─── Форма создания/редактирования ───────────────────────────────────────────

// Зеркало parseAccountIds из routes/offers/index.tsx (мульти-кабинет).
function parseAccountIds(raw: string): string[] | null {
  const ids: string[] = [];
  const seen = new Set<string>();
  for (const part of raw.split(/[\s,;]+/)) {
    const token = part.trim();
    if (!token) continue;
    const normalized = token.replace(/^act_/i, "");
    if (!/^\d+$/.test(normalized)) return null;
    if (!seen.has(normalized)) {
      seen.add(normalized);
      ids.push(normalized);
    }
  }
  return ids;
}

function OfferForm({ offer, onClose }: { offer: Offer | null; onClose: () => void }) {
  const isEdit = !!offer;
  const offerAccounts =
    (offer as (Offer & { ad_account_ids?: string[] }) | null)?.ad_account_ids ?? [];
  const [code, setCode] = useState(offer?.code ?? "");
  const [name, setName] = useState(offer?.name ?? "");
  const [vertical, setVertical] = useState(offer?.vertical ?? "");
  const [accountsRaw, setAccountsRaw] = useState(offerAccounts.join(", "));
  const [accountsError, setAccountsError] = useState<string | null>(null);
  const [isActive, setIsActive] = useState(offer?.is_active ?? true);
  const [error, setError] = useState<string | null>(null);
  const switchId = useId();
  const create = useCreateOffer();
  const update = useUpdateOffer();
  const saving = create.isPending || update.isPending;

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setAccountsError(null);
    if (!code.trim()) { setError("Код оффера обязателен"); return; }
    // Мульти-кабинет: минимум один числовой ID кабинета.
    const accountIds = parseAccountIds(accountsRaw);
    if (accountIds === null) { setAccountsError("Только числовые ID кабинетов (через запятую)"); return; }
    if (accountIds.length === 0) { setAccountsError("Укажи минимум один ID кабинета"); return; }
    try {
      if (isEdit && offer) {
        const payload: OfferUpdatePayload = {
          name: name.trim() || null,
          vertical: vertical || null,
          is_active: isActive,
          ad_account_ids: accountIds,
        };
        await update.mutateAsync({ id: offer.id, payload });
      } else {
        const trimmedCode = code.trim().toUpperCase();
        const payload: OfferCreatePayload = {
          code: trimmedCode,
          name: name.trim() || trimmedCode,
          vertical: vertical || null,
          ad_account_ids: accountIds,
        };
        await create.mutateAsync(payload);
      }
      onClose();
    } catch (err) { setError((err as Error).message); }
  }

  return (
    <form onSubmit={(e) => void handleSubmit(e)} className="flex flex-col gap-4 pb-6">
      <Input
        label="Код оффера"
        placeholder="CR2_GH"
        value={code}
        onChange={(e) => setCode(e.target.value.toUpperCase())}
        disabled={isEdit}
        errorMessage={error ?? undefined}
      />
      <Input
        label="Название"
        placeholder={code || "GH Aviator"}
        value={name}
        onChange={(e) => setName(e.target.value)}
      />
      <Input
        label="Рекламные кабинеты"
        placeholder="1234567890, 9876543210"
        value={accountsRaw}
        onChange={(e) => {
          setAccountsRaw(e.target.value);
          if (accountsError) setAccountsError(null);
        }}
        errorMessage={accountsError ?? undefined}
      />
      <Select
        label="Вертикаль"
        value={vertical}
        onChange={(e) => setVertical(e.target.value)}
        options={VERTICAL_OPTIONS}
      />
      {isEdit && (
        <div className="border-t border-bg-5 pt-3">
          <Switch
            id={switchId}
            label="Активен"
            checked={isActive}
            onChange={(e) => setIsActive(e.target.checked)}
          />
        </div>
      )}
      <Button type="submit" loading={saving} fullWidth>
        {isEdit ? "Сохранить" : "Создать оффер"}
      </Button>
    </form>
  );
}

// ─── Редактор порогов ─────────────────────────────────────────────────────────

function ThresholdsForm({ offerId, onClose }: { offerId: string; onClose: () => void }) {
  const { data: rules, isLoading } = useOfferRules(offerId);
  const updateRules = useUpdateOfferRules();
  const [values, setValues] = useState<Record<string, string>>({});
  const [initialized, setInitialized] = useState(false);

  if (rules && !initialized) {
    const init: Record<string, string> = {};
    for (const f of THRESHOLD_FIELDS) {
      const v = rules[f.key];
      init[f.key] = v != null ? String(v) : "";
    }
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

  if (isLoading) {
    return (
      <div className="flex flex-col gap-3 pb-6">
        {Array.from({ length: 6 }, (_, i) => <Skeleton key={i} className="h-14" />)}
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4 pb-6">
      {THRESHOLD_FIELDS.map((f) => (
        <Input
          key={f.key}
          label={f.label}
          placeholder="—"
          type="number"
          min="0"
          step="any"
          value={values[f.key] ?? ""}
          onChange={(e) => setValues((prev) => ({ ...prev, [f.key]: e.target.value }))}
        />
      ))}
      <Button onClick={() => void handleSave()} loading={updateRules.isPending} fullWidth>
        Сохранить пороги
      </Button>
    </div>
  );
}

// ─── Карточка оффера ──────────────────────────────────────────────────────────

function OfferCard({ offer, onClick }: { offer: Offer; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={`Оффер ${offer.code}`}
      className="w-full text-left border bg-bg-1 min-h-[44px] flex items-center gap-3 px-4 py-3"
    >
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="font-display text-bg-11 tracking-[0.04em] shrink-0" style={{ fontSize: 15, fontWeight: 600 }}>
            {offer.code}
          </span>
          {offer.vertical ? (
            <Pill variant="default" className="shrink-0">{offer.vertical}</Pill>
          ) : null}
        </div>
        {offer.name && offer.name !== offer.code ? (
          <p className="text-[12px] text-bg-9 mt-0.5 truncate">{offer.name}</p>
        ) : null}
      </div>
      <div className="flex items-center gap-2 shrink-0">
        <Badge variant={offer.is_active ? "done" : "disabled"} size="sm" withDot>
          {offer.is_active ? "active" : "inactive"}
        </Badge>
        <ChevronRight size={14} strokeWidth={1.5} className="text-bg-7" />
      </div>
    </button>
  );
}

// ─── Detail sheet ─────────────────────────────────────────────────────────────

function OfferDetail({
  offer, onEdit, onThresholds, onDelete, onToggleActive, isToggling,
}: {
  offer: Offer;
  onEdit: () => void;
  onThresholds: () => void;
  onDelete: () => void;
  onToggleActive: () => void;
  isToggling: boolean;
}) {
  return (
    <div className="flex flex-col gap-5 pb-6">
      <div>
        <div className="flex items-center gap-2.5 mb-1">
          <span className="font-display text-bg-11 tracking-[0.04em]" style={{ fontSize: 20, fontWeight: 600 }}>
            {offer.code}
          </span>
          <Badge variant={offer.is_active ? "done" : "disabled"} size="sm" withDot>
            {offer.is_active ? "active" : "inactive"}
          </Badge>
        </div>
        {offer.vertical ? (
          <Pill variant="default">{offer.vertical}</Pill>
        ) : null}
      </div>
      <div className="grid grid-cols-2 gap-2">
        <Button variant="secondary" onClick={onEdit} fullWidth>Редактировать</Button>
        <Button variant="secondary" onClick={onThresholds} fullWidth>Пороги</Button>
        <Button variant="secondary" onClick={onToggleActive} loading={isToggling} fullWidth>
          {offer.is_active ? "Выключить" : "Включить"}
        </Button>
        <Button variant="danger" onClick={onDelete} fullWidth>Удалить</Button>
      </div>
    </div>
  );
}

// ─── TestOffersPage ───────────────────────────────────────────────────────────

type SheetMode = "detail" | "edit" | "create" | "thresholds" | null;

function TestOffersPage() {
  const { data: offers, isLoading, isError, refetch } = useOffers();
  const updateOffer = useUpdateOffer();
  const deleteOffer = useDeleteOffer();
  const [selected, setSelected] = useState<Offer | null>(null);
  const [sheetMode, setSheetMode] = useState<SheetMode>(null);

  function openDetail(offer: Offer) {
    setSelected(offer);
    setSheetMode("detail");
    haptic.selection();
  }

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
    await updateOffer.mutateAsync({
      id: selected.id,
      payload: { is_active: !selected.is_active },
    });
    closeSheet();
  }

  const sheetTitle: Record<NonNullable<SheetMode>, string> = {
    detail: selected?.code ?? "Оффер",
    edit: `Редактировать ${selected?.code ?? ""}`,
    create: "Новый оффер",
    thresholds: `Пороги: ${selected?.code ?? ""}`,
  };

  return (
    <div>
      <MiniHeader
        eyebrowNum="02"
        eyebrow="CATALOG · ОФФЕРЫ"
        title="Офферы"
        right={
          <Button
            size="sm"
            variant="secondary"
            onClick={() => { setSelected(null); setSheetMode("create"); haptic.selection(); }}
          >
            <Plus size={13} strokeWidth={2} />
            Новый
          </Button>
        }
      />
      <div className="px-4 pt-3 flex flex-col gap-px">
        {isLoading && Array.from({ length: 3 }, (_, i) => <Skeleton key={i} className="h-[60px]" />)}
        {isError && !isLoading && (
          <EmptyState
            title="Не удалось загрузить"
            description="Проверьте соединение"
            action={{ label: "Повторить", onClick: () => void refetch() }}
          />
        )}
        {!isLoading && !isError && (offers ?? []).length === 0 && (
          <EmptyState
            title="Офферов нет"
            description="Создайте первый оффер"
            action={{ label: "Создать оффер", onClick: () => { setSelected(null); setSheetMode("create"); } }}
          />
        )}
        {!isLoading && !isError && (offers ?? []).map((offer) => (
          <OfferCard key={offer.id} offer={offer} onClick={() => openDetail(offer)} />
        ))}
      </div>
      <Sheet
        open={sheetMode !== null}
        onClose={closeSheet}
        eyebrow={sheetMode ? {
          detail: "CATALOG · ДЕТАЛИ",
          edit: "CATALOG · РЕДАКТИРОВАНИЕ",
          create: "CATALOG · СОЗДАНИЕ",
          thresholds: "CATALOG · СТОП-ПРАВИЛА",
        }[sheetMode] : undefined}
        title={sheetMode !== null ? sheetTitle[sheetMode] : undefined}
      >
        {sheetMode === "detail" && selected ? (
          <OfferDetail
            offer={selected}
            onEdit={() => setSheetMode("edit")}
            onThresholds={() => setSheetMode("thresholds")}
            onDelete={() => void handleDelete()}
            onToggleActive={() => void handleToggleActive()}
            isToggling={updateOffer.isPending}
          />
        ) : null}
        {(sheetMode === "edit" || sheetMode === "create") ? (
          <OfferForm offer={sheetMode === "edit" ? selected : null} onClose={closeSheet} />
        ) : null}
        {sheetMode === "thresholds" && selected ? (
          <ThresholdsForm offerId={selected.id} onClose={() => setSheetMode("detail")} />
        ) : null}
      </Sheet>
    </div>
  );
}

// ─── Обёртка ─────────────────────────────────────────────────────────────────

const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });

export default function OffersTestWrapper() {
  return (
    <QueryClientProvider client={qc}>
      <TestOffersPage />
    </QueryClientProvider>
  );
}
