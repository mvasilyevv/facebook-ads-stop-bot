/**
 * Шаг 4 — Структура кампаний.
 *
 * Список кампаний: каждая имеет key, label (необязательно), adset_count.
 * Добавить/удалить кампанию. Итого: сколько adset'ов всего.
 */

import { type FC } from "react";
import { nextCampaignKey, validateCampaignStructure } from "@fb/features/campaigns";
import { Trash2, Plus, Minus, Layers } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import type { CampaignStructure } from "@/lib/api/campaigns";

interface WizardStep4StructureProps {
  campaigns: CampaignStructure[];
  onChange: (campaigns: CampaignStructure[]) => void;
  errors?: string;
}

export const WizardStep4Structure: FC<WizardStep4StructureProps> = ({
  campaigns,
  onChange,
  errors,
}) => {
  const totalAdsets = campaigns.reduce((sum, c) => sum + c.adset_count, 0);

  const addCampaign = () => {
    const key = nextCampaignKey(campaigns);
    onChange([...campaigns, { key, adset_count: 3 }]);
  };

  const removeCampaign = (idx: number) => {
    onChange(campaigns.filter((_, i) => i !== idx));
  };

  const updateCampaign = (idx: number, patch: Partial<CampaignStructure>) => {
    onChange(campaigns.map((c, i) => (i === idx ? { ...c, ...patch } : c)));
  };

  return (
    <div className="space-y-6">
      {/* Заголовок */}
      <div>
        <div className="font-display text-[12px] tracking-[0.14em] uppercase text-bg-8 mb-1">
          ШАГ 4 · СТРУКТУРА
        </div>
        <h2 className="font-display text-[20px] font-medium text-bg-11 leading-tight m-0">
          Кампании и adset'ы
        </h2>
        <p className="text-[13px] text-bg-9 mt-1">
          Добавьте кампании и число adset'ов N. Концепты (фото и видео) привяжете на след. шаге —
          один adset может держать и фото-, и видео-объявления. Метка позволяет различать кампании в
          имени.
        </p>
      </div>

      {/* Список кампаний */}
      {campaigns.length === 0 ? (
        <div className="border border-dashed border-[var(--color-hairline-strong)] rounded-[var(--radius-3)] p-8 text-center">
          <div className="text-bg-8 text-[13px] mb-3">Нет кампаний — добавьте хотя бы одну</div>
          <Button variant="secondary" size="sm" leftIcon={<Plus size={13} />} onClick={addCampaign}>
            Кампания
          </Button>
        </div>
      ) : (
        <div className="space-y-3">
          {campaigns.map((camp, idx) => (
            <CampaignRow
              key={camp.key}
              campaign={camp}
              index={idx}
              onUpdate={(patch) => updateCampaign(idx, patch)}
              onRemove={() => removeCampaign(idx)}
            />
          ))}

          {/* Добавить ещё */}
          <div className="flex items-center gap-2 pt-1">
            <Button
              variant="secondary"
              size="sm"
              leftIcon={<Plus size={13} />}
              onClick={addCampaign}
            >
              Кампания
            </Button>
          </div>
        </div>
      )}

      {/* Итого */}
      {campaigns.length > 0 && (
        <div className="border border-[var(--color-hairline)] rounded-[var(--radius-2)] px-4 py-3 bg-bg-2 flex items-center gap-3">
          <span className="text-[12px] text-bg-8">Итого:</span>
          <span className="font-display text-[13px] text-bg-11">
            <b>{campaigns.length}</b> кампани{campaigns.length === 1 ? "я" : "и"},{" "}
            <b>{totalAdsets}</b> adset'ов
          </span>
        </div>
      )}

      {errors && (
        <span role="alert" className="text-[12px] text-danger font-display">
          {errors}
        </span>
      )}
    </div>
  );
};

// ─── CampaignRow — одна строка кампании ──────────────────────────────────────

interface CampaignRowProps {
  campaign: CampaignStructure;
  index: number;
  onUpdate: (patch: Partial<CampaignStructure>) => void;
  onRemove: () => void;
}

const CampaignRow: FC<CampaignRowProps> = ({ campaign, index, onUpdate, onRemove }) => {
  return (
    <div className="border border-[var(--color-hairline)] rounded-[var(--radius-3)] p-4 bg-bg-1 flex items-start gap-4">
      {/* Нейтральная иконка */}
      <div className="size-9 rounded-[var(--radius-2)] flex items-center justify-center shrink-0 bg-bg-3 text-bg-8 mt-1">
        <Layers size={16} />
      </div>

      {/* Индекс + key */}
      <div className="flex-1 min-w-0 space-y-3">
        <div>
          <span className="font-display text-[12px] text-bg-8">#{index + 1}</span>
          <div
            className="font-display text-[12px] text-bg-9 truncate"
            title={`key: ${campaign.key}`}
          >
            key: <span className="text-bg-11">{campaign.key}</span>
          </div>
        </div>

        {/* Поле метки */}
        <Input
          label="Метка (необязательно)"
          type="text"
          value={campaign.label ?? ""}
          placeholder="напр. CR2 / тест-A"
          onChange={(e) => {
            onUpdate({ label: e.target.value });
          }}
        />
      </div>

      {/* N adset'ов — степпер −/+ (по нативным стрелкам тяжело попасть) */}
      <div className="w-36 shrink-0 mt-1">
        <div className="font-display text-[12px] tracking-wider uppercase text-bg-9 mb-1.5">
          Число adset'ов N
        </div>
        <div className="flex items-center rounded-[var(--radius-2)] border border-[var(--color-hairline-strong)] bg-bg-2 overflow-hidden">
          <button
            type="button"
            aria-label="Уменьшить число adset'ов"
            disabled={campaign.adset_count <= 1}
            onClick={() => onUpdate({ adset_count: Math.max(1, campaign.adset_count - 1) })}
            className="size-11 shrink-0 flex items-center justify-center text-bg-9 hover:bg-bg-3 hover:text-bg-11 disabled:opacity-30 disabled:hover:bg-transparent transition-colors"
          >
            <Minus size={15} />
          </button>
          <input
            type="text"
            inputMode="numeric"
            aria-label="Число adset'ов N"
            value={String(campaign.adset_count)}
            onChange={(e) => {
              const v = parseInt(e.target.value, 10);
              if (!isNaN(v) && v >= 1 && v <= 50) onUpdate({ adset_count: v });
            }}
            className="h-11 min-w-0 flex-1 border-x border-[var(--color-hairline)] bg-transparent text-center text-[14px] tabular-nums text-bg-11 outline-none"
          />
          <button
            type="button"
            aria-label="Увеличить число adset'ов"
            disabled={campaign.adset_count >= 50}
            onClick={() => onUpdate({ adset_count: Math.min(50, campaign.adset_count + 1) })}
            className="size-11 shrink-0 flex items-center justify-center text-bg-9 hover:bg-bg-3 hover:text-bg-11 disabled:opacity-30 disabled:hover:bg-transparent transition-colors"
          >
            <Plus size={15} />
          </button>
        </div>
      </div>

      {/* Удалить */}
      <button
        type="button"
        aria-label="Удалить кампанию"
        onClick={onRemove}
        className="mt-1 flex size-11 shrink-0 items-center justify-center rounded-[var(--radius-2)] text-bg-8 transition-colors hover:bg-danger/10 hover:text-danger"
      >
        <Trash2 size={14} />
      </button>
    </div>
  );
};

// ─── Валидация ────────────────────────────────────────────────────────────────

export function validateStructure(campaigns: CampaignStructure[]): string | null {
  return validateCampaignStructure(campaigns);
}
