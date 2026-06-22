/**
 * Шаг 4 — Структура кампаний.
 *
 * Список кампаний: каждая имеет key, kind (image/video), adset_count.
 * Добавить/удалить кампанию. Итого: сколько adset'ов всего.
 */

import { type FC } from "react";
import { Trash2, Image, Video } from "lucide-react";
import { cn } from "@/lib/utils/cn";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import type { CampaignStructure } from "@/lib/api/campaigns";

interface WizardStep4StructureProps {
  campaigns: CampaignStructure[];
  onChange: (campaigns: CampaignStructure[]) => void;
  errors?: string;
}

/** Генерирует уникальный key для новой кампании. */
function genKey(campaigns: CampaignStructure[], kind: "image" | "video"): string {
  const existing = campaigns.filter((c) => c.kind === kind).length;
  return `${kind}${existing + 1}`;
}

export const WizardStep4Structure: FC<WizardStep4StructureProps> = ({
  campaigns,
  onChange,
  errors,
}) => {
  const totalAdsets = campaigns.reduce((sum, c) => sum + c.adset_count, 0);

  const addCampaign = (kind: "image" | "video") => {
    const key = genKey(campaigns, kind);
    onChange([...campaigns, { key, kind, adset_count: 3, concept_refs: [] }]);
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
        <div className="font-display text-[10px] tracking-[0.14em] uppercase text-bg-7 mb-1">
          ШАГ 4 · СТРУКТУРА
        </div>
        <h2 className="font-display text-[20px] font-medium text-bg-11 leading-tight m-0">
          Кампании и adset'ы
        </h2>
        <p className="text-[13px] text-bg-9 mt-1">
          Добавьте кампании — static (фото) и/или video. Укажите число adset'ов N в каждой.
          Бот создаст K×N ads (K концептов × N уникализаций).
        </p>
      </div>

      {/* Список кампаний */}
      {campaigns.length === 0 ? (
        <div className="border border-dashed border-[var(--hairline-strong)] rounded-[var(--radius-3)] p-8 text-center">
          <div className="text-bg-8 text-[13px] mb-3">Нет кампаний — добавьте хотя бы одну</div>
          <div className="flex items-center justify-center gap-2">
            <Button
              variant="secondary"
              size="sm"
              leftIcon={<Image size={13} />}
              onClick={() => addCampaign("image")}
            >
              + Фото-кампания
            </Button>
            <Button
              variant="secondary"
              size="sm"
              leftIcon={<Video size={13} />}
              onClick={() => addCampaign("video")}
            >
              + Видео-кампания
            </Button>
          </div>
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
              leftIcon={<Image size={13} />}
              onClick={() => addCampaign("image")}
            >
              + Фото
            </Button>
            <Button
              variant="secondary"
              size="sm"
              leftIcon={<Video size={13} />}
              onClick={() => addCampaign("video")}
            >
              + Видео
            </Button>
          </div>
        </div>
      )}

      {/* Итого */}
      {campaigns.length > 0 && (
        <div className="border border-[var(--hairline)] rounded-[var(--radius-2)] px-4 py-3 bg-bg-2 flex items-center gap-3">
          <span className="text-[12px] text-bg-8">Итого:</span>
          <span className="font-display text-[13px] text-bg-11">
            <b>{campaigns.length}</b> кампани{campaigns.length === 1 ? "я" : "и"},{" "}
            <b>{totalAdsets}</b> adset'ов
          </span>
        </div>
      )}

      {errors && (
        <span role="alert" className="text-[11px] text-danger font-display">
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
  const isVideo = campaign.kind === "video";

  return (
    <div className="border border-[var(--hairline)] rounded-[var(--radius-3)] p-4 bg-bg-1 flex items-center gap-4">
      {/* Иконка типа */}
      <div
        className={cn(
          "size-9 rounded-[var(--radius-2)] flex items-center justify-center shrink-0",
          isVideo ? "bg-purple-500/10 text-purple-400" : "bg-blue-500/10 text-blue-400",
        )}
      >
        {isVideo ? <Video size={16} /> : <Image size={16} />}
      </div>

      {/* Тип и key */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 mb-1">
          <span
            className={cn(
              "font-display text-[10px] tracking-[0.12em] uppercase px-1.5 py-0.5 rounded",
              isVideo ? "bg-purple-500/10 text-purple-400" : "bg-blue-500/10 text-blue-400",
            )}
          >
            {isVideo ? "VIDEO" : "STATIC"}
          </span>
          <span className="font-display text-[11px] text-bg-8">#{index + 1}</span>
        </div>
        <div
          className="font-display text-[12px] text-bg-9 truncate"
          title={`key: ${campaign.key}`}
        >
          key: <span className="text-bg-11">{campaign.key}</span>
        </div>
      </div>

      {/* N adset'ов */}
      <div className="w-36 shrink-0">
        <Input
          label="Число adset'ов N"
          type="number"
          min={1}
          max={50}
          value={String(campaign.adset_count)}
          onChange={(e) => {
            const v = parseInt(e.target.value, 10);
            if (!isNaN(v) && v >= 1 && v <= 50) {
              onUpdate({ adset_count: v });
            }
          }}
        />
      </div>

      {/* Удалить */}
      <button
        type="button"
        aria-label="Удалить кампанию"
        onClick={onRemove}
        className="shrink-0 size-8 flex items-center justify-center rounded-[var(--radius-2)] text-bg-7 hover:text-danger hover:bg-danger/10 transition-colors"
      >
        <Trash2 size={14} />
      </button>
    </div>
  );
};

// ─── Валидация ────────────────────────────────────────────────────────────────

export function validateStructure(campaigns: CampaignStructure[]): string | null {
  if (campaigns.length === 0) return "Добавьте хотя бы одну кампанию";
  if (campaigns.some((c) => c.adset_count < 1)) return "Число adset'ов должно быть ≥ 1";
  return null;
}
