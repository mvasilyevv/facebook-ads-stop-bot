/**
 * AdCreativeSection — секция «КРЕАТИВ» в AdDrawer: превью + бюджет/пиксель/фаза обучения.
 *
 * Выделено из AdDrawer.tsx (было >600 строк в одном файле — god-component).
 * Поля creative_ и adset_ — расширение AdSnapshot, доступны после добавления бэком
 * (мягкий каст, как adAccountId в adHelpers.ts).
 */
import type { AdSnapshot } from "@fb/shared";
import { Eyebrow } from "@/components/data/Eyebrow";
import { cn } from "@/lib/utils/cn";

type AdSnapshotExt = AdSnapshot & {
  creative_thumb_url?: string | null;
  creative_image_url?: string | null;
  adset_pixel_id?: string | null;
  adset_daily_budget?: string | null;
  adset_lifetime_budget?: string | null;
  adset_budget_remaining?: string | null;
  learning_stage?: string | null;
};

interface AdCreativeSectionProps {
  ad: AdSnapshot;
}

export function AdCreativeSection({ ad }: AdCreativeSectionProps) {
  const adExt = ad as AdSnapshotExt;
  const creativeThumb = adExt.creative_thumb_url ?? null;
  const creativeImage = adExt.creative_image_url ?? null;
  const creativeSrc = creativeImage || creativeThumb;
  const pixelId = adExt.adset_pixel_id ?? null;
  const dailyBudget = adExt.adset_daily_budget ?? null;
  const lifetimeBudget = adExt.adset_lifetime_budget ?? null;
  const budgetRemaining = adExt.adset_budget_remaining ?? null;
  const learningStage = adExt.learning_stage ?? null;

  if (!creativeSrc) return null;

  const hasMetaBlock = Boolean(
    dailyBudget || lifetimeBudget || budgetRemaining || pixelId || learningStage,
  );

  return (
    <section>
      <Eyebrow className="mb-3">КРЕАТИВ</Eyebrow>
      <div className="flex flex-col gap-3">
        {/* Крупное превью — кликабельно: открывает оригинал в новой вкладке */}
        <a
          href={creativeImage || creativeThumb || "#"}
          target="_blank"
          rel="noopener noreferrer"
          className="block rounded-[var(--radius-2)] overflow-hidden border border-[var(--hairline)] bg-bg-1"
          aria-label="Открыть креатив в полном размере"
          title="Открыть в полном размере"
        >
          <img
            src={creativeSrc}
            alt="Превью крео"
            className="w-full max-h-[280px] object-contain"
          />
        </a>

        {/* Компактный блок: бюджет / пиксель / фаза обучения */}
        {hasMetaBlock && (
          <div className="border border-[var(--hairline)] rounded-[var(--radius-2)] overflow-hidden">
            {dailyBudget && (
              <CreativeMetaRow label="Бюджет (день)" value={formatBudgetMinorUnits(dailyBudget)} />
            )}
            {lifetimeBudget && (
              <CreativeMetaRow
                label="Бюджет (total)"
                value={formatBudgetMinorUnits(lifetimeBudget)}
                border={Boolean(dailyBudget)}
              />
            )}
            {budgetRemaining && (
              <CreativeMetaRow
                label="Остаток"
                value={formatBudgetMinorUnits(budgetRemaining)}
                border={Boolean(dailyBudget || lifetimeBudget)}
              />
            )}
            {pixelId && (
              <CreativeMetaRow
                label="Пиксель"
                value={pixelId}
                mono
                border={Boolean(dailyBudget || lifetimeBudget || budgetRemaining)}
              />
            )}
            {learningStage && (
              <div
                className={cn(
                  "flex items-center justify-between gap-3 px-3 py-2",
                  (dailyBudget || lifetimeBudget || budgetRemaining || pixelId) &&
                    "border-t border-[var(--hairline)]",
                )}
              >
                <span className="font-display text-[10px] uppercase tracking-[0.1em] text-bg-9 shrink-0">
                  Обучение
                </span>
                <LearningBadge stage={learningStage} />
              </div>
            )}
          </div>
        )}
      </div>
    </section>
  );
}

/** Строка в блоке «бюджет/пиксель» внутри секции КРЕАТИВ. */
function CreativeMetaRow({
  label,
  value,
  border,
  mono,
}: {
  label: string;
  value: string;
  border?: boolean;
  mono?: boolean;
}) {
  return (
    <div
      className={cn(
        "flex items-center justify-between gap-3 px-3 py-2",
        border && "border-t border-[var(--hairline)]",
      )}
    >
      <span className="font-display text-[10px] uppercase tracking-[0.1em] text-bg-9 shrink-0">
        {label}
      </span>
      <span
        className={cn("text-[13px] text-bg-11 truncate text-right", mono && "font-mono text-[12px]")}
      >
        {value}
      </span>
    </div>
  );
}

/**
 * Бейдж фазы обучения.
 * LEARNING → «Обучение», LEARNING_LIMITED → «Обучение ограничено».
 */
function LearningBadge({ stage }: { stage: string }) {
  if (stage === "LEARNING_LIMITED") {
    return (
      <span className="inline-block px-2 py-0.5 rounded-[var(--radius-1)] text-[11px] font-display bg-bg-3 text-danger border border-[rgba(199,98,92,0.3)]">
        Обучение ограничено
      </span>
    );
  }
  if (stage === "LEARNING") {
    return (
      <span className="inline-block px-2 py-0.5 rounded-[var(--radius-1)] text-[11px] font-display bg-bg-3 text-bg-10 border border-[var(--hairline)]">
        Обучение
      </span>
    );
  }
  // Неизвестный stage — показываем as-is моноширинно.
  return <span className="font-mono text-[12px] text-bg-9">{stage}</span>;
}

/**
 * Форматирует бюджет из minor units (центы) в читаемый вид.
 * Валюта неизвестна (Meta передаёт без символа) — используем нейтральный формат.
 * Пример: "150000" → "1 500.00"
 */
function formatBudgetMinorUnits(raw: string | null | undefined): string {
  if (!raw) return "—";
  const n = Number.parseFloat(raw);
  if (Number.isNaN(n)) return raw ?? "—";
  return (n / 100).toLocaleString("ru-RU", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}
