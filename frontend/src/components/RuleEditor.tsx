import { useEffect, useState } from "react";
import { Badge } from "./Badge";
import { formatDateTime, formatMoney } from "../lib/format";
import type { RuleItem } from "../types";

type RuleEditorProps = {
  rule: RuleItem;
  offerPreviews: RuleOfferPreview[];
  onSave: (payload: Partial<RuleItem>) => Promise<void>;
};

type PercentRulePreset = {
  percentLabel: string;
  min: number;
  max: number;
  step: number;
};

export type RuleOfferPreview = {
  offerId: string;
  offerName: string;
  offerCode: string;
  cpaUsd: number;
};

const PERCENT_RULE_PRESETS: Record<string, PercentRulePreset> = {
  stop_high_cpc: { percentLabel: "Лимит клика", min: 0.1, max: 4.0, step: 0.01 },
  stop_high_cpl: { percentLabel: "Лимит лида", min: 1.0, max: 20.0, step: 0.05 },
  stop_high_cpr: { percentLabel: "Лимит регистрации", min: 5.0, max: 40.0, step: 0.05 },
  stop_spend_window_without_deposit: { percentLabel: "Лимит расхода", min: 10, max: 100, step: 5 },
  stop_spend_after_deposit: { percentLabel: "Лимит расхода после депозита", min: 10, max: 150, step: 5 },
};

function multiplierToPercent(value: RuleItem["cpa_multiplier"]): number {
  if (value == null || value === "") {
    return 0;
  }
  return Number((Number(value) * 100).toFixed(2));
}

function calculateThresholdUsd(percent: number, cpaUsd: number): number {
  return (cpaUsd * percent) / 100;
}

function formatPercentValue(value: number): string {
  if (!Number.isFinite(value)) {
    return "0";
  }
  return value.toFixed(2).replace(/\.?0+$/, "");
}

function buildQuickPercents(preset: PercentRulePreset): number[] {
  const mid = Number(((preset.min + preset.max) / 2).toFixed(2));
  const values = [preset.min, mid, preset.max];
  return [...new Set(values)].filter((value) => value >= preset.min && value <= preset.max);
}

export function RuleEditor({ rule, offerPreviews, onSave }: RuleEditorProps) {
  const percentPreset = PERCENT_RULE_PRESETS[rule.code];
  const isPercentRule = percentPreset != null;
  const hasRealPreviews = isPercentRule && offerPreviews.length > 0;
  const fallbackPercent = percentPreset?.min ?? 0;
  const [isEnabled, setIsEnabled] = useState(rule.is_enabled);
  const [percent, setPercent] = useState(multiplierToPercent(rule.cpa_multiplier) || fallbackPercent);
  const quickPercents = percentPreset ? buildQuickPercents(percentPreset) : [];
  const thresholdValues = hasRealPreviews
    ? offerPreviews.map((preview) => calculateThresholdUsd(percent, preview.cpaUsd))
    : [];
  const minThresholdUsd = thresholdValues.length > 0 ? Math.min(...thresholdValues) : null;
  const maxThresholdUsd = thresholdValues.length > 0 ? Math.max(...thresholdValues) : null;
  const thresholdSummary =
    minThresholdUsd == null || maxThresholdUsd == null
      ? null
      : Math.abs(maxThresholdUsd - minThresholdUsd) < 0.001
        ? `Сейчас это ${formatMoney(minThresholdUsd)}`
        : `Сейчас это диапазон ${formatMoney(minThresholdUsd)} - ${formatMoney(maxThresholdUsd)}`;

  useEffect(() => {
    setIsEnabled(rule.is_enabled);
    setPercent(multiplierToPercent(rule.cpa_multiplier) || fallbackPercent);
  }, [rule.is_enabled, rule.cpa_multiplier, fallbackPercent]);
  const percentThresholdText = isPercentRule
    ? `Порог считается автоматически: ${formatPercentValue(percent)}% от CPA каждого найденного оффера`
    : "Фиксированное правило без процента от CPA";

  return (
    <form
      className="rule-editor"
      onSubmit={async (event) => {
        event.preventDefault();
        await onSave({
          is_enabled: isEnabled,
          cpa_multiplier: isPercentRule ? (percent / 100).toFixed(4) : undefined,
        });
      }}
    >
      <div className="rule-editor__head">
        <div>
          <strong>{rule.title}</strong>
          <div className="muted">
            {rule.code} · {formatDateTime(rule.updated_at)}
          </div>
          <div className="rule-editor__caption">{percentThresholdText}</div>
          {isPercentRule && !hasRealPreviews ? (
            <div className="rule-editor__hint">
              После создания оффера здесь появятся реальные лимиты в долларах.
            </div>
          ) : null}
        </div>
        <Badge tone={isEnabled ? "good" : "warn"}>{isEnabled ? "включено" : "выключено"}</Badge>
      </div>
      {rule.description ? <div className="rule-editor__description">{rule.description}</div> : null}
      {isPercentRule ? (
        <div className="rule-percent">
          <div className="rule-percent__head">
            <div className="rule-percent__labels">
              <span>{percentPreset.percentLabel}</span>
              {thresholdSummary ? <span className="rule-percent__money">{thresholdSummary}</span> : null}
            </div>
            <strong>{formatPercentValue(percent)}% от CPA</strong>
          </div>
          <div className="rule-percent__input-row">
            <input
              className="rule-percent__slider"
              type="range"
              min={percentPreset.min}
              max={percentPreset.max}
              step={percentPreset.step}
              value={percent}
              onChange={(event) => setPercent(Number(event.target.value))}
              style={{ flex: 1, marginRight: "1rem" }}
            />
            <input
              className="input input--compact rule-percent__input"
              type="number"
              min={percentPreset.min}
              max={percentPreset.max}
              step={percentPreset.step}
              value={percent}
              onChange={(event) => setPercent(Number(event.target.value))}
            />
            <span className="rule-percent__suffix">%</span>
          </div>
          <div className="rule-percent__chips">
            {quickPercents.map((value) => (
              <button
                key={`${rule.id}-${value}`}
                type="button"
                className={`chip${percent === value ? " chip--active" : ""}`}
                onClick={() => setPercent(value)}
              >
                {value}%
              </button>
            ))}
          </div>
        </div>
      ) : null}
      {hasRealPreviews ? (
        <div className="rule-preview-list">
          {offerPreviews.map((preview) => (
            <div key={`${rule.id}-${preview.offerId}-${preview.offerCode}`} className="rule-preview-item">
              <div className="rule-preview-item__head">
                <strong>{preview.offerName}</strong>
                <span className="rule-preview-item__threshold">
                  {formatMoney(calculateThresholdUsd(percent, preview.cpaUsd))}
                </span>
              </div>
              <div className="rule-preview-item__meta">
                <span>CPA: {formatMoney(preview.cpaUsd)}</span>
                <span>Код для нейминга: {preview.offerCode}</span>
              </div>
            </div>
          ))}
        </div>
      ) : null}
      <label className="checkbox">
        <input type="checkbox" checked={isEnabled} onChange={(e) => setIsEnabled(e.target.checked)} />
        <span>Правило активно</span>
      </label>
      <div className="row-actions">
        <button type="submit" className="button button--small button--primary">Сохранить правило</button>
      </div>
    </form>
  );
}
