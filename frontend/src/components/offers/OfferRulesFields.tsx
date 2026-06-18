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
import { Input } from "@/components/ui/Input";
import { Slider } from "@/components/ui/Slider";
import { Skeleton } from "@/components/ui/Skeleton";
import { useRulesPreview } from "@/lib/api/offers";
import type { OfferRules } from "@fb/shared";

// ─── Значения блока ────────────────────────────────────────────────────────────

export interface OfferRulesValues {
  /** CPA как строка (для number-input). Пусто → правила неактивны (нет авторасчёта). */
  cpa: string;
  /** Стоп = N% от базового правила (1–100, дефолт 80). */
  stop_percent_of_rule: number;
  /** Warning = M% от стопа (1–100, дефолт 80). */
  warning_percent_of_stop: number;
}

export const DEFAULT_OFFER_RULES_VALUES: OfferRulesValues = {
  cpa: "",
  stop_percent_of_rule: 80,
  warning_percent_of_stop: 80,
};

/** OfferRulesValues → payload для PUT /offers/{id}/rules. cpa пусто → null (снять CPA). */
export function rulesValuesToPayload(v: OfferRulesValues): Partial<OfferRules> {
  const cpa = v.cpa.trim();
  return {
    cpa_threshold: cpa ? cpa : null,
    stop_percent_of_rule: String(v.stop_percent_of_rule),
    warning_percent_of_stop: String(v.warning_percent_of_stop),
  };
}

/** OfferRuleOut (с бэка) → OfferRulesValues для формы. NULL/пусто → дефолт 80/80. */
export function rulesValuesFromOut(rules: OfferRules | null | undefined): OfferRulesValues {
  if (!rules) return DEFAULT_OFFER_RULES_VALUES;
  return {
    cpa: rules.cpa_threshold ?? "",
    stop_percent_of_rule: rules.stop_percent_of_rule ? Number(rules.stop_percent_of_rule) : 80,
    warning_percent_of_stop: rules.warning_percent_of_stop
      ? Number(rules.warning_percent_of_stop)
      : 80,
  };
}

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
  const cpaNum = parseFloat(values.cpa);
  const cpaValid = Number.isFinite(cpaNum) && cpaNum > 0;

  // Дебаунсим связку cpa/stop/warning → меньше запросов при движении ползунка.
  const debounced = useDebounced(
    {
      cpa: cpaValid ? cpaNum : null,
      stop: values.stop_percent_of_rule,
      warning: values.warning_percent_of_stop,
    },
    250,
  );
  const preview = useRulesPreview({
    cpa: debounced.cpa,
    stop_percent_of_rule: debounced.stop,
    warning_percent_of_stop: debounced.warning,
  });

  return (
    <div className="flex flex-col gap-5">
      <Input
        id="offer-cpa"
        type="number"
        step="any"
        min="0"
        label="CPA ставка"
        placeholder="10"
        value={values.cpa}
        onChange={(e) => onChange({ cpa: e.target.value })}
        disabled={disabled}
        leftIcon={<span className="text-[12px] text-bg-9">$</span>}
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
}: {
  loading: boolean;
  data: PreviewData | undefined;
  cpaValid: boolean;
}) {
  if (!cpaValid) {
    return (
      <div className="border border-[var(--hairline)] rounded-[var(--radius-2)] text-[12px] text-bg-9" style={{ padding: "var(--s-4)" }}>
        Укажите CPA — покажу, при какой цене сработают стоп и warning по каждой метрике.
      </div>
    );
  }
  if (loading && !data) {
    return <Skeleton height={160} />;
  }
  if (!data) return null;

  return (
    <div className="border border-[var(--hairline)] rounded-[var(--radius-2)]" style={{ padding: "var(--s-4)" }}>
      <div className="font-display text-[10px] tracking-[0.12em] uppercase text-bg-8 mb-3">
        ПРИ КАКОЙ ЦЕНЕ СРАБОТАЕТ
      </div>

      {/* Денежные правила: CPC / CPL / CPR */}
      <table className="w-full text-[12.5px]">
        <thead>
          <tr className="text-bg-8 font-display text-[10px] tracking-wider uppercase">
            <th className="text-left font-normal pb-1.5">Метрика</th>
            <th className="text-right font-normal pb-1.5">Warning</th>
            <th className="text-right font-normal pb-1.5">Стоп</th>
          </tr>
        </thead>
        <tbody>
          {data.cost_rules.map((r) => (
            <tr key={r.rule} className="border-t border-[var(--hairline)]">
              <td className="py-1.5 text-bg-10">{r.label}</td>
              <td className="py-1.5 text-right font-display tabular-nums text-warning">
                ${r.warning}
              </td>
              <td className="py-1.5 text-right font-display tabular-nums text-danger">${r.stop}</td>
            </tr>
          ))}
        </tbody>
      </table>

      {/* Диапазоны расхода без/с депозитом */}
      {data.spend_ranges.length > 0 && (
        <div className="mt-3 pt-3 border-t border-[var(--hairline)] flex flex-col gap-1.5">
          {data.spend_ranges.map((s) => (
            <div key={s.rule} className="flex items-center justify-between text-[12px]">
              <span className="text-bg-10">{s.label}</span>
              <span className="font-display tabular-nums text-bg-11">
                ${s.stop_from}–${s.stop_to}
              </span>
            </div>
          ))}
        </div>
      )}

      <div className="mt-3 text-[11px] text-bg-8">
        {data.regs_no_dep_stop_count} регистраций без депозитов → стоп. Базовые проценты правил
        (CPC 2% / CPL 10% / CPR 20% от CPA) фиксированы.
      </div>
    </div>
  );
}
