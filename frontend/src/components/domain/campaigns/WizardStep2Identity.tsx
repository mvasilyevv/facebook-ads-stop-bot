/**
 * Шаг 2 — Идентичность + Оффер.
 *
 * Поля: ad_account_ids, page_id, pixel_id, offer_code, byer_tag.
 * Если шаг 1 был "preset" — поля предзаполнены из пресета.
 *
 * Дерайв из оффера: при выборе offer_code, совпавшего с оффером из useOffers,
 * кабинеты выбираются только из offer_ad_accounts, pixel_id и goal.countries
 * дерайвятся из оффера. Первый выбранный кабинет используется для preview/page
 * context; launch повторно валидирует каждый кабинет независимо.
 *
 * IANA timezone, currency и точность денег приходят только из свежего durable
 * account snapshot. Клиент не может их редактировать или подменить.
 *
 * Тем же blur'ом (общий дедуп) тянем список FB-страниц кабинета: если подтянулись —
 * page_id выбирается дропдауном, иначе остаётся ручной ввод ID.
 */

import { useEffect, useRef, useState, type FC } from "react";
import { validateCampaignIdentity } from "@fb/features/campaigns";
import { ChoiceTagListInput } from "@fb/operator-ui";
import { AlertTriangle, CheckCircle2, Loader2 } from "lucide-react";
import { Input } from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";
import { useAdAccountContext, useAdAccountPages } from "@/lib/api/campaigns";
import { useOffers } from "@/lib/api/offers";
import type { Offer } from "@fb/shared";
import type { WizardGoal, WizardIdentity } from "@/stores/campaignWizard";
import { formatRussianCount } from "@/lib/utils/russianCount";

interface WizardStep2IdentityProps {
  values: WizardIdentity;
  onChange: (v: Partial<WizardIdentity>) => void;
  /** Префилл полей шага «Параметры» (goal.countries) при дерайве из оффера. */
  onGoalChange?: (v: Partial<WizardGoal>) => void;
  /** Ошибки валидации по именам полей. */
  errors?: Partial<Record<keyof WizardIdentity, string>>;
}

export const WizardStep2Identity: FC<WizardStep2IdentityProps> = ({
  values,
  onChange,
  onGoalChange,
  errors = {},
}) => {
  const contextMutation = useAdAccountContext();
  const pagesMutation = useAdAccountPages();
  const offersQuery = useOffers();
  // Подтянутые страницы кабинета → дропдаун выбора page_id. Пусто/ошибка → ручной ввод.
  const [pages, setPages] = useState<{ id: string; name: string }[]>([]);
  // Durable context read is cheap; pages still use the live read channel.
  const lastFetchedAct = useRef<string | null>(null);

  const offers: Offer[] = offersQuery.data ?? [];
  const selectedOffer = offers.find((offer) => offer.code === values.offer_code);
  const offerAccounts = (selectedOffer?.ad_account_ids ?? []).filter(
    (accountId) => accountId.trim().length > 0,
  );

  const prefillOfferCpa = (currency: string, offerCode: string) => {
    if (!onGoalChange) return;
    const offer = offers.find((candidate) => candidate.code === offerCode);
    const offerCurrency = offer?.currency?.trim().toUpperCase();
    const cpa = offer?.cpa_threshold?.trim() ?? "";
    if (offerCurrency === currency && /^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$/.test(cpa)) {
      onGoalChange({ bid_amount: cpa });
    }
  };

  // Подтягиваем authoritative account context и список страниц по конкретному act_id.
  const fetchAccountMetaFor = (rawActId: string) => {
    const actId = rawActId.trim();
    if (!/^(?:act_)?[0-9]+$/.test(actId) || actId === lastFetchedAct.current) return;
    lastFetchedAct.current = actId;
    contextMutation.mutate(actId, {
      onSuccess: (data) => {
        if (lastFetchedAct.current !== actId) return;
        onChange({
          account_context_state: data.state,
          timezone_name: data.timezone_name ?? "",
          currency: data.currency === "USD" ? "USD" : "",
          currency_exponent: data.currency_exponent === 2 ? 2 : null,
          account_context_observed_at: data.observed_at,
          account_context_issue: data.issue,
        });
        if (data.state === "ready" && data.currency) {
          prefillOfferCpa(data.currency, values.offer_code);
          if (data.next_start_date && onGoalChange) {
            onGoalChange({ start_date: data.next_start_date });
          }
        }
      },
      onError: () => {
        if (lastFetchedAct.current !== actId) return;
        lastFetchedAct.current = null;
        onChange({
          account_context_state: "unavailable",
          timezone_name: "",
          currency: "",
          currency_exponent: null,
          account_context_observed_at: null,
          account_context_issue: "account_context_request_failed",
        });
      },
    });
    pagesMutation.mutate(actId, {
      onSuccess: (data) => {
        if (lastFetchedAct.current !== actId) return;
        // Непустой массив → дропдаун; пустой → остаётся ручной ввод page_id.
        setPages(data.pages);
      },
      onError: () => {
        if (lastFetchedAct.current !== actId) return;
        // Не удалось подтянуть страницы — фолбэк на ручной ввод page_id.
        setPages([]);
      },
    });
  };

  // A preset can populate act_id without a blur. Debounce typing, but resolve it
  // automatically as soon as the numeric account ID is complete.
  useEffect(() => {
    const timer = window.setTimeout(() => fetchAccountMetaFor(values.act_id), 300);
    return () => window.clearTimeout(timer);
    // Mutations are intentionally deduplicated by lastFetchedAct.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [values.act_id]);

  // Дерайв из выбранного оффера: authoritative accounts, pixel_id, goal.countries.
  const deriveFromOffer = (code: string) => {
    const offer = offers.find((o) => o.code === code);
    if (!offer) {
      onChange({ ad_account_ids: [], act_id: "" });
      return;
    }
    const accounts = (offer.ad_account_ids ?? []).filter((a) => a.trim().length > 0);
    // Один кабинет выбирается сразу; несколько требуют явного multi-select.
    const soleAccount = accounts.length === 1 ? accounts[0] : null;

    const patch: Partial<WizardIdentity> = {
      ad_account_ids: soleAccount ? [soleAccount] : [],
      act_id: soleAccount ?? "",
      page_id: "",
      account_context_state: "unavailable",
      timezone_name: "",
      currency: "",
      currency_exponent: null,
      account_context_observed_at: null,
      account_context_issue: null,
    };
    if (offer.pixel_id) patch.pixel_id = offer.pixel_id;
    onChange(patch);

    // Гео оффера → префилл countries в шаге «Параметры» (редактируемо).
    if (onGoalChange && offer.countries && offer.countries.length > 0) {
      onGoalChange({ countries: offer.countries.map((c) => c.toUpperCase()) });
    }

    // CPA belongs to a currency. It is copied only after exact account-currency match.
    if (onGoalChange) {
      onGoalChange({ bid_amount: "" });
      if (values.account_context_state === "ready" && values.currency) {
        prefillOfferCpa(values.currency, code);
      }
    }

    // Авто-кабинет → сразу тянем его TZ и страницы (как при blur).
    if (soleAccount) fetchAccountMetaFor(soleAccount);
  };

  const handleAccountsChange = (accountIds: string[]) => {
    const actId = accountIds[0] ?? "";
    if (actId === values.act_id) {
      onChange({ ad_account_ids: accountIds });
      return;
    }
    lastFetchedAct.current = null;
    setPages([]);
    onChange({
      ad_account_ids: accountIds,
      act_id: actId,
      page_id: "",
      account_context_state: "unavailable",
      timezone_name: "",
      currency: "",
      currency_exponent: null,
      account_context_observed_at: null,
      account_context_issue: null,
    });
    if (actId) fetchAccountMetaFor(actId);
  };

  const offerCurrency = selectedOffer?.currency?.trim().toUpperCase() ?? "";
  const offerCurrencyMismatch =
    values.account_context_state === "ready" &&
    Boolean(selectedOffer?.cpa_threshold) &&
    (!offerCurrency || offerCurrency !== values.currency);

  return (
    <div className="space-y-6">
      {/* Заголовок */}
      <div>
        <div className="font-display text-[12px] tracking-[0.14em] uppercase text-bg-8 mb-1">
          ШАГ 2 · ИДЕНТИЧНОСТЬ
        </div>
        <h2 className="font-display text-[20px] font-medium text-bg-11 leading-tight m-0">
          Кабинет и оффер
        </h2>
        <p className="text-[13px] text-bg-9 mt-1">
          Сначала выберите оффер — кабинет, пиксель, гео и целевой CPA подтянутся автоматически.
          Страницу укажете ниже.
        </p>
      </div>

      {/* Оффер — первым: выбор оффера дерайвит кабинет/пиксель/гео/CPA */}
      <div>
        <div className="font-display text-[12px] tracking-[0.14em] uppercase text-bg-8 mb-3">
          ОФФЕР И БАЙЕР
        </div>
        {/* Комбобокс-подсказки из активных офферов (вне grid — datalist не занимает место). */}
        <datalist id="offers-dl">
          {(offersQuery.data ?? []).map((o) => (
            <option key={o.id} value={o.code}>
              {o.name}
            </option>
          ))}
        </datalist>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          {/* Свободный ввод разрешён, .toUpperCase() сохраняется. Совпадение
              с оффером каталога → дерайв act_id/pixel/countries/CPA. */}
          <Input
            label="Код оффера"
            placeholder="GH_CR2"
            value={values.offer_code}
            onChange={(e) => {
              const code = e.target.value.toUpperCase();
              onChange({ offer_code: code });
              deriveFromOffer(code);
            }}
            errorMessage={errors.offer_code}
            helpText="Войдёт в название кампании"
            list="offers-dl"
          />
          <Input
            label="Тег байера"
            placeholder="MV"
            value={values.byer_tag}
            onChange={(e) => onChange({ byer_tag: e.target.value.toUpperCase() })}
            errorMessage={errors.byer_tag}
            helpText="Опционально — для фильтра owner_campaign_tag"
          />
        </div>
      </div>

      {/* Кабинет */}
      <div>
        <div className="font-display text-[12px] tracking-[0.14em] uppercase text-bg-8 mb-3">
          РЕКЛАМНЫЙ КАБИНЕТ
        </div>
        <div className="mb-4">
          <ChoiceTagListInput
            label="Кабинеты оффера"
            values={values.ad_account_ids ?? []}
            options={offerAccounts.map((accountId) => ({
              value: accountId,
              label: `act_${accountId}`,
            }))}
            onChange={handleAccountsChange}
            placeholder="Добавить кабинет оффера"
            selectAllLabel={offerAccounts.length > 1 ? "Выбрать все" : undefined}
            errorMessage={errors.ad_account_ids ?? errors.act_id}
            disabled={offerAccounts.length === 0}
            helpText="Каждый выбранный кабинет получит отдельный run; первый используется для preview и списка страниц."
          />
        </div>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <div className="flex flex-col gap-1.5">
            <label className="text-[12px] font-display tracking-wider uppercase text-bg-9">
              Контекст кабинета
            </label>
            <div
              className={[
                "min-h-20 rounded-[var(--radius-2)] border px-3 py-2.5 text-[13px]",
                values.account_context_state === "ready"
                  ? "border-success/35 bg-success/10"
                  : values.account_context_state === "stale"
                    ? "border-warning/35 bg-warning/10"
                    : "border-[var(--color-hairline-strong)] bg-bg-2",
              ].join(" ")}
              role="status"
            >
              {contextMutation.isPending ? (
                <div className="flex items-center gap-2 text-bg-9">
                  <Loader2 aria-hidden="true" size={14} className="animate-spin" />
                  Проверяю снимок кабинета…
                </div>
              ) : values.account_context_state === "ready" ? (
                <>
                  <div className="flex items-center gap-2 font-medium text-success">
                    <CheckCircle2 aria-hidden="true" size={14} />
                    Подтверждено
                  </div>
                  <div className="mt-1 text-bg-11">
                    {values.timezone_name} · {values.currency}
                  </div>
                  <div className="mt-1 text-[12px] text-bg-8">
                    Точность: {values.currency_exponent} · снимок{" "}
                    {formatObservedAt(values.account_context_observed_at)}
                  </div>
                </>
              ) : values.act_id.trim() ? (
                <>
                  <div className="flex items-center gap-2 font-medium text-warning">
                    <AlertTriangle aria-hidden="true" size={14} />
                    {values.account_context_state === "stale"
                      ? "Снимок устарел"
                      : "Контекст недоступен"}
                  </div>
                  <div className="mt-1 text-[12px] text-bg-9">
                    {/* Причина приходит из ответа ручки; без неё остаётся общая
                        формулировка, а не пустая строка. */}
                    {values.account_context_issue ??
                      "Запуск заблокирован до свежего подтверждения Meta."}
                  </div>
                </>
              ) : (
                <span className="text-bg-9">
                  Выберите кабинет оффера — часовой пояс и валюта подтянутся из снимка Meta
                </span>
              )}
            </div>
            {errors.account_context_state && (
              <span className="text-[12px] text-danger font-display">
                {errors.account_context_state}
              </span>
            )}
            {offerCurrencyMismatch && (
              <span className="text-[12px] text-danger font-display">
                Валюта CPA оффера ({offerCurrency || "не подтверждена"}) не совпадает с{" "}
                {values.currency}
              </span>
            )}
          </div>
          <div className="min-h-20 rounded-[var(--radius-2)] border border-[var(--color-hairline-strong)] bg-bg-2 px-3 py-2.5 text-[13px] text-bg-9">
            <span className="block font-display text-[12px] uppercase tracking-wider text-bg-8">
              Единица работы
            </span>
            <span className="mt-2 block">
              {(values.ad_account_ids ?? []).length > 0
                ? formatRussianCount(
                    values.ad_account_ids?.length ?? 0,
                    "отдельный запуск",
                    "отдельных запуска",
                    "отдельных запусков",
                  )
                : "Кабинеты ещё не выбраны"}
            </span>
          </div>
        </div>
      </div>

      {/* Страница и пиксель */}
      <div>
        <div className="font-display text-[12px] tracking-[0.14em] uppercase text-bg-8 mb-3">
          СТРАНИЦА И ПИКСЕЛЬ
        </div>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          {pagesMutation.isPending ? (
            // Спиннер во время фетча страниц — финальный контрол ещё неизвестен.
            <div className="flex flex-col gap-1.5">
              <label className="text-[12px] font-display tracking-wider uppercase text-bg-9">
                Facebook Page ID
              </label>
              <div className="flex h-8 items-center gap-2 rounded-[var(--radius-2)] border border-[var(--color-hairline-strong)] bg-bg-2 px-3 text-[13.5px] text-bg-9">
                <Loader2 aria-hidden="true" size={14} className="animate-spin text-bg-9" />
                <span>Подтягиваю страницы кабинета…</span>
              </div>
              {errors.page_id && (
                <span className="text-[12px] text-danger font-display">{errors.page_id}</span>
              )}
            </div>
          ) : pages.length > 0 ? (
            // Страницы подтянулись — выбор из дропдауна, value=id.
            <Select
              label="Facebook Page"
              placeholder="Выберите страницу"
              options={pages.map((p) => ({ value: p.id, label: `${p.name} — ${p.id}` }))}
              value={values.page_id}
              onChange={(e) => onChange({ page_id: e.target.value })}
              errorMessage={errors.page_id}
            />
          ) : (
            // Фетч упал / страниц нет — ручной ввод ID с подсказкой.
            <Input
              label="Facebook Page ID"
              placeholder="123456789"
              value={values.page_id}
              onChange={(e) => onChange({ page_id: e.target.value })}
              errorMessage={errors.page_id}
              helpText="Не удалось подтянуть — введите ID вручную"
            />
          )}
          <Input
            label="FB Pixel ID"
            placeholder="123456789"
            value={values.pixel_id}
            onChange={(e) => onChange({ pixel_id: e.target.value })}
            errorMessage={errors.pixel_id}
          />
        </div>
      </div>
    </div>
  );
};

function formatObservedAt(value: string | null): string {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "—";
  return parsed.toLocaleString("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

// ─── Валидация ────────────────────────────────────────────────────────────────

export function validateIdentity(
  values: WizardIdentity,
): Partial<Record<keyof WizardIdentity, string>> {
  return validateCampaignIdentity(values);
}
