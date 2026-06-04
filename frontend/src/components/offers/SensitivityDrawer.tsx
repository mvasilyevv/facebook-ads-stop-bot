/**
 * SensitivityDrawer — Drawer для настройки чувствительности правил оффера.
 * Два слайдера: stop_percent_of_rule + warning_percent_of_stop.
 * Live-превью таблицы порогов через GET /offers/rules/preview (debounce 300ms).
 */

import { useState, useEffect, useRef } from "react";
import { Drawer } from "@/components/ui/Drawer";
import { Button } from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/Skeleton";
import { toast } from "@/components/ui/Toast";
import { useOfferRules, useUpsertOfferRules, useRulePreview } from "@/lib/api/offers";
import type { Offer } from "@/lib/types/api";

interface SensitivityDrawerProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  offer: Offer | null;
}

export function SensitivityDrawer({ open, onOpenChange, offer }: SensitivityDrawerProps) {
  const rulesQuery = useOfferRules(open ? (offer?.id ?? null) : null);

  return (
    <Drawer
      open={open}
      onOpenChange={onOpenChange}
      title={offer ? `Чувствительность — ${offer.code}` : "Чувствительность правил"}
      description={undefined}
      width={480}
    >
      {rulesQuery.isLoading ? (
        <div className="flex flex-col gap-5">
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="flex flex-col gap-1.5">
              <Skeleton height={11} width={180} />
              <Skeleton height={24} />
            </div>
          ))}
        </div>
      ) : (
        <SensitivityForm
          key={offer?.id ?? "no-offer"}
          offerId={offer?.id ?? ""}
          initialStop={Number(rulesQuery.data?.stop_percent_of_rule ?? 80)}
          initialWarning={Number(rulesQuery.data?.warning_percent_of_stop ?? 80)}
          cpaTreshold={rulesQuery.data?.cpa_threshold ?? null}
          onClose={() => onOpenChange(false)}
        />
      )}
    </Drawer>
  );
}

// ─── Форма слайдеров ──────────────────────────────────────────────────────────

interface SensitivityFormProps {
  offerId: string;
  initialStop: number;
  initialWarning: number;
  cpaTreshold: string | null;
  onClose: () => void;
}

function SensitivityForm({
  offerId,
  initialStop,
  initialWarning,
  cpaTreshold,
  onClose,
}: SensitivityFormProps) {
  const [stop, setStop] = useState(initialStop);
  const [warning, setWarning] = useState(initialWarning);

  // Debounced значения для preview-запроса.
  const [debouncedStop, setDebouncedStop] = useState(stop);
  const [debouncedWarning, setDebouncedWarning] = useState(warning);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      setDebouncedStop(stop);
      setDebouncedWarning(warning);
    }, 300);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [stop, warning]);

  const cpa = cpaTreshold ? Number(cpaTreshold) : null;

  const preview = useRulePreview(cpa, debouncedStop, debouncedWarning);

  const upsert = useUpsertOfferRules();

  function handleSave() {
    // Partial: шлём ТОЛЬКО чувствительность — CPA и частота не трогаем (backend partial-upsert).
    const rulesData = {
      stop_percent_of_rule: String(stop),
      warning_percent_of_stop: String(warning),
    };
    upsert.mutate(
      { id: offerId, data: rulesData },
      {
        onSuccess: () => {
          toast.success("Чувствительность сохранена", "Пороги правил обновлены.");
          onClose();
        },
        onError: (err) =>
          toast.error("Ошибка сохранения", err instanceof Error ? err.message : String(err)),
      },
    );
  }

  return (
    <div className="flex flex-col gap-6 h-full">
      <p className="text-[12px] text-bg-9 leading-relaxed">
        Регулирует при каком % от базового правила срабатывают стоп и ворнинг.
        Пример: CPA $50 → стоп = $40 (80%) → ворнинг = $32 (80% от стопа).
      </p>

      {/* Слайдер стоп */}
      <SliderField
        label="Стоп — % от правила"
        value={stop}
        onChange={setStop}
        help="При каком % от базового правила срабатывает стоп. По умолчанию 80."
      />

      {/* Слайдер ворнинг */}
      <SliderField
        label="Ворнинг — % от стопа"
        value={warning}
        onChange={setWarning}
        help="При каком % от стоп-порога срабатывает предупреждение. По умолчанию 80."
      />

      {/* Live-превью */}
      <div className="flex-1">
        <p className="text-[11px] font-medium uppercase tracking-wide text-bg-8 mb-3">
          Предварительный просмотр
        </p>
        {cpa === null ? (
          <p className="text-[12px] text-bg-7 italic">
            Задайте CPA в настройках оффера, чтобы увидеть стоимости.
          </p>
        ) : preview.isLoading ? (
          <div className="flex flex-col gap-2">
            <Skeleton height={14} />
            <Skeleton height={14} />
            <Skeleton height={14} />
          </div>
        ) : preview.isError || !preview.data ? (
          <p className="text-[12px] text-bg-7">Не удалось загрузить превью.</p>
        ) : (
          <PreviewTable data={preview.data} />
        )}
      </div>

      {/* Кнопки */}
      <div className="flex justify-end gap-2 pt-4 border-t border-bg-5">
        <Button variant="ghost" onClick={onClose}>
          Отмена
        </Button>
        <Button variant="primary" loading={upsert.isPending} onClick={handleSave}>
          Сохранить
        </Button>
      </div>
    </div>
  );
}

// ─── Кастомный слайдер ────────────────────────────────────────────────────────

interface SliderFieldProps {
  label: string;
  value: number;
  onChange: (v: number) => void;
  help: string;
}

function SliderField({ label, value, onChange, help }: SliderFieldProps) {
  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center justify-between">
        <label className="text-[13px] font-medium text-bg-11">{label}</label>
        <span className="font-numeric text-[14px] tabular-nums text-accent font-semibold min-w-[3ch] text-right">
          {value}%
        </span>
      </div>
      <div className="relative flex items-center h-6">
        <input
          type="range"
          min={1}
          max={100}
          step={1}
          value={value}
          onChange={(e) => onChange(Number(e.target.value))}
          className="sensitivity-slider w-full"
          aria-label={label}
          aria-valuenow={value}
          aria-valuemin={1}
          aria-valuemax={100}
        />
      </div>
      <div className="flex justify-between text-[10px] text-bg-7 font-numeric">
        <span>1%</span>
        <span>50%</span>
        <span>100%</span>
      </div>
      <p className="text-[11px] text-bg-7 leading-relaxed">{help}</p>
    </div>
  );
}

// ─── Таблица превью ───────────────────────────────────────────────────────────

import type { RulePreviewOut } from "@/lib/types/api";

interface PreviewTableProps {
  data: RulePreviewOut;
}

export function fmt(v: string | number): string {
  // preview-API отдаёт Decimal как строку ("0.06") — коэрсим в число перед toFixed.
  const n = Number(v);
  return Number.isFinite(n) ? `$${n.toFixed(2)}` : "—";
}

function PreviewTable({ data }: PreviewTableProps) {
  return (
    <div className="flex flex-col gap-1 rounded-md border border-bg-5 overflow-hidden text-[12px]">
      {/* Шапка */}
      <div className="grid grid-cols-4 gap-2 px-3 py-2 bg-bg-3 text-[10px] uppercase tracking-wide text-bg-8 font-display">
        <span className="col-span-2">Правило</span>
        <span className="text-right">Стоп</span>
        <span className="text-right">Ворнинг</span>
      </div>

      {/* Строки cost_rules */}
      {data.cost_rules.map((r) => (
        <div
          key={r.rule}
          className="grid grid-cols-4 gap-2 px-3 py-2 border-t border-bg-4 hover:bg-bg-2 transition-colors"
        >
          <div className="col-span-2 text-bg-10">
            {r.label}
            <span className="ml-1 text-bg-7">(база {fmt(r.base)})</span>
          </div>
          <div className="text-right font-numeric tabular-nums text-danger">{fmt(r.stop)}</div>
          <div className="text-right font-numeric tabular-nums text-warning">{fmt(r.warning)}</div>
        </div>
      ))}

      {/* Строки spend_ranges */}
      {data.spend_ranges.map((r) => (
        <div
          key={r.rule}
          className="grid grid-cols-4 gap-2 px-3 py-2 border-t border-bg-4 hover:bg-bg-2 transition-colors"
        >
          <div className="col-span-2 text-bg-10">{r.label}</div>
          <div className="text-right font-numeric tabular-nums text-danger text-[11px]">
            {fmt(r.stop_from)}–{fmt(r.stop_to)}
          </div>
          <div className="text-right font-numeric tabular-nums text-warning text-[11px]">
            {fmt(r.warning_from)}+
          </div>
        </div>
      ))}

      {/* regs_no_dep */}
      <div className="px-3 py-2 border-t border-bg-4 text-bg-10 col-span-4">
        Рег без депозитов:{" "}
        <span className="font-numeric tabular-nums text-danger">
          {data.regs_no_dep_stop_count}
        </span>{" "}
        → стоп
      </div>
    </div>
  );
}
