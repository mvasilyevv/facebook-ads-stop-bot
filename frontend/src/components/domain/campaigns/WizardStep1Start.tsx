/**
 * Шаг 1 — старт визарда: новый залив / из пресета / клон.
 *
 * - Три карточки-опции (radio-like)
 * - При выборе "из пресета" — выпадающий список пресетов
 * - При выборе "клон" — поле run_id (заполняется из истории)
 */

import { type FC } from "react";
import { Plus, Layers, Copy } from "lucide-react";
import { cn } from "@/lib/utils/cn";
import { usePresets } from "@/lib/api/campaigns";
import type { StartMode } from "@/stores/campaignWizard";
import { Select } from "@/components/ui/Select";
import { Input } from "@/components/ui/Input";
import { Skeleton } from "@/components/ui/Skeleton";

interface WizardStep1StartProps {
  mode: StartMode;
  presetId?: string | null;
  cloneRunId?: string | null;
  onChange: (v: { mode: StartMode; preset_id?: string | null; clone_run_id?: string | null }) => void;
}

const OPTIONS: { mode: StartMode; icon: FC<{ size: number }>; label: string; desc: string }[] = [
  {
    mode: "new",
    icon: Plus,
    label: "Новый залив",
    desc: "Начать с чистого листа — задать все параметры вручную.",
  },
  {
    mode: "preset",
    icon: Layers,
    label: "Из пресета",
    desc: "Загрузить сохранённый пресет (кабинет, пиксель, оффер, атрибуция).",
  },
  {
    mode: "clone",
    icon: Copy,
    label: "Клон запуска",
    desc: "Скопировать конфиг предыдущего залива из истории и изменить нужное.",
  },
];

export const WizardStep1Start: FC<WizardStep1StartProps> = ({
  mode,
  presetId,
  cloneRunId,
  onChange,
}) => {
  const { data: presets, isLoading: presetsLoading } = usePresets();

  const presetOptions =
    presets?.map((p) => ({ value: p.id, label: `${p.name} (${p.offer_code ?? "—"})` })) ?? [];

  return (
    <div className="space-y-6">
      {/* Заголовок шага */}
      <div>
        <div className="font-display text-[10px] tracking-[0.14em] uppercase text-bg-7 mb-1">
          ШАГ 1 · СТАРТ
        </div>
        <h2 className="font-display text-[20px] font-medium text-bg-11 leading-tight m-0">
          Как начать?
        </h2>
        <p className="text-[13px] text-bg-9 mt-1">
          Выберите режим — новый залив, загрузка пресета или клон предыдущего.
        </p>
      </div>

      {/* Три карточки-опции */}
      <div className="grid grid-cols-3 gap-3">
        {OPTIONS.map(({ mode: m, icon: Icon, label, desc }) => {
          const isActive = mode === m;
          return (
            <button
              key={m}
              type="button"
              onClick={() => onChange({ mode: m, preset_id: null, clone_run_id: null })}
              className={cn(
                "text-left p-4 rounded-[var(--radius-3)] border transition-all duration-[120ms]",
                "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent",
                isActive
                  ? "bg-accent-bg border-accent"
                  : "bg-bg-2 border-[var(--hairline)] hover:border-[var(--hairline-strong)] hover:bg-bg-3",
              )}
              aria-pressed={isActive}
            >
              <div
                className={cn(
                  "size-8 rounded-[var(--radius-2)] flex items-center justify-center mb-3",
                  isActive ? "bg-accent text-bg-0" : "bg-bg-3 text-bg-9",
                )}
              >
                <Icon size={16} />
              </div>
              <div
                className={cn(
                  "font-display text-[13px] font-medium mb-1",
                  isActive ? "text-accent" : "text-bg-11",
                )}
              >
                {label}
              </div>
              <div className="text-[12px] text-bg-8 leading-snug">{desc}</div>
            </button>
          );
        })}
      </div>

      {/* Дополнительный выбор при preset */}
      {mode === "preset" && (
        <div className="space-y-2">
          <div className="text-[11px] font-display tracking-wider uppercase text-bg-8">
            Выберите пресет
          </div>
          {presetsLoading ? (
            <Skeleton className="h-8 w-full" />
          ) : presetOptions.length === 0 ? (
            <p className="text-[13px] text-bg-8">
              Пресетов нет. Создайте первый пресет на шаге 2 после завершения залива.
            </p>
          ) : (
            <Select
              options={presetOptions}
              placeholder="— Выберите пресет —"
              value={presetId ?? ""}
              onChange={(e) =>
                onChange({ mode: "preset", preset_id: e.target.value || null })
              }
            />
          )}
        </div>
      )}

      {/* Дополнительный ввод при clone */}
      {mode === "clone" && (
        <div className="space-y-2">
          <Input
            label="Run ID для клонирования"
            placeholder="UUID запуска из истории"
            value={cloneRunId ?? ""}
            onChange={(e) =>
              onChange({ mode: "clone", clone_run_id: e.target.value || null })
            }
            helpText="Скопируйте из раздела «История запусков» → Run ID"
          />
        </div>
      )}
    </div>
  );
};
