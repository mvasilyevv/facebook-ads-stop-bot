/**
 * AdDrawer — drawer деталей объявления (канон ads-web.jsx AdDrawer).
 *
 * 560px, slide-in справа. Орекстрирует секции, вынесенные в под-компоненты
 * (god-component >600 строк разнесён на части):
 *   - AdCreativeSection — превью крео + бюджет/пиксель/learning
 *   - AdMetricsPanel    — metrics-grid + CPL sparkline
 *   - AdTaskHistory     — алерты + задачи DESC
 *   - AdDisableButton   — MONEY: footer disable-кнопка с confirm-with-typing
 *
 * Заголовок строится мгновенно из переданного AdSnapshot (строка таблицы),
 * детали (timeline) догружаются. Esc / scrim / крест закрывают (через Drawer).
 *
 * Поддерживает loading-стейт: при isLoading (или ad=null) рендерит skeleton
 * внутри Drawer (для холодного deep-link /ads/$fbAdId без кэша).
 */

import {
  alertStateToBadgeVariant,
  displayAdState,
  type AdSnapshot,
  type StatsToday,
} from "@fb/shared";
import type { components } from "@fb/shared/api/generated";

import { Drawer } from "@/components/ui/Drawer";
import { Badge } from "@/components/ui/Badge";
import { Skeleton } from "@/components/ui/Skeleton";
import { Eyebrow } from "@/components/data/Eyebrow";
import { cn } from "@/lib/utils/cn";

import { useAdTimeline } from "@/lib/api/ads";
import { adAccountId, readAdMetrics, deriveGeo } from "./adHelpers";
import { RulePills } from "./RulePill";
import { AdCreativeSection } from "./AdCreativeSection";
import { AdMetricsPanel } from "./AdMetricsPanel";
import { AdTaskHistory } from "./AdTaskHistory";
import { AdDisableButton } from "./AdDisableButton";
import { AdsetDuplicateAction } from "./AdsetDuplicateAction";

type AlertRow = components["schemas"]["AlertRow"];
type TaskRow = components["schemas"]["TaskRow"];
type MetricRow = components["schemas"]["MetricRow"];

interface AdDrawerProps {
  /** Snapshot объявления. null — данные ещё грузятся (deep-link). */
  ad: AdSnapshot | null;
  onClose: () => void;
  /** Внешний флаг загрузки (когда ad ещё не готов). */
  isLoading?: boolean;
  /** fb_ad_id, известный до прихода snapshot (для header/timeline при ad=null). */
  fbAdId?: string;
  /**
   * true — переданный `ad` собран синтетически (холодный deep-link без snapshot
   * в кэше, только timeline), реального alert_state с бэка нет. НЕ подставляем
   * ложную «Норму» — показываем нейтральный статус «Статус неизвестен».
   */
  stateUnknown?: boolean;
  /** Кабинетный live-срез AdSet.pro из GET /stats/today. */
  trackerData?: StatsToday["tracker"] | null;
  trackerDataLoading?: boolean;
}

export function AdDrawer({
  ad,
  onClose,
  isLoading = false,
  fbAdId,
  stateUnknown = false,
  trackerData,
  trackerDataLoading = false,
}: AdDrawerProps) {
  // id резолвится из snapshot или из явного пропа (для холодного deep-link).
  const resolvedId = ad?.fb_ad_id ?? fbAdId ?? "";

  // Timeline (метрики/алерты/задачи) — хук всегда в топе.
  const { data: timeline, isLoading: timelineLoading } = useAdTimeline(resolvedId, {
    include_metrics: true,
    include_alerts: true,
    include_tasks: true,
  });

  const loading = isLoading || (!ad && timelineLoading);

  // ── Loading-стейт (skeleton внутри Drawer) ─────────────────────────────────
  if (loading || !ad) {
    return (
      <Drawer
        open
        onOpenChange={(o) => {
          if (!o) onClose();
        }}
        width={560}
        eyebrow={<DrawerEyebrow geo="—" />}
        title={<span className="truncate">{resolvedId || "Объявление"}</span>}
      >
        <div className="flex flex-col gap-4" role="status" aria-label="Загрузка данных объявления">
          <Skeleton height={72} className="w-full" />
          <Skeleton height={120} className="w-full" />
          <div className="flex flex-col gap-2">
            {Array.from({ length: 3 }).map((_, i) => (
              <Skeleton key={i} height={36} className="w-full" />
            ))}
          </div>
        </div>
      </Drawer>
    );
  }

  // ── Полные данные ──────────────────────────────────────────────────────────
  // STATE учитывает И FSM, И доставку в FB (см. displayAdState): выключенное объявление
  // с alert_state=normal показывается как «Выключено», а не ложная «Норма».
  // stateUnknown (холодный deep-link без snapshot) — реальный FSM неизвестен,
  // displayAdState() не вызываем вообще (иначе синтетический "normal" покажет
  // ложную «Норму» вместо честного «нет данных»).
  const display = stateUnknown
    ? { label: "Статус неизвестен", state: "normal" as const }
    : displayAdState(ad.alert_state, ad.delivery_status);
  const geo = deriveGeo(ad);
  const m = readAdMetrics(ad);
  const rules = [...(ad.stop_rule_codes ?? []), ...(ad.warning_rule_codes ?? [])];
  const age = relativeAge(ad.last_seen_at);

  return (
    <Drawer
      open
      onOpenChange={(o) => {
        if (!o) onClose();
      }}
      width={560}
      eyebrow={<DrawerEyebrow geo={geo} />}
      title={<span className="truncate">{ad.ad_name}</span>}
      description={
        <span className="flex items-center gap-2 flex-wrap">
          <Badge
            variant={stateUnknown ? "neutral" : alertStateToBadgeVariant(display.state)}
            size="sm"
          >
            {display.label}
          </Badge>
          {ad.offer_code ? (
            <span className="inline-block px-1.5 py-px bg-bg-3 border border-[var(--hairline)] rounded-[var(--radius-1)] text-bg-10 font-display text-[10px] tracking-[0.04em] uppercase">
              {ad.offer_code}
            </span>
          ) : null}
          {adAccountId(ad) ? (
            // Мульти-кабинет: кабинет объявления (просто лейбл, без ссылки).
            <span
              className="inline-block px-1.5 py-px bg-bg-2 border border-[var(--hairline)] rounded-[var(--radius-1)] text-bg-9 font-display text-[10px] tabular-nums tracking-[0.04em]"
              title={`Кабинет ${adAccountId(ad)}`}
            >
              act {adAccountId(ad)}
            </span>
          ) : null}
          <span className="font-display text-[11px] text-bg-8">{resolvedId}</span>
        </span>
      }
      footer={
        <div className="flex w-full flex-col items-stretch gap-2 sm:flex-row sm:items-center">
          <AdsetDuplicateAction ad={ad} />
          <div className="min-w-0 flex-1">
            <AdDisableButton
              fbAdId={resolvedId}
              // При неизвестном статусе не утверждаем «уже отключено» — кнопка
              // Disable остаётся доступной (сама по себе безопасна: создаёт задачу).
              alreadyDisabled={!stateUnknown && display.state === "disabled"}
              onDisabled={onClose}
            />
          </div>
        </div>
      }
    >
      <div className="flex flex-col gap-6">
        {/* Triggered-rule banner */}
        {rules.length > 0 && (
          <div
            className="flex items-center gap-2 flex-wrap p-4 bg-danger-bg border border-[rgba(199,98,92,0.3)] rounded-[var(--radius-2)]"
            style={{ borderLeft: "2px solid var(--danger)" }}
          >
            <span className="text-[12px] text-bg-10">сработали:</span>
            <RulePills codes={rules} />
          </div>
        )}

        <AdCreativeSection ad={ad} />

        {/* Иерархия: кампания / адсет («отец») — различает дубли по адсету */}
        {(ad.campaign_name || ad.adset_name) && (
          <section>
            <Eyebrow className="mb-3">ИЕРАРХИЯ</Eyebrow>
            <div className="border border-[var(--hairline)] rounded-[var(--radius-2)] overflow-hidden">
              <HierRow label="Кампания" value={ad.campaign_name} />
              <HierRow label="Адсет" value={ad.adset_name} border />
            </div>
          </section>
        )}

        <AdMetricsPanel
          metrics={m}
          age={age}
          metricsRows={(timeline?.metrics ?? []) as MetricRow[]}
          metricsRowsLoading={timelineLoading}
          trackerData={trackerData}
          trackerDataLoading={trackerDataLoading}
        />

        <AdTaskHistory
          alerts={(timeline?.alerts ?? []) as AlertRow[]}
          tasks={(timeline?.tasks ?? []) as TaskRow[]}
          isLoading={timelineLoading}
        />
      </div>
    </Drawer>
  );
}

// ─── Sub-компоненты заголовка (мелкие, остаются здесь) ────────────────────────

/** Строка иерархии (Кампания / Адсет) в drawer. */
function HierRow({
  label,
  value,
  border,
}: {
  label: string;
  value?: string | null;
  border?: boolean;
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
      <span className="text-[13px] text-bg-11 truncate text-right" title={value ?? undefined}>
        {value || "—"}
      </span>
    </div>
  );
}

function DrawerEyebrow({ geo }: { geo: string }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className="text-accent-muted">{geo}</span>
      <span className="text-bg-8">/</span>
      {/* Отдельный span чтобы getByText("ОБЪЯВЛЕНИЕ") работал в тестах */}
      <span>ОБЪЯВЛЕНИЕ</span>
    </span>
  );
}

/** Возраст (с last_seen) в кратком виде или «—». */
function relativeAge(iso: string | null | undefined): string {
  if (!iso) return "—";
  const diffMin = Math.floor((Date.now() - new Date(iso).getTime()) / 60000);
  if (diffMin < 1) return "<1м";
  if (diffMin < 60) return `${diffMin}м`;
  const h = Math.floor(diffMin / 60);
  if (h < 24) return `${h}ч`;
  return `${Math.floor(h / 24)}д`;
}
