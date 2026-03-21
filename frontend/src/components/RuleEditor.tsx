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
  stop_high_cpc: { percentLabel: "Лимит клика", min: 1, max: 20, step: 1 },
  stop_high_cpl: { percentLabel: "Лимит лида", min: 1, max: 40, step: 1 },
  stop_high_cpr: { percentLabel: "Лимит регистрации", min: 5, max: 60, step: 1 },
  stop_spend_window_without_deposit: { percentLabel: "Лимит расхода", min: 10, max: 100, step: 5 },
  stop_spend_after_deposit: { percentLabel: "Лимит расхода после депозита", min: 10, max: 150, step: 5 },
};

function multiplierToPercent(value: RuleItem["cpa_multiplier"]): number {
  if (value == null || value === "") {
    return 0;
  }
  return Math.round(Number(value) * 100);
}

function calculateThresholdUsd(percent: number, cpaUsd: number): number {
  return (cpaUsd * percent) / 100;
}

export function RuleEditor({ rule, offerPreviews, onSave }: RuleEditorProps) {
  const percentPreset = PERCENT_RULE_PRESETS[rule.code];
  const isPercentRule = percentPreset != null;
  const fallbackPercent = percentPreset?.min ?? 0;
  const [isEnabled, setIsEnabled] = useState(rule.is_enabled);
  const [percent, setPercent] = useState(multiplierToPercent(rule.cpa_multiplier) || fallbackPercent);

  useEffect(() => {
    setIsEnabled(rule.is_enabled);
    setPercent(multiplierToPercent(rule.cpa_multiplier) || fallbackPercent);
  }, [rule.is_enabled, rule.cpa_multiplier, fallbackPercent]);
  const percentThresholdText = isPercentRule
    ? `Порог считается автоматически: ${percent}% от CPA каждого найденного оффера`
    : "Фиксированное правило без процента от CPA";
  const hasRealPreviews = isPercentRule && offerPreviews.length > 0;

  return (
    <form
      className="rule-editor"
      onSubmit={async (event) => {
        event.preventDefault();
        await onSave({
          is_enabled: isEnabled,
          cpa_multiplier: isPercentRule ? (percent / 100).toFixed(2) : undefined,
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
        <div className="rule-slider">
          <div className="rule-slider__head">
            <span>{percentPreset.percentLabel}</span>
            <strong>{percent}% от CPA</strong>
          </div>
          <input
            className="rule-slider__input"
            type="range"
            min={percentPreset.min}
            max={percentPreset.max}
            step={percentPreset.step}
            value={percent}
            onChange={(event) => setPercent(Number(event.target.value))}
          />
          <div className="rule-slider__scale">
            <span>{percentPreset.min}%</span>
            <span>{percentPreset.max}%</span>
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
                <span>Код для нейминга: {preview.offerName}</span>
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
