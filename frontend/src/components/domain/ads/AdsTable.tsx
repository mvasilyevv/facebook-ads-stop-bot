/**
 * AdsTable — виртуализованная таблица объявлений под канон ads-web.jsx.
 *
 * Особенности:
 *   - @tanstack/react-virtual: в DOM только видимое окно строк (+overscan).
 *   - Сетка колонок строго по эталону:
 *       [checkbox 40][AD 1fr][OFFER 64][STATE 130][SPEND 96][CPL 74]
 *       [FREQ 62][CPM 62][CTR 62][ROAS 66][⋯ 40].
 *   - Высота строки density-driven через var(--row-h) (44/34/28).
 *   - Header-row: bg-2, 32px, eyebrow-лейблы колонок.
 *   - Строка: checkbox + geo-thumb + ad-name (mono, truncate) + первый rule-pill
 *     + offer-chip + FSM-badge + right-aligned tnum (флаги CPL>30/FREQ>4/ROAS<1).
 *   - selected → accent-bg + 2px accent left-border; cursor → bg-2.
 *
 * Контролируется снаружи: selected (Set), cursor (index), колбэки.
 * ROAS/значения, которых нет в API → «—» (без фейка).
 */

import { useRef, type RefObject } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { Check, ExternalLink } from "lucide-react";

import {
  ALERT_STATE_LABELS,
  alertStateToBadgeVariant,
  normalizeAlertState,
} from "@fb/shared";
import type { AdSnapshot } from "@fb/shared";

import { Badge } from "@/components/ui/Badge";
import { cn } from "@/lib/utils/cn";

import { RulePill } from "./RulePill";
import {
  adAccountId,
  adsManagerAdUrl,
  readAdMetrics,
  deriveGeo,
  money1,
  isCplBad,
  isFreqBad,
  isRoasBad,
  shortAccountId,
} from "./adHelpers";

// Сетка колонок — единый источник для header и строк.
// CAB (56px, мульти-кабинет) — между OFFER и STATE: хвост ID кабинета, full — в title.
const COLS =
  "40px minmax(0,1fr) 64px 56px 130px 96px 74px 62px 62px 62px 66px 40px";

// Правые числовые колонки (для header-лейблов).
const NUM_HEADERS = ["SPEND", "CPL", "FREQ", "CPM", "CTR", "ROAS"];

// Eyebrow-стиль лейблов колонок (канон: 10px, 600, uppercase, tracking).
const COL_HEAD =
  "font-display text-[10px] font-semibold uppercase tracking-[0.12em] text-bg-9";

export interface AdsTableProps {
  rows: AdSnapshot[];
  /** Множество выбранных fb_ad_id. */
  selected: Set<string>;
  /** Индекс строки под keyboard-курсором (-1 = нет). */
  cursor: number;
  /** Высота строки (px) из density — для estimateSize виртуализатора. */
  rowHeight: number;
  /** ref на scroll-контейнер (для скролла курсора во вью). */
  scrollRef?: RefObject<HTMLDivElement | null>;

  onToggleSelect: (fbAdId: string) => void;
  onOpen: (ad: AdSnapshot) => void;
  /** Выбрать/снять все строки. */
  onSelectAll?: () => void;
}

export function AdsTable({
  rows,
  selected,
  cursor,
  rowHeight,
  scrollRef,
  onToggleSelect,
  onOpen,
  onSelectAll,
}: AdsTableProps) {
  const innerRef = useRef<HTMLDivElement | null>(null);
  // Если внешний ref не передан — используем внутренний.
  const containerRef = scrollRef ?? innerRef;

  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => containerRef.current,
    estimateSize: () => rowHeight,
    overscan: 8,
  });

  const items = virtualizer.getVirtualItems();
  const totalHeight = virtualizer.getTotalSize();

  return (
    <div
      className="flex flex-col min-h-0 flex-1 border border-bg-6"
      role="table"
      aria-label="Объявления"
      aria-rowcount={rows.length}
    >
      {/* ── Header row ──────────────────────────────────────────────────── */}
      <div
        className="grid items-center h-8 bg-bg-2 border-b border-bg-6 shrink-0"
        style={{ gridTemplateColumns: COLS }}
        role="row"
      >
        {/* Select-all checkbox */}
        <span className="flex items-center justify-center h-full">
          {onSelectAll ? (
            <span
              role="checkbox"
              aria-checked={rows.length > 0 && selected.size === rows.length}
              aria-label="Выбрать все объявления"
              onClick={onSelectAll}
              className={cn(
                "size-[15px] inline-flex items-center justify-center border cursor-pointer",
                rows.length > 0 && selected.size === rows.length
                  ? "border-accent bg-accent"
                  : "border-bg-7",
              )}
            >
              {rows.length > 0 && selected.size === rows.length ? (
                <Check size={11} strokeWidth={3} className="text-bg-0" />
              ) : rows.length > 0 && selected.size > 0 ? (
                <span className="w-[7px] h-[2px] bg-accent" />
              ) : null}
            </span>
          ) : null}
        </span>
        <span className={cn(COL_HEAD, "pl-1")}>AD</span>
        <span className={COL_HEAD}>OFFER</span>
        <span className={COL_HEAD}>CAB</span>
        <span className={COL_HEAD}>STATE</span>
        {NUM_HEADERS.map((h) => (
          <span key={h} className={cn(COL_HEAD, "text-right pr-2")}>
            {h === "SPEND" ? (
              // Таблица всегда отсортирована по spend desc — показываем это явно.
              <span title="Сортировка: spend по убыванию">
                {h}
                <span aria-hidden="true" className="ml-0.5 text-bg-8">
                  ↓
                </span>
              </span>
            ) : (
              h
            )}
          </span>
        ))}
        <span />
      </div>

      {/* ── Virtualized body ────────────────────────────────────────────── */}
      <div
        ref={containerRef}
        className="flex-1 overflow-y-auto relative min-h-0"
        role="rowgroup"
        aria-label="Список объявлений"
      >
        <div style={{ height: totalHeight, position: "relative" }}>
          {items.map((vi) => {
            const ad = rows[vi.index]!;
            return (
              <AdRow
                key={ad.fb_ad_id}
                ad={ad}
                selected={selected.has(ad.fb_ad_id)}
                cursor={vi.index === cursor}
                top={vi.start}
                height={rowHeight}
                onToggleSelect={onToggleSelect}
                onOpen={onOpen}
              />
            );
          })}
        </div>
      </div>
    </div>
  );
}

// ─── Одна строка ────────────────────────────────────────────────────────────

interface AdRowProps {
  ad: AdSnapshot;
  selected: boolean;
  cursor: boolean;
  top: number;
  height: number;
  onToggleSelect: (fbAdId: string) => void;
  onOpen: (ad: AdSnapshot) => void;
}

function AdRow({ ad, selected, cursor, top, height, onToggleSelect, onOpen }: AdRowProps) {
  const m = readAdMetrics(ad);
  const state = normalizeAlertState(ad.alert_state);
  const firstRule = (ad.stop_rule_codes?.[0] ?? ad.warning_rule_codes?.[0]) || null;
  const geo = deriveGeo(ad);
  const amUrl = adsManagerAdUrl(ad);

  return (
    <div
      role="row"
      onClick={() => onOpen(ad)}
      className={cn(
        "grid items-center cursor-pointer border-b border-bg-5",
        "absolute left-0 right-0",
        selected ? "bg-accent-bg" : cursor ? "bg-bg-2" : "hover:bg-bg-1",
      )}
      style={{
        gridTemplateColumns: COLS,
        height,
        transform: `translateY(${top}px)`,
        borderLeft: selected ? "2px solid var(--accent)" : "2px solid transparent",
      }}
    >
      {/* Checkbox */}
      <span
        className="flex items-center justify-center h-full"
        onClick={(e) => {
          e.stopPropagation();
          onToggleSelect(ad.fb_ad_id);
        }}
      >
        <span
          role="checkbox"
          aria-checked={selected}
          aria-label={`Выбрать ${ad.ad_name}`}
          className={cn(
            "size-[15px] inline-flex items-center justify-center border",
            selected ? "border-accent bg-accent" : "border-bg-7",
          )}
        >
          {selected ? <Check size={11} strokeWidth={3} className="text-bg-0" /> : null}
        </span>
      </span>

      {/* AD: thumb + name + первый rule-pill */}
      <div className="flex items-center gap-2 min-w-0 pl-1">
        <GeoThumb geo={geo} dimmed={state === "disabled"} />
        <span
          className="font-display text-bg-11 truncate"
          style={{ fontSize: "var(--row-fs)" }}
          title={ad.ad_name}
        >
          {ad.ad_name}
        </span>
        {firstRule ? (
          <span className="shrink-0">
            <RulePill code={firstRule} />
          </span>
        ) : null}
      </div>

      {/* OFFER chip */}
      <span className="self-center">
        {ad.offer_code ? (
          <span className="inline-block h-[18px] leading-[18px] px-1.5 bg-bg-3 border border-bg-6 text-bg-10 font-display text-[10px] tracking-[0.04em] uppercase">
            {ad.offer_code}
          </span>
        ) : (
          <span className="text-bg-8 font-display text-[10px]">—</span>
        )}
      </span>

      {/* CAB: хвост ID кабинета (мульти-кабинет), полный — в title */}
      <CabCell id={adAccountId(ad)} />

      {/* STATE badge */}
      <span className="self-center pl-0.5">
        <Badge variant={alertStateToBadgeVariant(state)} size="sm">
          {ALERT_STATE_LABELS[state] ?? state}
        </Badge>
      </span>

      {/* Числа (right-aligned, tnum, флаги) */}
      <NumCell value={money1(m.spend)} />
      <NumCell value={m.cpl != null ? money1(m.cpl) : "—"} danger={isCplBad(m.cpl)} />
      <NumCell value={m.freq != null ? m.freq.toFixed(1) : "—"} danger={isFreqBad(m.freq)} />
      <NumCell value={m.cpm != null ? money1(m.cpm) : "—"} muted />
      <NumCell value={m.ctr != null ? `${m.ctr.toFixed(1)}%` : "—"} muted />
      <NumCell value={m.roas != null ? `${m.roas.toFixed(1)}×` : "—"} danger={isRoasBad(m.roas)} />

      {/* Открыть в Ads Manager (deep-link по кабинету; нет кабинета — пусто) */}
      <span
        className="flex items-center justify-center"
        onClick={(e) => e.stopPropagation()}
      >
        {amUrl ? (
          <a
            href={amUrl}
            target="_blank"
            rel="noopener noreferrer"
            aria-label={`Открыть ${ad.ad_name} в Ads Manager`}
            title="Открыть в Ads Manager"
            className="inline-flex items-center justify-center size-6 text-bg-8 hover:text-bg-11 transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
          >
            <ExternalLink size={14} aria-hidden="true" />
          </a>
        ) : null}
      </span>
    </div>
  );
}

// ─── Ячейка кабинета (мульти-кабинет) ───────────────────────────────────────

function CabCell({ id }: { id: string | null }) {
  if (!id) {
    return <span className="font-display text-[10px] text-bg-8 self-center">—</span>;
  }
  return (
    <span
      className="font-display tabular-nums text-[11px] text-bg-9 self-center truncate pr-1"
      title={`Кабинет ${id}`}
    >
      {shortAccountId(id)}
    </span>
  );
}

// ─── Geo-thumb (плейсхолдер) ───────────────────────────────────────────────

function GeoThumb({ geo, dimmed }: { geo: string; dimmed?: boolean }) {
  return (
    <div
      aria-hidden="true"
      className={cn(
        "w-10 h-6 shrink-0 bg-bg-2 border border-bg-6 flex items-center justify-center overflow-hidden",
        dimmed && "opacity-40",
      )}
    >
      <span className="font-display text-[8px] text-bg-8 tracking-[0.02em]">{geo}</span>
    </div>
  );
}

// ─── Числовая ячейка ────────────────────────────────────────────────────────

function NumCell({
  value,
  danger,
  muted,
}: {
  value: string;
  danger?: boolean;
  muted?: boolean;
}) {
  return (
    <div
      className={cn(
        "font-display tabular-nums text-right self-center px-2",
        danger ? "text-danger" : muted ? "text-bg-9" : "text-bg-11",
      )}
      style={{ fontSize: "var(--row-fs)" }}
    >
      {value}
    </div>
  );
}
