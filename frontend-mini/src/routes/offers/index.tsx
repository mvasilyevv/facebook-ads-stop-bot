/**
 * OffersPage — каталог офферов с CRUD под дизайн-канон mini.
 * Шапка → список карточек → bottom-sheet деталей/формы/порогов.
 * Код оффера: mono 15px weight 600 text-bg-11 (канон OfferCard).
 */
import { useEffect, useId, useRef, useState } from "react";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { Plus, ChevronRight } from "lucide-react";
import { safeApiProblemMessage } from "@fb/operator-api";
import {
  DEFAULT_OFFER_RULES_VALUES,
  isOfferCpaValid,
  isOfferCurrencyValid,
  parseOfferAccountIds,
  parseOfferCountries,
  rulesValuesFromOut,
  rulesValuesToPayload,
  type OfferRulesValues,
} from "@fb/features/offers";
import { formatSpend } from "@fb/shared/format/number";
import { Eyebrow } from "@fb/operator-ui";
import { MiniHeader } from "@/components/layout/MiniHeader";
import {
  Badge,
  Button,
  Input,
  Slider,
  Switch,
  Sheet,
  Skeleton,
  EmptyState,
} from "@/components/ui";
import {
  useOffers,
  useCreateOffer,
  useUpdateOffer,
  useOfferRules,
  useUpdateOfferRules,
  useRulesPreview,
  type OfferCreatePayload,
  type OfferUpdatePayload,
} from "@/lib/api";
import { haptic } from "@/lib/tg";
import { cn } from "@/lib/cn";
import { getStoredRole } from "@/lib/auth";
import type { Offer } from "@fb/shared";

type OfferFilter = "all" | "active" | "inactive";

export const Route = createFileRoute("/offers/")({
  validateSearch: (
    search: Record<string, unknown>,
  ): { filter: OfferFilter } => ({
    filter:
      search.filter === "active" || search.filter === "inactive"
        ? search.filter
        : "all",
  }),
  component: OffersPage,
});

// ─── Форма создания/редактирования оффера ─────────────────────────────────────

interface OfferFormProps {
  offer: Offer | null;
  onClose: () => void;
}

function OfferForm({ offer, onClose }: OfferFormProps) {
  const isEdit = !!offer;
  const offerAccounts = offer?.ad_account_ids ?? [];
  const offerCountries = offer?.countries ?? [];
  const [code, setCode] = useState(offer?.code ?? "");
  const [pixelId, setPixelId] = useState(offer?.pixel_id ?? "");
  const [accountsRaw, setAccountsRaw] = useState(offerAccounts.join(", "));
  const [accountsError, setAccountsError] = useState<string | null>(null);
  const [countriesRaw, setCountriesRaw] = useState(offerCountries.join(", "));
  const [countriesError, setCountriesError] = useState<string | null>(null);
  const [isActive, setIsActive] = useState(offer?.is_active ?? true);
  const [error, setError] = useState<string | null>(null);
  const switchId = useId();
  const codeRef = useRef<HTMLInputElement>(null);
  const accountsRef = useRef<HTMLInputElement>(null);
  const countriesRef = useRef<HTMLInputElement>(null);

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
      codeRef.current?.focus();
      return;
    }

    // Мульти-кабинет: минимум один числовой ID — без него оффер не сканируется.
    const accountIds = parseOfferAccountIds(accountsRaw);
    if (accountIds === null) {
      setAccountsError("Только числовые ID кабинетов (через запятую)");
      accountsRef.current?.focus();
      return;
    }
    if (accountIds.length === 0) {
      setAccountsError("Укажи минимум один ID кабинета");
      accountsRef.current?.focus();
      return;
    }

    // Гео — опц.: пусто → [] (валидно). Невалидный токен (не ISO-2) → ошибка.
    const countries = parseOfferCountries(countriesRaw);
    if (countries === null) {
      setCountriesError("Только ISO-2 коды стран (US, GB, DE…)");
      countriesRef.current?.focus();
      return;
    }

    try {
      if (isEdit && offer) {
        const payload: OfferUpdatePayload = {
          pixel_id: pixelId,
          is_active: isActive,
          ad_account_ids: accountIds,
          countries,
        };
        await update.mutateAsync({ id: offer.id, payload });
      } else {
        const trimmedCode = code.trim().toUpperCase();
        const payload: OfferCreatePayload = {
          code: trimmedCode,
          pixel_id: pixelId || null,
          is_active: isActive,
          ad_account_ids: accountIds,
          countries,
        };
        await create.mutateAsync(payload);
      }
      haptic.notify("success");
      onClose();
    } catch (err) {
      haptic.notify("error");
      setError(
        safeApiProblemMessage(
          err,
          "Оффер не сохранён. Проверьте поля и повторите.",
        ),
      );
    }
  }

  return (
    <form
      onSubmit={(e) => void handleSubmit(e)}
      className="flex flex-col gap-4 pb-6"
    >
      {/* Код оффера — immutable при редактировании */}
      <Input
        inputRef={codeRef}
        label="Код оффера"
        placeholder="CR2_GH"
        value={code}
        onChange={(e) => setCode(e.target.value.toUpperCase())}
        disabled={isEdit}
        errorMessage={error ?? undefined}
      />

      {/* Кабинеты (мульти-кабинет): числовые ID через запятую, минимум 1 */}
      <Input
        inputRef={accountsRef}
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

      <Input
        label="FB Pixel ID"
        placeholder="1234567890123456"
        value={pixelId}
        onChange={(event) => setPixelId(event.target.value)}
        inputMode="numeric"
        autoComplete="off"
        autoCorrect="off"
      />

      {/* Гео оффера (ISO-2 upper, опц.) — визард префиллит ими countries */}
      <Input
        inputRef={countriesRef}
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

      <div className="border-t border-[var(--color-hairline)] pt-3">
        <Switch
          id={switchId}
          label="Активен"
          checked={isActive}
          onChange={(e) => setIsActive(e.target.checked)}
        />
      </div>

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

// ─── Debounce (preview не дёргаем на каждый тик ползунка) — зеркало web ──────────

function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const id = window.setTimeout(() => setV(value), ms);
    return () => window.clearTimeout(id);
  }, [value, ms]);
  return v;
}

function ThresholdsForm({ offerId, onClose }: ThresholdsFormProps) {
  const { data: rules, isLoading } = useOfferRules(offerId);
  const updateRules = useUpdateOfferRules();

  const [values, setValues] = useState<OfferRulesValues>({
    ...DEFAULT_OFFER_RULES_VALUES,
  });
  const [frequency, setFrequency] = useState("");
  const [initialized, setInitialized] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  // Инициализируем значения при загрузке данных
  if (rules && !initialized) {
    setValues(rulesValuesFromOut(rules));
    setFrequency(rules.frequency_threshold ?? "");
    setInitialized(true);
  }

  const cpaValid = isOfferCpaValid(values.cpa);
  const currencyValid = isOfferCurrencyValid(values.currency);

  // Дебаунсим связку cpa/stop/warning → меньше запросов при движении ползунка.
  const debounced = useDebounced(
    {
      cpa: cpaValid ? values.cpa.trim() : null,
      currency: values.currency.trim().toUpperCase(),
      stop: values.stop_percent_of_rule,
      warning: values.warning_percent_of_stop,
    },
    250,
  );
  const preview = useRulesPreview({
    cpa: debounced.cpa,
    currency: debounced.currency,
    stop_percent_of_rule: debounced.stop,
    warning_percent_of_stop: debounced.warning,
  });

  async function handleSave() {
    haptic.impact("medium");
    setSaveError(null);
    let payload;
    try {
      payload = rulesValuesToPayload(values, frequency);
    } catch {
      setSaveError("Проверьте CPA, валюту и проценты правила");
      return;
    }
    try {
      await updateRules.mutateAsync({ offerId, payload });
      haptic.notify("success");
      onClose();
    } catch (err) {
      haptic.notify("error");
      setSaveError(
        safeApiProblemMessage(err, "Пороги не сохранены. Повторите попытку."),
      );
    }
  }

  if (isLoading) {
    return (
      <div className="flex flex-col gap-3 pb-6">
        {Array.from({ length: 6 }, (_, i) => (
          <Skeleton key={i} className="h-14" />
        ))}
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-5 pb-6">
      <div
        className="border-y border-[var(--color-hairline)] px-1 py-3"
        role="status"
      >
        <div className="text-[12px] text-bg-8">Валюта бюджета</div>
        <div className="mt-1 font-numeric text-[16px] text-bg-11">
          {currencyValid ? "$ · доллар США" : "Нужна конфигурация USD"}
        </div>
      </div>
      <Input
        label={`CPA ставка${currencyValid ? " ($)" : ""}`}
        placeholder="10"
        type="text"
        inputMode="decimal"
        value={values.cpa}
        onChange={(e) =>
          setValues((prev) => ({ ...prev, cpa: e.target.value }))
        }
      />
      <p className="text-[12px] text-bg-8 -mt-3">
        Целевая цена действия (FTD/депозит). От неё автоматически считаются
        стоп-пороги.
      </p>

      <Slider
        label="Стоп — % от правила"
        value={values.stop_percent_of_rule}
        onChange={(v) =>
          setValues((prev) => ({ ...prev, stop_percent_of_rule: v }))
        }
        hint="100% = базовое правило. Меньше — стоп срабатывает раньше (жёстче)."
      />
      <Slider
        label="Warning — % от стопа"
        value={values.warning_percent_of_stop}
        onChange={(v) =>
          setValues((prev) => ({ ...prev, warning_percent_of_stop: v }))
        }
        hint="Ранний сигнал: warning = этот % от стоп-порога."
      />

      <div className="border-t border-[var(--color-hairline)] pt-4 flex flex-col gap-4">
        <Input
          label="Клик (CPC): % от CPA"
          placeholder="2"
          type="text"
          inputMode="decimal"
          value={values.cpc_percent_of_cpa}
          onChange={(e) =>
            setValues((prev) => ({ ...prev, cpc_percent_of_cpa: e.target.value }))
          }
        />
        <Input
          label="Лид (CPL): % от CPA"
          placeholder="10"
          type="text"
          inputMode="decimal"
          value={values.cpl_percent_of_cpa}
          onChange={(e) =>
            setValues((prev) => ({ ...prev, cpl_percent_of_cpa: e.target.value }))
          }
        />
        <Input
          label="Регистрация (CPR): % от CPA"
          placeholder="20"
          type="text"
          inputMode="decimal"
          value={values.cpr_percent_of_cpa}
          onChange={(e) =>
            setValues((prev) => ({ ...prev, cpr_percent_of_cpa: e.target.value }))
          }
        />
        <Input
          label="Регистраций без депозита (стоп, штук)"
          placeholder="5"
          type="text"
          inputMode="numeric"
          value={values.regs_no_dep_stop_count}
          onChange={(e) =>
            setValues((prev) => ({ ...prev, regs_no_dep_stop_count: e.target.value }))
          }
        />

        <div className="flex flex-col gap-1.5">
          <label className="text-[12px] font-display tracking-wider uppercase text-bg-9">
            Спенд без депозита (% от CPA, от — до)
          </label>
          <p className="text-[12px] text-bg-8 -mt-1">Дефолт: 50 — 70</p>
          <div className="flex items-center gap-2">
            <div className="flex-1">
              <Input
                type="text"
                inputMode="decimal"
                placeholder="50"
                value={values.spend_no_dep_from_percent}
                onChange={(e) =>
                  setValues((prev) => ({ ...prev, spend_no_dep_from_percent: e.target.value }))
                }
              />
            </div>
            <span className="text-bg-9">—</span>
            <div className="flex-1">
              <Input
                type="text"
                inputMode="decimal"
                placeholder="70"
                value={values.spend_no_dep_to_percent}
                onChange={(e) =>
                  setValues((prev) => ({ ...prev, spend_no_dep_to_percent: e.target.value }))
                }
              />
            </div>
          </div>
        </div>

        <div className="flex flex-col gap-1.5">
          <label className="text-[12px] font-display tracking-wider uppercase text-bg-9">
            Спенд с депозитом (% от CPA, от — до)
          </label>
          <p className="text-[12px] text-bg-8 -mt-1">Дефолт: 70 — 90</p>
          <div className="flex items-center gap-2">
            <div className="flex-1">
              <Input
                type="text"
                inputMode="decimal"
                placeholder="70"
                value={values.spend_with_dep_from_percent}
                onChange={(e) =>
                  setValues((prev) => ({ ...prev, spend_with_dep_from_percent: e.target.value }))
                }
              />
            </div>
            <span className="text-bg-9">—</span>
            <div className="flex-1">
              <Input
                type="text"
                inputMode="decimal"
                placeholder="90"
                value={values.spend_with_dep_to_percent}
                onChange={(e) =>
                  setValues((prev) => ({ ...prev, spend_with_dep_to_percent: e.target.value }))
                }
              />
            </div>
          </div>
        </div>

        <Input
          label="Минимальный знаменатель отношения"
          placeholder="100"
          type="text"
          inputMode="numeric"
          value={values.min_ratio_denominator}
          onChange={(e) =>
            setValues((prev) => ({ ...prev, min_ratio_denominator: e.target.value }))
          }
        />
      </div>

      <RulesPreview
        loading={preview.isLoading || preview.isFetching}
        data={preview.data}
        cpaValid={cpaValid}
        currencyValid={currencyValid}
      />

      <div className="border-t border-[var(--color-hairline)] pt-4">
        <Input
          label="Frequency порог"
          placeholder="—"
          type="number"
          min="0.01"
          step="any"
          value={frequency}
          onChange={(e) => setFrequency(e.target.value)}
        />
        <p className="text-[12px] text-bg-8 mt-1">
          Максимальная частота показа — независимый порог (не связан с
          CPA-расчётом).
        </p>
      </div>

      {saveError ? (
        <p
          role="alert"
          className="m-0 rounded-[var(--radius-2)] bg-danger-bg p-3 text-[14px] text-danger"
        >
          {saveError}
        </p>
      ) : null}

      <div className="sticky bottom-0 z-10 -mx-1 bg-bg-1/95 px-1 pb-[max(4px,var(--tg-content-safe-bottom,0px))] pt-2 backdrop-blur">
        <Button
          onClick={() => void handleSave()}
          loading={updateRules.isPending}
          fullWidth
        >
          Сохранить пороги
        </Button>
      </div>
    </div>
  );
}

// ─── Живая разбивка порогов ─────────────────────────────────────────────────────

type PreviewData = NonNullable<ReturnType<typeof useRulesPreview>["data"]>;

function RulesPreview({
  loading,
  data,
  cpaValid,
  currencyValid,
}: {
  loading: boolean;
  data: PreviewData | undefined;
  cpaValid: boolean;
  currencyValid: boolean;
}) {
  if (!cpaValid || !currencyValid) {
    return (
      <div className="rounded-[var(--radius-2)] border border-[var(--color-hairline)] p-3 text-[14px] leading-5 text-bg-9">
        {!currencyValid
          ? "Настройка оффера не в USD. Исправьте adoption bundle до запуска."
          : "Укажите CPA — покажу, при какой цене сработают стоп и warning по каждой метрике."}
      </div>
    );
  }
  if (loading && !data) {
    return <Skeleton className="h-40" />;
  }
  if (!data) return null;

  return (
    <div className="border border-[var(--color-hairline)] rounded-[var(--radius-2)] p-3">
      <div className="font-display text-[12px] tracking-[0.12em] uppercase text-bg-8 mb-3">
        ПРИ КАКОЙ ЦЕНЕ СРАБОТАЕТ
      </div>

      {/* Денежные правила: CPC / CPL / CPR */}
      <table className="w-full text-[14px]">
        <thead>
          <tr className="text-bg-8 font-display text-[12px] tracking-wider uppercase">
            <th className="text-left font-normal pb-1.5">Метрика</th>
            <th className="text-right font-normal pb-1.5">Warning</th>
            <th className="text-right font-normal pb-1.5">Стоп</th>
          </tr>
        </thead>
        <tbody>
          {data.cost_rules.map((r) => (
            <tr
              key={r.rule}
              className="border-t border-[var(--color-hairline)]"
            >
              <td className="py-1.5 text-bg-10">{r.label}</td>
              <td className="py-1.5 text-right font-display tabular-nums text-warning">
                {formatSpend(r.warning, data.currency)}
              </td>
              <td className="py-1.5 text-right font-display tabular-nums text-danger">
                {formatSpend(r.stop, data.currency)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {/* Диапазоны расхода без/с депозитом */}
      {data.spend_ranges.length > 0 && (
        <div className="mt-3 pt-3 border-t border-[var(--color-hairline)] flex flex-col gap-1.5">
          {data.spend_ranges.map((s) => (
            <div
              key={s.rule}
              className="flex items-center justify-between gap-3 text-[14px]"
            >
              <span className="text-bg-10">{s.label}</span>
              <span className="font-display tabular-nums text-bg-11">
                {formatSpend(s.stop_from, data.currency)}–
                {formatSpend(s.stop_to, data.currency)}
              </span>
            </div>
          ))}
        </div>
      )}

      <div className="mt-3 text-[12px] text-bg-8">
        {data.regs_no_dep_stop_count} регистраций без депозитов → стоп.
      </div>
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
  const accounts = offer.ad_account_ids ?? [];

  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "w-full text-left border bg-bg-1 min-h-[44px] rounded-[var(--radius-2)]",
        "flex items-center gap-3 px-4 py-3.5",
        "active:bg-bg-2 transition-colors duration-[var(--dur-base)]",
        isActive
          ? "border-[var(--color-hairline)]"
          : "border-[var(--color-hairline)] opacity-70",
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
        </div>
        {offer.name && offer.name !== offer.code ? (
          <p className="text-[12px] text-bg-9 mt-0.5 truncate">{offer.name}</p>
        ) : null}
        {/* Мульти-кабинет: кабинеты оффера; пусто = warning (оффер вне скана) */}
        {isActive && accounts.length === 0 ? (
          <p className="text-[12px] text-warning mt-0.5">
            кабинеты не заданы — не сканируется
          </p>
        ) : accounts.length > 0 ? (
          <p
            className="font-display tabular-nums text-[12px] text-bg-8 mt-0.5 truncate"
            title={accounts.join(", ")}
          >
            каб:{" "}
            {accounts
              .map((a) => (a.length > 8 ? `…${a.slice(-6)}` : a))
              .join(" · ")}
          </p>
        ) : null}
      </div>

      {/* Правая часть: Badge активности + chevron */}
      <div className="flex items-center gap-2 shrink-0">
        <Badge variant={isActive ? "done" : "disabled"} size="sm" withDot>
          {isActive ? "Активен" : "Выключен"}
        </Badge>
        <ChevronRight size={14} strokeWidth={1.5} className="text-bg-8" />
      </div>
    </button>
  );
}

// ─── Detail-sheet содержимое ──────────────────────────────────────────────────

interface OfferDetailProps {
  offer: Offer;
  onEdit: () => void;
  onThresholds: () => void;
  onToggleActive: () => void;
  isToggling: boolean;
  canEdit: boolean;
  toggleArmed: boolean;
}

function OfferDetail({
  offer,
  onEdit,
  onThresholds,
  onToggleActive,
  isToggling,
  canEdit,
  toggleArmed,
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
            {offer.is_active ? "Активен" : "Выключен"}
          </Badge>
        </div>
        {offer.name && offer.name !== offer.code ? (
          <p className="text-[13px] text-bg-9 mt-1">{offer.name}</p>
        ) : null}
      </div>

      {/* Кнопки действий */}
      {!canEdit ? (
        <p
          role="status"
          className="m-0 border-y border-[var(--color-hairline)] py-3 text-[14px] text-warning"
        >
          Изменять офферы может только владелец.
        </p>
      ) : null}
      <div className="grid grid-cols-2 gap-2">
        <Button
          variant="secondary"
          onClick={onEdit}
          disabled={!canEdit}
          fullWidth
        >
          Редактировать
        </Button>
        <Button
          variant="secondary"
          onClick={onThresholds}
          disabled={!canEdit}
          fullWidth
        >
          Пороги
        </Button>
        <Button
          variant="secondary"
          onClick={onToggleActive}
          loading={isToggling}
          disabled={!canEdit}
          fullWidth
          className="col-span-2"
        >
          {toggleArmed
            ? `Подтвердить ${offer.is_active ? "выключение" : "включение"}`
            : offer.is_active
              ? "Выключить"
              : "Включить"}
        </Button>
      </div>
    </div>
  );
}

// ─── OffersPage ────────────────────────────────────────────────────────────────

type SheetMode = "detail" | "edit" | "create" | "thresholds" | null;

function OffersPage() {
  const navigate = useNavigate();
  const { filter } = Route.useSearch();
  const canEdit = getStoredRole() === "owner";
  const { data: offers, isLoading, isError, error, refetch } = useOffers();
  const updateOffer = useUpdateOffer();

  const [selected, setSelected] = useState<Offer | null>(null);
  const [sheetMode, setSheetMode] = useState<SheetMode>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [toggleArmed, setToggleArmed] = useState(false);

  function openDetail(offer: Offer) {
    setActionError(null);
    setToggleArmed(false);
    setSelected(offer);
    setSheetMode("detail");
    haptic.selection();
  }

  function closeSheet() {
    setSheetMode(null);
    setToggleArmed(false);
    // selected не сбрасываем — нужен при detail→edit переходе
  }

  async function handleToggleActive() {
    if (!selected || !canEdit) return;
    if (!toggleArmed) {
      setToggleArmed(true);
      return;
    }
    setActionError(null);
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
      setActionError(
        safeApiProblemMessage(
          err,
          "Статус оффера не изменён. Повторите попытку.",
        ),
      );
    }
  }

  // Заголовки для sheet по режиму
  const sheetEyebrow: Record<NonNullable<SheetMode>, string> = {
    detail: "КАТАЛОГ · ДЕТАЛИ",
    edit: "КАТАЛОГ · РЕДАКТИРОВАНИЕ",
    create: "КАТАЛОГ · СОЗДАНИЕ",
    thresholds: "КАТАЛОГ · СТОП-ПРАВИЛА",
  };
  const sheetTitle: Record<NonNullable<SheetMode>, string> = {
    detail: selected?.code ?? "Оффер",
    edit: `Редактировать ${selected?.code ?? ""}`,
    create: "Новый оффер",
    thresholds: `Пороги: ${selected?.code ?? ""}`,
  };

  // Счётчик для eyebrow правой кнопки
  const activeCount = (offers ?? []).filter((o) => o.is_active).length;
  const filteredOffers = (offers ?? []).filter((offer) => {
    if (filter === "active") return offer.is_active;
    if (filter === "inactive") return !offer.is_active;
    return true;
  });

  return (
    <div className="flex flex-col min-h-full pb-20">
      {/* Шапка */}
      <MiniHeader
        eyebrowNum="02"
        eyebrow="КАТАЛОГ · ОФФЕРЫ"
        title="Офферы"
        right={
          <Button
            size="sm"
            variant="secondary"
            disabled={!canEdit}
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

      {!canEdit ? (
        <p
          role="status"
          className="mx-4 mt-3 border-y border-[var(--color-hairline)] py-3 text-[14px] text-warning"
        >
          Каталог доступен только для чтения. Изменения разрешены владельцу.
        </p>
      ) : null}

      {actionError ? (
        <p
          role="alert"
          className="mx-4 mt-3 rounded-[var(--radius-2)] border border-danger/40 bg-danger-bg p-3 text-[14px] text-danger"
        >
          {actionError}
        </p>
      ) : null}

      {!isLoading && !isError ? (
        <div
          className="flex gap-2 overflow-x-auto px-4 pb-1 pt-3"
          aria-label="Фильтр офферов"
        >
          {(
            [
              ["all", "Все"],
              ["active", "Активные"],
              ["inactive", "Выключенные"],
            ] as const
          ).map(([value, label]) => (
            <button
              key={value}
              type="button"
              aria-pressed={filter === value}
              onClick={() =>
                void navigate({ to: "/offers", search: { filter: value } })
              }
              className={`min-h-11 shrink-0 rounded-full px-4 text-[13px] ${
                filter === value
                  ? "bg-accent text-bg-0"
                  : "border border-[var(--color-hairline)] text-bg-10"
              }`}
            >
              {label}
            </button>
          ))}
        </div>
      ) : null}

      {/* Подзаголовок-счётчик */}
      {!isLoading && !isError && (offers ?? []).length > 0 ? (
        <div className="px-4 pt-2 pb-1">
          <Eyebrow>
            Активных: {activeCount} · выключено:{" "}
            {(offers ?? []).length - activeCount}
          </Eyebrow>
        </div>
      ) : null}

      {/* Список */}
      <div className="px-4 pt-3 flex flex-col gap-2">
        {/* Загрузка */}
        {isLoading &&
          Array.from({ length: 4 }, (_, i) => (
            <Skeleton
              key={i}
              className="h-[60px] w-full rounded-[var(--radius-2)]"
            />
          ))}

        {/* Ошибка */}
        {isError && !isLoading && (
          <EmptyState
            title="Не удалось загрузить"
            description={safeApiProblemMessage(
              error,
              "Проверьте соединение и повторите",
            )}
            action={{ label: "Повторить", onClick: () => void refetch() }}
          />
        )}

        {/* Пусто */}
        {!isLoading && !isError && filteredOffers.length === 0 && (
          <EmptyState
            title={
              filter === "active"
                ? "Активных офферов нет"
                : filter === "inactive"
                  ? "Выключенных офферов нет"
                  : "Офферов нет"
            }
            description={
              filter === "all"
                ? "Создайте первый оффер для настройки стоп-правил"
                : "В выбранном состоянии офферов нет. Вернитесь к полному каталогу."
            }
            action={
              filter !== "all"
                ? {
                    label: "Показать все",
                    onClick: () =>
                      void navigate({
                        to: "/offers",
                        search: { filter: "all" },
                      }),
                  }
                : canEdit
                  ? {
                      label: "Создать оффер",
                      onClick: () => {
                        setSelected(null);
                        setSheetMode("create");
                      },
                    }
                  : undefined
            }
          />
        )}

        {/* Карточки офферов */}
        {!isLoading &&
          !isError &&
          filteredOffers.map((offer) => (
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
            onEdit={() => {
              setSheetMode("edit");
            }}
            onThresholds={() => {
              setSheetMode("thresholds");
            }}
            onToggleActive={() => void handleToggleActive()}
            isToggling={updateOffer.isPending}
            canEdit={canEdit}
            toggleArmed={toggleArmed}
          />
        ) : null}

        {sheetMode === "edit" || sheetMode === "create" ? (
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
