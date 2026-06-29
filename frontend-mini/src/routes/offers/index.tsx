/**
 * OffersPage — каталог офферов с CRUD под дизайн-канон mini.
 * Шапка → список карточек → bottom-sheet деталей/формы/порогов.
 * Код оффера: mono 15px weight 600 text-bg-11 (канон OfferCard).
 */
import { useState, useId } from "react";
import { createFileRoute } from "@tanstack/react-router";
import { Plus, ChevronRight } from "lucide-react";
import { MiniHeader } from "@/components/layout/MiniHeader";
import { Eyebrow } from "@/components/data/Eyebrow";
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
  type OfferExt,
} from "@/lib/api";
import { haptic, tgConfirm } from "@/lib/tg";
import { cn } from "@/lib/cn";
import type { Offer, OfferRules } from "@fb/shared";

export const Route = createFileRoute("/offers/")({
  component: OffersPage,
});

// ─── Константы вертикалей ─────────────────────────────────────────────────────

const VERTICAL_OPTIONS = [
  { value: "", label: "Не указана" },
  { value: "gambling", label: "Gambling" },
  { value: "nutra", label: "Nutra" },
  { value: "finance", label: "Finance" },
  { value: "dating", label: "Dating" },
  { value: "crypto", label: "Crypto" },
  { value: "other", label: "Другая" },
];

// ─── Конфиг порогов ───────────────────────────────────────────────────────────

interface ThresholdField {
  key: keyof OfferRules;
  label: string;
  hint: string;
}

// Только пороги, которые реально читает evaluator. spend_no_event/cpm/ctr/funnel_ratio
// убраны: движок их не использует (CPM/CTR — диагностика по решению байера, spend-без-
// события дублирует guardrail'ы CPC/CPL), а в форме создавали ложное чувство настройки стопа.
const THRESHOLD_FIELDS: ThresholdField[] = [
  {
    key: "cpa_threshold",
    label: "CPA порог ($)",
    hint: "Максимально допустимый CPA",
  },
  {
    key: "frequency_threshold",
    label: "Frequency порог",
    hint: "Максимальная частота показа",
  },
];

// ─── Форма создания/редактирования оффера ─────────────────────────────────────

interface OfferFormProps {
  offer: Offer | null;
  onClose: () => void;
}

/** Разбор ввода кабинетов: запятые/пробелы, срез act_, дедуп. null — есть мусор. */
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

/**
 * Разбор гео-ввода: запятые/пробелы, ISO-2 upper, дедуп. null — есть невалидный
 * токен (не 2 буквы). Пустой ввод → [] (валидно, гео не задано).
 */
function parseCountries(raw: string): string[] | null {
  const codes: string[] = [];
  const seen = new Set<string>();
  for (const part of raw.split(/[\s,;]+/)) {
    const token = part.trim().toUpperCase();
    if (!token) continue;
    if (!/^[A-Z]{2}$/.test(token)) return null;
    if (!seen.has(token)) {
      seen.add(token);
      codes.push(token);
    }
  }
  return codes;
}

function OfferForm({ offer, onClose }: OfferFormProps) {
  const isEdit = !!offer;
  // countries появляются в generated-типах после pnpm gen:api —
  // до этого читаем мягко через OfferExt (бэк OfferOut уже отдаёт их).
  const offerExt = offer as OfferExt | null;
  const offerAccounts = offerExt?.ad_account_ids ?? [];
  const offerCountries = offerExt?.countries ?? [];
  const [code, setCode] = useState(offer?.code ?? "");
  const [name, setName] = useState(offer?.name ?? "");
  const [vertical, setVertical] = useState(offer?.vertical ?? "");
  const [accountsRaw, setAccountsRaw] = useState(offerAccounts.join(", "));
  const [accountsError, setAccountsError] = useState<string | null>(null);
  const [countriesRaw, setCountriesRaw] = useState(offerCountries.join(", "));
  const [countriesError, setCountriesError] = useState<string | null>(null);
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
    setCountriesError(null);
    haptic.impact("medium");

    if (!code.trim()) {
      setError("Код оффера обязателен");
      return;
    }

    // Мульти-кабинет: минимум один числовой ID — без него оффер не сканируется.
    const accountIds = parseAccountIds(accountsRaw);
    if (accountIds === null) {
      setAccountsError("Только числовые ID кабинетов (через запятую)");
      return;
    }
    if (accountIds.length === 0) {
      setAccountsError("Укажи минимум один ID кабинета");
      return;
    }

    // Гео — опц.: пусто → [] (валидно). Невалидный токен (не ISO-2) → ошибка.
    const countries = parseCountries(countriesRaw);
    if (countries === null) {
      setCountriesError("Только ISO-2 коды стран (US, GB, DE…)");
      return;
    }

    try {
      if (isEdit && offer) {
        const payload: OfferUpdatePayload = {
          name: name.trim() || null,
          vertical: vertical || null,
          is_active: isActive,
          ad_account_ids: accountIds,
          countries,
        };
        await update.mutateAsync({ id: offer.id, payload });
      } else {
        const trimmedCode = code.trim().toUpperCase();
        const payload: OfferCreatePayload = {
          code: trimmedCode,
          name: name.trim() || trimmedCode,
          vertical: vertical || null,
          ad_account_ids: accountIds,
          countries,
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
    <form onSubmit={(e) => void handleSubmit(e)} className="flex flex-col gap-4 pb-6">
      {/* Код оффера — immutable при редактировании */}
      <Input
        label="Код оффера"
        placeholder="CR2_GH"
        value={code}
        onChange={(e) => setCode(e.target.value.toUpperCase())}
        disabled={isEdit}
        errorMessage={error ?? undefined}
      />

      {/* Имя */}
      <Input
        label="Название"
        placeholder={code || "GH Aviator"}
        value={name}
        onChange={(e) => setName(e.target.value)}
      />

      {/* Кабинеты (мульти-кабинет): числовые ID через запятую, минимум 1 */}
      <Input
        label="Рекламные кабинеты"
        placeholder="1234567890, 9876543210"
        value={accountsRaw}
        onChange={(e) => {
          setAccountsRaw(e.target.value);
          if (accountsError) setAccountsError(null);
        }}
        errorMessage={accountsError ?? undefined}
        inputMode="numeric"
      />

      {/* Гео оффера (ISO-2 upper, опц.) — визард префиллит ими countries */}
      <Input
        label="Гео (страны, ISO-2)"
        placeholder="US, GB, DE"
        value={countriesRaw}
        onChange={(e) => {
          setCountriesRaw(e.target.value.toUpperCase());
          if (countriesError) setCountriesError(null);
        }}
        errorMessage={countriesError ?? undefined}
        autoCapitalize="characters"
        autoCorrect="off"
      />

      {/* Вертикаль */}
      <Select
        label="Вертикаль"
        value={vertical}
        onChange={(e) => setVertical(e.target.value)}
        options={VERTICAL_OPTIONS}
      />

      {/* Активность (только при редактировании) */}
      {isEdit && (
        <div className="border-t border-[var(--hairline)] pt-3">
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

// ─── Редактор порогов ──────────────────────────────────────────────────────────

interface ThresholdsFormProps {
  offerId: string;
  onClose: () => void;
}

function ThresholdsForm({ offerId, onClose }: ThresholdsFormProps) {
  const { data: rules, isLoading } = useOfferRules(offerId);
  const updateRules = useUpdateOfferRules();

  const [values, setValues] = useState<Record<string, string>>({});
  const [initialized, setInitialized] = useState(false);

  // Инициализируем значения при загрузке данных
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
      <div className="flex flex-col gap-3 pb-6">
        {Array.from({ length: 6 }, (_, i) => <Skeleton key={i} className="h-14" />)}
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4 pb-6">
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
          <p className="text-[11px] text-bg-8 mt-1">{f.hint}</p>
        </div>
      ))}
      <Button onClick={() => void handleSave()} loading={updateRules.isPending} fullWidth>
        Сохранить пороги
      </Button>
    </div>
  );
}

// ─── Карточка оффера ──────────────────────────────────────────────────────────

interface OfferCardProps {
  offer: Offer;
  onClick: () => void;
}

function OfferCard({ offer, onClick }: OfferCardProps) {
  const isActive = offer.is_active;
  const vertical = offer.vertical;
  // Мульти-кабинет: до pnpm gen:api поле читаем мягким кастом.
  const accounts = (offer as Offer & { ad_account_ids?: string[] }).ad_account_ids ?? [];

  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "w-full text-left border bg-bg-1 min-h-[44px] rounded-[var(--radius-2)]",
        "flex items-center gap-3 px-4 py-3.5",
        "active:bg-bg-2 transition-colors duration-[var(--dur-base)]",
        isActive ? "border-[var(--hairline)]" : "border-[var(--hairline)] opacity-70",
      )}
      aria-label={`Оффер ${offer.code}`}
    >
      {/* Левая часть: код + название + вертикаль */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 flex-wrap">
          {/* Код оффера — mono 15px 600 text-bg-11, канон OfferCard */}
          <span
            className="font-display text-bg-11 tracking-[0.04em] shrink-0"
            style={{ fontSize: 15, fontWeight: 600 }}
          >
            {offer.code}
          </span>
          {vertical ? (
            <Pill variant="default" className="shrink-0">
              {vertical}
            </Pill>
          ) : null}
        </div>
        {offer.name && offer.name !== offer.code ? (
          <p className="text-[12px] text-bg-9 mt-0.5 truncate">{offer.name}</p>
        ) : null}
        {/* Мульти-кабинет: кабинеты оффера; пусто = warning (оффер вне скана) */}
        {isActive && accounts.length === 0 ? (
          <p className="text-[11px] text-warning mt-0.5">кабинеты не заданы — не сканируется</p>
        ) : accounts.length > 0 ? (
          <p
            className="font-display tabular-nums text-[11px] text-bg-8 mt-0.5 truncate"
            title={accounts.join(", ")}
          >
            каб: {accounts.map((a) => (a.length > 8 ? `…${a.slice(-6)}` : a)).join(" · ")}
          </p>
        ) : null}
      </div>

      {/* Правая часть: Badge активности + chevron */}
      <div className="flex items-center gap-2 shrink-0">
        <Badge
          variant={isActive ? "done" : "disabled"}
          size="sm"
          withDot
        >
          {isActive ? "active" : "inactive"}
        </Badge>
        <ChevronRight size={14} strokeWidth={1.5} className="text-bg-7" />
      </div>
    </button>
  );
}

// ─── Detail-sheet содержимое ──────────────────────────────────────────────────

interface OfferDetailProps {
  offer: Offer;
  onEdit: () => void;
  onThresholds: () => void;
  onDelete: () => void;
  onToggleActive: () => void;
  isToggling: boolean;
}

function OfferDetail({
  offer,
  onEdit,
  onThresholds,
  onDelete,
  onToggleActive,
  isToggling,
}: OfferDetailProps) {
  return (
    <div className="flex flex-col gap-5 pb-6">
      {/* Шапка: код + badge + вертикаль */}
      <div>
        <div className="flex items-center gap-2.5 mb-1">
          <span
            className="font-display text-bg-11 tracking-[0.04em]"
            style={{ fontSize: 20, fontWeight: 600 }}
          >
            {offer.code}
          </span>
          <Badge
            variant={offer.is_active ? "done" : "disabled"}
            size="sm"
            withDot
          >
            {offer.is_active ? "active" : "inactive"}
          </Badge>
        </div>
        {offer.vertical ? (
          <div className="flex items-center gap-1.5 mt-1">
            <Eyebrow>ВЕРТИКАЛЬ</Eyebrow>
            <Pill variant="default">{offer.vertical}</Pill>
          </div>
        ) : null}
        {offer.name && offer.name !== offer.code ? (
          <p className="text-[13px] text-bg-9 mt-1">{offer.name}</p>
        ) : null}
      </div>

      {/* Кнопки действий */}
      <div className="grid grid-cols-2 gap-2">
        <Button variant="secondary" onClick={onEdit} fullWidth>
          Редактировать
        </Button>
        <Button variant="secondary" onClick={onThresholds} fullWidth>
          Пороги
        </Button>
        <Button
          variant="secondary"
          onClick={onToggleActive}
          loading={isToggling}
          fullWidth
        >
          {offer.is_active ? "Выключить" : "Включить"}
        </Button>
        <Button variant="danger" onClick={onDelete} fullWidth>
          Удалить
        </Button>
      </div>
    </div>
  );
}

// ─── OffersPage ────────────────────────────────────────────────────────────────

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
    // selected не сбрасываем — нужен при detail→edit переходе
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

  // Заголовки для sheet по режиму
  const sheetEyebrow: Record<NonNullable<SheetMode>, string> = {
    detail: "CATALOG · ДЕТАЛИ",
    edit: "CATALOG · РЕДАКТИРОВАНИЕ",
    create: "CATALOG · СОЗДАНИЕ",
    thresholds: "CATALOG · СТОП-ПРАВИЛА",
  };
  const sheetTitle: Record<NonNullable<SheetMode>, string> = {
    detail: selected?.code ?? "Оффер",
    edit: `Редактировать ${selected?.code ?? ""}`,
    create: "Новый оффер",
    thresholds: `Пороги: ${selected?.code ?? ""}`,
  };

  // Счётчик для eyebrow правой кнопки
  const activeCount = (offers ?? []).filter((o) => o.is_active).length;

  return (
    <div className="flex flex-col min-h-full pb-20">
      {/* Шапка */}
      <MiniHeader
        eyebrowNum="02"
        eyebrow="CATALOG · ОФФЕРЫ"
        title="Офферы"
        right={
          <Button
            size="sm"
            variant="secondary"
            onClick={() => {
              setSelected(null);
              setSheetMode("create");
              haptic.selection();
            }}
          >
            <Plus size={13} strokeWidth={2} />
            Новый
          </Button>
        }
      />

      {/* Подзаголовок-счётчик */}
      {!isLoading && !isError && (offers ?? []).length > 0 ? (
        <div className="px-4 pt-2 pb-1">
          <Eyebrow>
            {activeCount} active · {(offers ?? []).length - activeCount} inactive
          </Eyebrow>
        </div>
      ) : null}

      {/* Список */}
      <div className="px-4 pt-3 flex flex-col gap-2">
        {/* Загрузка */}
        {isLoading &&
          Array.from({ length: 4 }, (_, i) => (
            <Skeleton key={i} className="h-[60px] w-full rounded-[var(--radius-2)]" />
          ))}

        {/* Ошибка */}
        {isError && !isLoading && (
          <EmptyState
            title="Не удалось загрузить"
            description="Проверьте соединение"
            action={{ label: "Повторить", onClick: () => void refetch() }}
          />
        )}

        {/* Пусто */}
        {!isLoading && !isError && (offers ?? []).length === 0 && (
          <EmptyState
            title="Офферов нет"
            description="Создайте первый оффер для настройки стоп-правил"
            action={{
              label: "Создать оффер",
              onClick: () => {
                setSelected(null);
                setSheetMode("create");
              },
            }}
          />
        )}

        {/* Карточки офферов */}
        {!isLoading &&
          !isError &&
          (offers ?? []).map((offer) => (
            <OfferCard
              key={offer.id}
              offer={offer}
              onClick={() => openDetail(offer)}
            />
          ))}
      </div>

      {/* Bottom sheet */}
      <Sheet
        open={sheetMode !== null}
        onClose={closeSheet}
        eyebrow={sheetMode !== null ? sheetEyebrow[sheetMode] : undefined}
        title={sheetMode !== null ? sheetTitle[sheetMode] : undefined}
      >
        {sheetMode === "detail" && selected ? (
          <OfferDetail
            offer={selected}
            onEdit={() => { setSheetMode("edit"); }}
            onThresholds={() => { setSheetMode("thresholds"); }}
            onDelete={() => void handleDelete()}
            onToggleActive={() => void handleToggleActive()}
            isToggling={updateOffer.isPending}
          />
        ) : null}

        {(sheetMode === "edit" || sheetMode === "create") ? (
          <OfferForm
            offer={sheetMode === "edit" ? selected : null}
            onClose={closeSheet}
          />
        ) : null}

        {sheetMode === "thresholds" && selected ? (
          <ThresholdsForm
            offerId={selected.id}
            onClose={() => setSheetMode("detail")}
          />
        ) : null}
      </Sheet>
    </div>
  );
}
