/**
 * AdDrawer — drawer деталей объявления (канон ads-web.jsx AdDrawer).
 *
 * 560px, slide-in справа. Контент:
 *   header: eyebrow «гео · ОБЪЯВЛЕНИЕ» + полное ad-name + FSM-badge + offer-chip
 *           + ad_id.
 *   triggered-rule banner (если сработали правила).
 *   metrics-snapshot grid 4 кол (spend/CPL/CPM/CTR/freq/ROAS/leads/age) —
 *           flagged-ячейки danger.
 *   CPL sparkline (8 точек) — из timeline (CPL = spend/leads по точке).
 *   task-history секция (alerts + tasks DESC).
 *   footer: Snooze 1ч / Disable (MONEY: confirm-with-typing).
 *
 * Заголовок строится мгновенно из переданного AdSnapshot (строка таблицы),
 * детали (timeline) догружаются. Esc / scrim / крест закрывают (через Drawer).
 *
 * Поддерживает loading-стейт: при isLoading (или ad=null) рендерит skeleton
 * внутри Drawer (для холодного deep-link /ads/$fbAdId без кэша).
 */

import { useMemo, useState } from "react";
import { Ban, Clock } from "lucide-react";

import {
  ALERT_STATE_LABELS,
  alertStateToBadgeVariant,
  normalizeAlertState,
  formatRelativeTime,
  type AdSnapshot,
} from "@fb/shared";
import type { components } from "@fb/shared/api/generated";

import { Drawer } from "@/components/ui/Drawer";
import { Button } from "@/components/ui/Button";
import { Badge } from "@/components/ui/Badge";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { Skeleton } from "@/components/ui/Skeleton";
import { Sparkline } from "@/components/data/charts/Sparkline";
import { Eyebrow } from "@/components/data/Eyebrow";
import { toast } from "@/components/ui/Toast";
import { cn } from "@/lib/utils/cn";

import { useAdTimeline, useSnoozeAd, useBulkDisable } from "@/lib/api/ads";
import {
  adAccountId,
  readAdMetrics,
  deriveGeo,
  money1,
  isCplBad,
  isFreqBad,
  isRoasBad,
  num,
} from "./adHelpers";
import { RulePills } from "./RulePill";

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
}

export function AdDrawer({ ad, onClose, isLoading = false, fbAdId }: AdDrawerProps) {
  const [confirmOpen, setConfirmOpen] = useState(false);

  // id резолвится из snapshot или из явного пропа (для холодного deep-link).
  const resolvedId = ad?.fb_ad_id ?? fbAdId ?? "";

  // ── Timeline (метрики/алерты/задачи) — хуки всегда в топе ──────────────────
  const { data: timeline, isLoading: timelineLoading } = useAdTimeline(resolvedId, {
    include_metrics: true,
    include_alerts: true,
    include_tasks: true,
  });

  const snooze = useSnoozeAd(resolvedId);
  const bulkDisable = useBulkDisable();
  const pending = snooze.isPending || bulkDisable.isPending;

  // CPL sparkline (8 точек): CPL = spend/leads по точкам timeline.
  const cplSpark = useMemo<number[]>(() => {
    const metrics = (timeline?.metrics ?? []) as MetricRow[];
    const pts: number[] = [];
    for (const row of metrics) {
      const spend = num(row.spend);
      const leads = row.leads ?? null;
      if (spend != null && leads != null && leads > 0) pts.push(spend / leads);
    }
    return pts.slice(-8);
  }, [timeline]);

  // Task-history (alerts + tasks, DESC).
  const historyItems = useMemo<HistoryEntry[]>(() => {
    const out: HistoryEntry[] = [];
    for (const a of (timeline?.alerts ?? []) as AlertRow[]) {
      out.push({
        id: `al-${a.id}`,
        ts: a.created_at,
        kind: a.stage === "stop" ? "stop" : "warning",
        title: a.stage === "stop" ? "Сработал STOP" : "Сработал WARNING",
        rules: a.matched_rule_codes,
      });
    }
    for (const t of (timeline?.tasks ?? []) as TaskRow[]) {
      out.push({
        id: `tk-${t.id}`,
        ts: t.created_at,
        kind: "task",
        title: taskTitle(t.task_type),
        meta: `${t.status} · ${t.requested_by}`,
      });
    }
    out.sort((x, y) => new Date(y.ts).getTime() - new Date(x.ts).getTime());
    return out;
  }, [timeline]);

  async function handleSnooze() {
    if (!resolvedId) return;
    // L13: ждём результат — success только при фактическом успехе (был optimistic
    // toast при fire-and-forget). Ошибку покажет глобальный MutationCache.onError.
    try {
      await snooze.mutateAsync({ minutes: 60 });
      toast.success("Snooze на 1 час");
    } catch {
      /* глобальный onError покажет ошибку */
    }
  }

  // MONEY: disable одного — idempotency_token=randomUUID (отдельное поле).
  async function handleDisableConfirm() {
    if (!resolvedId) return;
    await bulkDisable.mutateAsync({
      fb_ad_ids: [resolvedId],
      idempotency_token: crypto.randomUUID(),
      reason: "manual disable via drawer",
    });
    toast.success("Создана disable-задача");
    onClose();
  }

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
        <div
          className="flex flex-col gap-4"
          role="status"
          aria-label="Загрузка данных объявления"
        >
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
  const state = normalizeAlertState(ad.alert_state);
  const geo = deriveGeo(ad);
  const m = readAdMetrics(ad);
  const rules = [...(ad.stop_rule_codes ?? []), ...(ad.warning_rule_codes ?? [])];
  const age = relativeAge(ad.last_seen_at);

  // Расширенные поля крео и адсета (доступны после добавления бэком).
  const adExt = ad as AdSnapshot & {
    creative_thumb_url?: string | null;
    creative_image_url?: string | null;
    adset_pixel_id?: string | null;
    adset_daily_budget?: string | null;
    adset_lifetime_budget?: string | null;
    adset_budget_remaining?: string | null;
    learning_stage?: string | null;
  };
  const creativeThumb = adExt.creative_thumb_url ?? null;
  const creativeImage = adExt.creative_image_url ?? null;
  const creativeSrc = creativeImage || creativeThumb;
  const pixelId = adExt.adset_pixel_id ?? null;
  const dailyBudget = adExt.adset_daily_budget ?? null;
  const lifetimeBudget = adExt.adset_lifetime_budget ?? null;
  const budgetRemaining = adExt.adset_budget_remaining ?? null;
  const learningStage = adExt.learning_stage ?? null;

  const metricCells: MetricCell[] = [
    { k: "spend", v: money1(m.spend) },
    { k: "CPL", v: m.cpl != null ? money1(m.cpl) : "—", flag: isCplBad(m.cpl) },
    { k: "CPM", v: m.cpm != null ? money1(m.cpm) : "—" },
    { k: "CTR", v: m.ctr != null ? `${m.ctr.toFixed(1)}%` : "—" },
    { k: "freq", v: m.freq != null ? m.freq.toFixed(1) : "—", flag: isFreqBad(m.freq) },
    { k: "ROAS", v: m.roas != null ? `${m.roas.toFixed(1)}×` : "—", flag: isRoasBad(m.roas) },
    { k: "leads", v: m.leads != null ? String(m.leads) : "—" },
    { k: "age", v: age },
  ];

  return (
    <>
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
            <Badge variant={alertStateToBadgeVariant(state)} size="sm">
              {ALERT_STATE_LABELS[state] ?? state}
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
          // Уже отключённое объявление (alert_state='disabled' после авто/ручного pause)
          // не предлагаем отключать снова — показываем статус. Snooze тоже бессмыслен.
          state === "disabled" ? (
            <div
              className="flex w-full items-center justify-center gap-2 py-1 text-[13px] text-bg-9"
              role="status"
            >
              <Ban size={14} aria-hidden="true" />
              Объявление отключено
            </div>
          ) : (
            <div className="flex w-full gap-3">
              <Button
                variant="secondary"
                className="flex-1"
                leftIcon={<Clock size={15} aria-hidden="true" />}
                onClick={handleSnooze}
                disabled={pending}
                aria-label="Снуз на 1 час"
              >
                Snooze 1ч
              </Button>
              <Button
                variant="danger"
                className="flex-1"
                leftIcon={<Ban size={15} aria-hidden="true" />}
                onClick={() => setConfirmOpen(true)}
                disabled={pending}
                loading={bulkDisable.isPending}
                aria-label="Отключить объявление вручную"
              >
                Отключить
              </Button>
            </div>
          )
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

          {/* Секция КРЕАТИВ: превью + бюджет / пиксель / фаза обучения */}
          {creativeSrc && (
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
                {(dailyBudget || lifetimeBudget || budgetRemaining || pixelId || learningStage) && (
                  <div className="border border-[var(--hairline)] rounded-[var(--radius-2)] overflow-hidden">
                    {dailyBudget && (
                      <CreativeMetaRow
                        label="Бюджет (день)"
                        value={formatBudgetMinorUnits(dailyBudget)}
                      />
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
          )}

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

          {/* Метрики-снимок */}
          <section>
            <Eyebrow className="mb-3">МЕТРИКИ · СНИМОК</Eyebrow>
            <div className="grid grid-cols-4 border border-[var(--hairline)] rounded-[var(--radius-2)] overflow-hidden">
              {metricCells.map((c, i) => (
                <div
                  key={c.k}
                  className={cn(
                    "px-3 py-2.5",
                    i % 4 !== 3 && "border-r border-[var(--hairline)]",
                    i >= 4 && "border-t border-[var(--hairline)]",
                    c.flag && "bg-danger-bg",
                  )}
                >
                  <div
                    className={cn(
                      "font-display text-[9px] font-semibold uppercase tracking-[0.1em]",
                      c.flag ? "text-danger" : "text-bg-9",
                    )}
                  >
                    {c.k}
                  </div>
                  <div
                    className={cn(
                      "font-display tabular-nums text-[15px] mt-1",
                      c.flag ? "text-danger" : "text-bg-11",
                    )}
                  >
                    {c.v}
                  </div>
                </div>
              ))}
            </div>
          </section>

          {/* CPL sparkline */}
          <section>
            <Eyebrow className="mb-3">CPL · 8 ТОЧЕК</Eyebrow>
            <div className="bg-bg-1 border border-[var(--hairline)] rounded-[var(--radius-2)] p-4">
              {timelineLoading ? (
                <Skeleton height={70} className="w-full" />
              ) : cplSpark.length >= 2 ? (
                <Sparkline
                  data={cplSpark}
                  color={isCplBad(m.cpl) ? "var(--danger)" : "var(--accent)"}
                  w={496}
                  h={70}
                  fill
                />
              ) : (
                <div className="text-[13px] text-bg-9">Недостаточно данных по CPL.</div>
              )}
            </div>
          </section>

          {/* Task history */}
          <section>
            <Eyebrow className="mb-3">ИСТОРИЯ ЗАДАЧ</Eyebrow>
            {timelineLoading ? (
              <div className="flex flex-col gap-2">
                {Array.from({ length: 3 }).map((_, i) => (
                  <Skeleton key={i} height={36} className="w-full" />
                ))}
              </div>
            ) : historyItems.length === 0 ? (
              <div className="text-[13px] text-bg-9">Задач по объявлению нет.</div>
            ) : (
              <div className="flex flex-col">
                {historyItems.map((h) => (
                  <HistoryRow key={h.id} entry={h} />
                ))}
              </div>
            )}
          </section>
        </div>
      </Drawer>

      {/* MONEY: confirm-with-typing DISABLE */}
      <ConfirmDialog
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
        title="Отключить объявление?"
        description={`Будет создана disable-задача для ${resolvedId}. Действие необратимо без ручного включения.`}
        confirmWord="DISABLE"
        confirmLabel="Отключить"
        confirmVariant="danger"
        onConfirm={handleDisableConfirm}
      />
    </>
  );
}

// ─── Sub-компоненты ───────────────────────────────────────────────────────────

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
      <span className="text-bg-7">/</span>
      {/* Отдельный span чтобы getByText("ОБЪЯВЛЕНИЕ") работал в тестах */}
      <span>ОБЪЯВЛЕНИЕ</span>
    </span>
  );
}

interface MetricCell {
  k: string;
  v: string;
  flag?: boolean;
}

interface HistoryEntry {
  id: string;
  ts: string;
  kind: "warning" | "stop" | "task";
  title: string;
  rules?: string[];
  meta?: string;
}

const KIND_DOT: Record<HistoryEntry["kind"], string> = {
  warning: "var(--fsm-warning)",
  stop: "var(--fsm-stop)",
  task: "var(--accent)",
};

function HistoryRow({ entry }: { entry: HistoryEntry }) {
  return (
    <div className="flex items-start gap-3 py-2.5 border-b border-[var(--hairline)] last:border-b-0">
      <span
        aria-hidden="true"
        className="size-[7px] rounded-full mt-1.5 shrink-0"
        style={{ background: KIND_DOT[entry.kind] }}
      />
      <div className="min-w-0 flex-1">
        <div className="flex items-center justify-between gap-2">
          <span className="text-[13px] text-bg-11">{entry.title}</span>
          <span className="font-display text-[11px] text-bg-8 tabular-nums shrink-0">
            {formatRelativeTime(entry.ts)}
          </span>
        </div>
        {entry.rules && entry.rules.length > 0 ? (
          <div className="mt-1.5">
            <RulePills codes={entry.rules} max={4} />
          </div>
        ) : null}
        {entry.meta ? (
          <div className="font-display text-[11px] text-bg-9 mt-1">{entry.meta}</div>
        ) : null}
      </div>
    </div>
  );
}

// ─── Компоненты секции КРЕАТИВ ────────────────────────────────────────────────

/**
 * Строка в блоке «бюджет/пиксель» внутри секции КРЕАТИВ.
 * Аналог HierRow по стилю (Eyebrow-канон).
 */
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
        className={cn(
          "text-[13px] text-bg-11 truncate text-right",
          mono && "font-mono text-[12px]",
        )}
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
  return (
    <span className="font-mono text-[12px] text-bg-9">{stage}</span>
  );
}

// ─── Хелперы ──────────────────────────────────────────────────────────────────

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

/** Заголовок задачи по типу. */
function taskTitle(taskType: string): string {
  if (taskType === "disable") return "Disable-задача";
  if (taskType === "enable") return "Enable-задача";
  if (taskType === "meta_api_mutation") return "Действие через API";
  return `Задача: ${taskType}`;
}
