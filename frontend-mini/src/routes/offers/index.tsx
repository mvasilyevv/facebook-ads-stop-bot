/**
 * OffersPage — список офферов с CRUD.
 * Карточки + bottom-sheet деталей + форма создания/редактирования + редактор 6 порогов.
 */
import { useState } from "react";
import { createFileRoute } from "@tanstack/react-router";
import { MiniHeader } from "@/components/layout/MiniHeader";
import {
  Card,
  Sheet,
  Button,
  Badge,
  Input,
  Skeleton,
  EmptyState,
  ErrorState,
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
import type { Offer, OfferRules } from "@fb/shared";

export const Route = createFileRoute("/offers/")({
  component: OffersPage,
});

// ─── Форма создания/редактирования оффера ────────────────────────────────

interface OfferFormProps {
  offer: Offer | null;
  onClose: () => void;
}

function OfferForm({ offer, onClose }: OfferFormProps) {
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
    haptic.impact("medium");

    if (!code.trim()) {
      setError("Код оффера обязателен");
      return;
    }

    try {
      if (isEdit && offer) {
        const payload: OfferUpdatePayload = {
          vertical: vertical.trim() || null,
        };
        await update.mutateAsync({ id: offer.id, payload });
      } else {
        const payload: OfferCreatePayload = {
          code: code.trim().toUpperCase(),
          name: code.trim().toUpperCase(), // name=code по дизайн-решению
          vertical: vertical.trim() || null,
        };
        await create.mutateAsync(payload);
      }
      haptic.notify("success");
      onClose();
    } catch (err) {
      haptic.notify("error");
      setError((err as Error).message);
    }
  }

  return (
    <form onSubmit={(e) => void handleSubmit(e)} className="flex flex-col gap-4 pb-4">
      <Input
        label="Код оффера"
        placeholder="CR2_GH"
        value={code}
        onChange={(e) => setCode(e.target.value.toUpperCase())}
        disabled={isEdit} // code immutable
        errorMessage={error ?? undefined}
      />
      <Input
        label="Вертикаль (необязательно)"
        placeholder="gambling"
        value={vertical}
        onChange={(e) => setVertical(e.target.value)}
      />
      <Button type="submit" loading={saving} fullWidth>
        {isEdit ? "Сохранить" : "Создать оффер"}
      </Button>
    </form>
  );
}

// ─── Редактор порогов ─────────────────────────────────────────────────────

const THRESHOLD_FIELDS: Array<{ key: keyof OfferRules; label: string; hint: string }> = [
  { key: "spend_no_event_threshold", label: "Spend без события ($)", hint: "При каком спенде без события — стоп" },
  { key: "cpa_threshold", label: "CPA порог ($)", hint: "Максимально допустимый CPA" },
  { key: "cpm_threshold", label: "CPM порог ($)", hint: "Максимально допустимый CPM" },
  { key: "ctr_threshold", label: "CTR порог (%)", hint: "Минимально допустимый CTR" },
  { key: "frequency_threshold", label: "Frequency порог", hint: "Максимальная частота показа" },
  { key: "funnel_ratio_threshold", label: "Funnel ratio (%)", hint: "Минимальный reg/lead ratio" },
];

interface ThresholdsFormProps {
  offerId: string;
  onClose: () => void;
}

function ThresholdsForm({ offerId, onClose }: ThresholdsFormProps) {
  const { data: rules, isLoading } = useOfferRules(offerId);
  const updateRules = useUpdateOfferRules();

  const [values, setValues] = useState<Record<string, string>>({});
  const [initialized, setInitialized] = useState(false);

  // Инициализируем значения при загрузке
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
    haptic.impact("medium");
    const payload: Partial<OfferRules> = {};
    for (const f of THRESHOLD_FIELDS) {
      const raw = values[f.key]?.trim();
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      (payload as any)[f.key] = raw ? Number(raw) : null;
    }
    try {
      await updateRules.mutateAsync({ offerId, payload });
      haptic.notify("success");
      onClose();
    } catch (err) {
      haptic.notify("error");
      void alert((err as Error).message);
    }
  }

  if (isLoading) {
    return (
      <div className="flex flex-col gap-3 pb-4">
        {Array.from({ length: 6 }, (_, i) => <Skeleton key={i} className="h-12" />)}
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3 pb-4">
      {THRESHOLD_FIELDS.map((f) => (
        <div key={f.key}>
          <Input
            label={f.label}
            placeholder="—"
            type="number"
            min="0"
            step="any"
            value={values[f.key] ?? ""}
            onChange={(e) => setValues((prev) => ({ ...prev, [f.key]: e.target.value }))}
          />
          <p className="text-[11px] text-[var(--color-bg-8)] mt-1">{f.hint}</p>
        </div>
      ))}
      <Button onClick={() => void handleSave()} loading={updateRules.isPending} fullWidth>
        Сохранить пороги
      </Button>
    </div>
  );
}

// ─── Детали оффера (bottom sheet) ─────────────────────────────────────────

interface OfferDetailProps {
  offer: Offer;
  onEdit: () => void;
  onThresholds: () => void;
  onDelete: () => void;
  onToggleActive: () => void;
}

function OfferDetail({ offer, onEdit, onThresholds, onDelete, onToggleActive }: OfferDetailProps) {
  return (
    <div className="flex flex-col gap-4 pb-4">
      <div className="flex items-center gap-3">
        <span className="text-[20px] font-semibold font-display text-[var(--color-bg-11)]">
          {offer.code}
        </span>
        <Badge variant={offer.is_active ? "normal" : "neutral"}>
          {offer.is_active ? "Активен" : "Выключен"}
        </Badge>
      </div>

      {offer.vertical && (
        <p className="text-[13px] text-[var(--color-bg-9)]">
          <span className="font-mono text-[11px] text-[var(--color-bg-8)] uppercase mr-1">Вертикаль:</span>
          {offer.vertical}
        </p>
      )}

      <div className="grid grid-cols-2 gap-2">
        <Button variant="secondary" onClick={onEdit}>Редактировать</Button>
        <Button variant="secondary" onClick={onThresholds}>Пороги</Button>
        <Button variant="secondary" onClick={onToggleActive}>
          {offer.is_active ? "Выключить" : "Включить"}
        </Button>
        <Button variant="danger" onClick={onDelete}>Удалить</Button>
      </div>
    </div>
  );
}

// ─── OffersPage ───────────────────────────────────────────────────────────

type SheetMode = "detail" | "edit" | "create" | "thresholds" | null;

function OffersPage() {
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

  function closeSheet() {
    setSheetMode(null);
    // не сбрасываем selected — нужен при переходах detail→edit
  }

  async function handleDelete() {
    if (!selected) return;
    const ok = await tgConfirm(`Удалить оффер ${selected.code}? Это действие необратимо.`);
    if (!ok) return;
    haptic.impact("heavy");
    try {
      await deleteOffer.mutateAsync({ id: selected.id });
      haptic.notify("success");
      closeSheet();
    } catch (err) {
      haptic.notify("error");
      void alert((err as Error).message);
    }
  }

  async function handleToggleActive() {
    if (!selected) return;
    haptic.impact("medium");
    try {
      await updateOffer.mutateAsync({
        id: selected.id,
        payload: { is_active: !selected.is_active },
      });
      haptic.notify("success");
      closeSheet();
    } catch (err) {
      haptic.notify("error");
      void alert((err as Error).message);
    }
  }

  const sheetTitle: Record<NonNullable<SheetMode>, string> = {
    detail: selected?.code ?? "Оффер",
    edit: `Редактировать ${selected?.code ?? ""}`,
    create: "Новый оффер",
    thresholds: `Пороги: ${selected?.code ?? ""}`,
  };

  return (
    <div className="flex flex-col min-h-full pb-20">
      <MiniHeader
        eyebrow="Офферы"
        title="Офферы"
        right={
          <Button size="sm" variant="secondary" onClick={() => { setSelected(null); setSheetMode("create"); }}>
            + Новый
          </Button>
        }
      />

      <div className="p-4 flex flex-col gap-3">
        {isLoading && (
          <>
            {Array.from({ length: 4 }, (_, i) => <Skeleton key={i} className="h-16" />)}
          </>
        )}
        {isError && (
          <ErrorState message="Не удалось загрузить офферы" onRetry={() => void refetch()} />
        )}
        {!isLoading && !isError && (offers ?? []).length === 0 && (
          <EmptyState
            title="Офферов нет"
            description="Создайте первый оффер"
            action={{ label: "Создать", onClick: () => { setSelected(null); setSheetMode("create"); } }}
          />
        )}
        {!isLoading &&
          !isError &&
          (offers ?? []).map((offer) => (
            <Card
              key={offer.id}
              padding="sm"
              onClick={() => openDetail(offer)}
              className="cursor-pointer active:opacity-70"
            >
              <div className="flex items-center justify-between gap-2">
                <div className="flex-1 min-w-0">
                  <p className="text-[14px] font-semibold font-mono text-[var(--color-bg-11)]">
                    {offer.code}
                  </p>
                  {offer.vertical && (
                    <p className="text-[11px] text-[var(--color-bg-8)] mt-0.5">{offer.vertical}</p>
                  )}
                </div>
                <Badge variant={offer.is_active ? "normal" : "neutral"}>
                  {offer.is_active ? "Активен" : "Выкл"}
                </Badge>
              </div>
            </Card>
          ))}
      </div>

      {/* Bottom sheet */}
      <Sheet
        open={sheetMode !== null}
        onClose={closeSheet}
        eyebrow={sheetMode !== null ? { detail: "Детали", edit: "Редактирование", create: "Создание", thresholds: "Стоп-правила" }[sheetMode] : undefined}
        title={sheetMode !== null ? sheetTitle[sheetMode] : undefined}
      >
        {sheetMode === "detail" && selected && (
          <OfferDetail
            offer={selected}
            onEdit={() => setSheetMode("edit")}
            onThresholds={() => setSheetMode("thresholds")}
            onDelete={() => void handleDelete()}
            onToggleActive={() => void handleToggleActive()}
          />
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
