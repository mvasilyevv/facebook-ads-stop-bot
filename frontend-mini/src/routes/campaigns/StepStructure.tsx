/**
 * StepStructure — шаг 4 визарда: структура (кампании image/video + число адсетов).
 * Динамический список кампаний — добавить/удалить/настроить каждую.
 */
import { useState } from "react";
import { PlusCircle, Trash2 } from "lucide-react";
import { Input, Button, Select, Badge } from "@/components/ui";
import { Eyebrow } from "@/components/data";
import { haptic } from "@/lib/tg";
import type { CampaignSpec } from "@/lib/campaignTypes";
import { useWizardStore } from "./-wizardStore";
import { cn } from "@/lib/cn";

const KIND_OPTIONS = [
  { value: "image", label: "Изображения" },
  { value: "video", label: "Видео" },
];

interface CampaignRowProps {
  spec: CampaignSpec;
  index: number;
  onUpdate: (spec: CampaignSpec) => void;
  onRemove: () => void;
}

function CampaignRow({ spec, index, onUpdate, onRemove }: CampaignRowProps) {
  return (
    <div className="border border-[var(--hairline)] bg-bg-1 rounded-[var(--radius-3)] overflow-hidden">
      {/* Заголовок строки */}
      <div className="flex items-center justify-between px-3.5 py-2.5 bg-bg-2 border-b border-[var(--hairline)]">
        <div className="flex items-center gap-2">
          <span className="font-display tabular-nums text-[11px] text-bg-8">#{index + 1}</span>
          <Badge variant={spec.kind === "video" ? "warning" : "done"}>
            {spec.kind}
          </Badge>
        </div>
        <button
          type="button"
          aria-label="Удалить кампанию"
          onClick={() => { haptic.impact("medium"); onRemove(); }}
          className="inline-flex items-center justify-center w-8 h-8 text-[var(--color-danger)] active:opacity-60"
        >
          <Trash2 size={15} strokeWidth={1.8} aria-hidden />
        </button>
      </div>

      {/* Поля */}
      <div className="flex flex-col gap-3 px-3.5 py-3">
        <div className="flex gap-3">
          <div className="flex-1">
            <Select
              label="Тип медиа"
              value={spec.kind}
              options={KIND_OPTIONS}
              onChange={(e) => onUpdate({ ...spec, kind: e.target.value as "image" | "video" })}
            />
          </div>
          <div className="w-[100px]">
            <Input
              label="Адсетов"
              placeholder="3"
              value={String(spec.adset_count)}
              onChange={(e) => {
                const n = parseInt(e.target.value, 10);
                if (!isNaN(n) && n >= 1 && n <= 50) {
                  onUpdate({ ...spec, adset_count: n });
                } else if (e.target.value === "") {
                  onUpdate({ ...spec, adset_count: 1 });
                }
              }}
              inputMode="numeric"
              type="number"
              min={1}
              max={50}
            />
          </div>
        </div>
        <Input
          label="Ключ (авто-нейминг)"
          placeholder={`camp_${index + 1}`}
          value={spec.key}
          onChange={(e) => onUpdate({ ...spec, key: e.target.value })}
          autoCapitalize="none"
        />
      </div>
    </div>
  );
}

export function StepStructure() {
  const { config, updateConfig, nextStep, prevStep } = useWizardStore();
  const [campaigns, setCampaigns] = useState<CampaignSpec[]>(
    config.campaigns && config.campaigns.length > 0
      ? config.campaigns
      : [{ key: "camp_1", kind: "image", adset_count: 3 }],
  );
  const [error, setError] = useState<string | null>(null);

  function addCampaign() {
    haptic.selection();
    setCampaigns((prev) => [
      ...prev,
      { key: `camp_${prev.length + 1}`, kind: "image", adset_count: 3 },
    ]);
  }

  function updateCampaign(idx: number, spec: CampaignSpec) {
    setCampaigns((prev) => prev.map((c, i) => (i === idx ? spec : c)));
  }

  function removeCampaign(idx: number) {
    setCampaigns((prev) => prev.filter((_, i) => i !== idx));
  }

  function handleNext() {
    setError(null);
    if (campaigns.length === 0) {
      setError("Добавьте хотя бы одну кампанию");
      return;
    }
    const keys = campaigns.map((c) => c.key.trim());
    if (keys.some((k) => !k)) {
      setError("Ключи не могут быть пустыми");
      return;
    }
    if (new Set(keys).size !== keys.length) {
      setError("Ключи кампаний должны быть уникальными");
      return;
    }
    haptic.impact("light");
    updateConfig({ campaigns });
    nextStep();
  }

  const totalAdsets = campaigns.reduce((s, c) => s + c.adset_count, 0);

  return (
    <div className="flex flex-col gap-4 p-4 pb-8">
      <div className="flex items-center justify-between">
        <Eyebrow num="04">СТРУКТУРА</Eyebrow>
        <span className={cn(
          "font-display tabular-nums text-[11px] px-2 py-0.5 rounded-full",
          "border border-[var(--hairline)] text-bg-9",
        )}>
          {campaigns.length} камп · {totalAdsets} адс
        </span>
      </div>

      {campaigns.length === 0 && (
        <p className="text-[13px] text-bg-8 text-center py-4">
          Добавьте хотя бы одну кампанию
        </p>
      )}

      <div className="flex flex-col gap-3">
        {campaigns.map((spec, idx) => (
          <CampaignRow
            key={idx}
            spec={spec}
            index={idx}
            onUpdate={(s) => updateCampaign(idx, s)}
            onRemove={() => removeCampaign(idx)}
          />
        ))}
      </div>

      <button
        type="button"
        onClick={addCampaign}
        className={cn(
          "w-full min-h-[48px] flex items-center justify-center gap-2",
          "border border-dashed border-[var(--hairline)] rounded-[var(--radius-3)]",
          "text-[13px] text-bg-9 active:bg-bg-2",
        )}
      >
        <PlusCircle size={16} strokeWidth={1.6} aria-hidden />
        Добавить кампанию
      </button>

      {error !== null && (
        <p className="text-[12px] text-[var(--color-danger)]">{error}</p>
      )}

      <div className="flex flex-col gap-3 mt-2">
        <Button fullWidth onClick={handleNext}>
          Далее
        </Button>
        <Button variant="ghost" fullWidth onClick={() => { haptic.selection(); prevStep(); }}>
          Назад
        </Button>
      </div>
    </div>
  );
}
