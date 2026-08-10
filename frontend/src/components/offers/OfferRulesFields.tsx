/**
 * OfferRulesFields — money-настройки оффера: CPA + 2 ползунка чувствительности
 * + живая разбивка стоп-порогов.
 *
 * Контролируемый блок (values + onChange, без собственных мутаций) — переиспользуется
 * в форме создания/редактирования оффера и в drawer'е правил.
 *
 * Авторасчёт: CPA → базовые правила (CPC 2% / CPL 10% / CPR 20% от CPA) → ×stop% → ×warning%.
 * Эти же пороги применяет observer (единый RuleContext), поэтому live-разбивка из
 * GET /offers/rules/preview ТОЧНО совпадает с реальными срабатываниями.
 */
import { useEffect, useState } from "react";
import { isOfferCpaValid, isOfferCurrencyValid, type OfferRulesValues } from "@fb/features/offers";
import { formatSpend } from "@fb/shared/format/number";
import { Input } from "@/components/ui/Input";
import { Slider } from "@/components/ui/Slider";
import { Skeleton } from "@/components/ui/Skeleton";
import { useRulesPreview } from "@/lib/api/offers";
export {
  DEFAULT_OFFER_RULES_VALUES,
  rulesValuesFromOut,
  rulesValuesToPayload,
  type OfferRulesValues,
} from "@fb/features/offers";

interface Props {
  values: OfferRulesValues;
  onChange: (patch: Partial<OfferRulesValues>) => void;
  disabled?: boolean;
}

// ─── Debounce (preview не дёргаем на каждый тик ползунка) ────────────────────────

function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const id = window.setTimeout(() => setV(value), ms);
    return () => window.clearTimeout(id);
  }, [value, ms]);
  return v;
}

// ─── Компонент ────────────────────────────────────────────────────────────────

export function OfferRulesFields({ values, onChange, disabled }: Props) {
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

  return (
    <div className="flex flex-col gap-5">
      <div className="border-y border-[var(--color-hairline)] px-1 py-3" role="status">
        <div className="text-[12px] text-bg-8">Валюта бюджета</div>
        <div className="mt-1 font-numeric text-[16px] text-bg-11">
          {currencyValid ? "$ · доллар США" : "Нужна конфигурация USD"}
        </div>
      </div>
      <Input
        id="offer-cpa"
        type="text"
        inputMode="decimal"
        label="CPA ставка"
        placeholder="10"
        value={values.cpa}
        onChange={(e) => onChange({ cpa: e.target.value })}
        disabled={disabled}
        leftIcon={<span className="text-[12px] text-bg-9">{currencyValid ? "$" : "!"}</span>}
        helpText="Целевая цена действия (FTD/депозит). От неё автоматически считаются стоп-пороги."
      />

      <Slider
        label="Стоп — % от правила"
        value={values.stop_percent_of_rule}
        onChange={(v) => onChange({ stop_percent_of_rule: v })}
        disabled={disabled}
        hint="100% = базовое правило. Меньше — стоп срабатывает раньше (жёстче)."
      />
      <Slider
        label="Warning — % от стопа"
        value={values.warning_percent_of_stop}
        onChange={(v) => onChange({ warning_percent_of_stop: v })}
        disabled={disabled}
        hint="Ранний сигнал: warning = этот % от стоп-порога."
      />

      <RulesPreview
        loading={preview.isLoading || preview.isFetching}
        data={preview.data}
        cpaValid={cpaValid}
        currencyValid={currencyValid}
      />
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
      <div
        className="border border-[var(--color-hairline)] rounded-[var(--radius-2)] text-[12px] text-bg-9"
        style={{ padding: "var(--space-4)" }}
      >
        {!currencyValid
          ? "Настройка оффера не в USD. Исправьте adoption bundle до запуска."
          : "Укажите CPA — покажу, при какой цене сработают стоп и warning по каждой метрике."}
      </div>
    );
  }
  if (loading && !data) {
    return <Skeleton height={160} />;
  }
  if (!data) return null;

  return (
    <div
      className="border border-[var(--color-hairline)] rounded-[var(--radius-2)]"
      style={{ padding: "var(--space-4)" }}
    >
      <div className="font-display text-[12px] tracking-[0.12em] uppercase text-bg-8 mb-3">
        ПРИ КАКОЙ ЦЕНЕ СРАБОТАЕТ
      </div>

      {/* Денежные правила: CPC / CPL / CPR */}
      <table className="w-full text-[12.5px]">
        <thead>
          <tr className="text-bg-8 font-display text-[12px] tracking-wider uppercase">
            <th className="text-left font-normal pb-1.5">Метрика</th>
            <th className="text-right font-normal pb-1.5">Warning</th>
            <th className="text-right font-normal pb-1.5">Стоп</th>
          </tr>
        </thead>
        <tbody>
          {data.cost_rules.map((r) => (
            <tr key={r.rule} className="border-t border-[var(--color-hairline)]">
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
            <div key={s.rule} className="flex items-center justify-between text-[12px]">
              <span className="text-bg-10">{s.label}</span>
              <span className="font-display tabular-nums text-bg-11">
                {formatSpend(s.stop_from, data.currency)}–{formatSpend(s.stop_to, data.currency)}
              </span>
            </div>
          ))}
        </div>
      )}

      <div className="mt-3 text-[12px] text-bg-8">
        {data.regs_no_dep_stop_count} регистраций без депозитов → стоп. Базовые проценты правил (CPC
        2% / CPL 10% / CPR 20% от CPA) фиксированы.
      </div>
    </div>
  );
}
