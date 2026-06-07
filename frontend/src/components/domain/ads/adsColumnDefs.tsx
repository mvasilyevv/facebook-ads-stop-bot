/**
 * Column definitions для DataTable<AdSnapshot>.
 *
 * API (column-defs API):
 *   const cols = buildAdsColumns({ onCheckboxClick, selectedIds, allChecked, indeterminate });
 *   <DataTable columns={cols} ... />
 *
 * Колонки (11 штук, по спеке ads.html):
 *   0  — checkbox (select-all header / row-checkbox)
 *   1  — ad (thumb 56×32 + name + meta "id · offer")
 *   2  — offer pill
 *   3  — state badge
 *   4  — spend (num, sortable)
 *   5  — cpl (num, sortable)
 *   6  — ctr (num, sortable)
 *   7  — frequency (num, sortable)
 *   8  — leads (num, sortable)
 *   9  — deposits (num, sortable)
 *   10 — last seen (num, sortable)
 *
 * Цвет метрик: metric-bad/warn/good через вспомогательные CSS-классы.
 * Для получения threshold'ов (CPL и т.п.) компонент получает offer_rules через проп.
 */

import { type ColumnDef } from "@tanstack/react-table";
import { Badge } from "@/components/ui/Badge";
import { Checkbox } from "@/components/ui/Checkbox";
import { cn } from "@/lib/utils/cn";
import {
  formatSpend,
  formatInt,
  truncateAdId,
  ALERT_STATE_LABELS,
  type AlertState,
  alertStateToBadgeVariant,
} from "@fb/shared";
import type { AdSnapshot } from "@fb/shared";
import type { ColumnMeta } from "@/components/data/table/DataTable";

// ─── Утилиты ─────────────────────────────────────────────────────────────────

/** Безопасный парсинг числа из строки/числа/null. */
function toNum(v: string | number | null | undefined): number | null {
  if (v == null || v === "") return null;
  const n = typeof v === "string" ? Number.parseFloat(v) : v;
  return Number.isNaN(n) ? null : n;
}

// ─── Параметры сборки column-defs ────────────────────────────────────────────

export interface BuildAdsColumnsParams {
  /** Все ли строки выбраны (для select-all header). */
  allSelected: boolean;
  /** Некоторые строки выбраны (indeterminate). */
  indeterminate: boolean;
  /** Колбэк клика по select-all checkbox. */
  onSelectAll: () => void;
  /** Проверка: выбрана ли конкретная строка. */
  isRowSelected: (fbAdId: string) => boolean;
  /** Коллбэк toggle выбора строки. */
  onRowSelect: (fbAdId: string) => void;
  /** Клик на строку → открыть drawer. */
  onRowOpen: (fbAdId: string) => void;
}

// ─── Основная функция ─────────────────────────────────────────────────────────

export function buildAdsColumns({
  allSelected,
  indeterminate,
  onSelectAll,
  isRowSelected,
  onRowSelect,
  onRowOpen,
}: BuildAdsColumnsParams): ColumnDef<AdSnapshot, unknown>[] {
  return [
    // ── 0. Checkbox-колонка ────────────────────────────────────────────────
    {
      id: "select",
      size: 48,
      enableSorting: false,
      // Header: select-all с tri-state
      header: () => (
        <div className="flex items-center justify-center">
          <Checkbox
            checked={indeterminate ? "indeterminate" : allSelected}
            onChange={onSelectAll}
            aria-label="Выбрать все объявления"
          />
        </div>
      ),
      // Cell: checkbox строки, клик не всплывает к row-open
      cell: ({ row }) => {
        const ad = row.original;
        return (
          <div
            className="flex items-center justify-center"
            onClick={(e) => {
              e.stopPropagation();
              onRowSelect(ad.fb_ad_id);
            }}
          >
            <Checkbox
              checked={isRowSelected(ad.fb_ad_id)}
              onChange={() => onRowSelect(ad.fb_ad_id)}
              aria-label={`Выбрать ${ad.ad_name}`}
            />
          </div>
        );
      },
    },

    // ── 1. Ad cell: thumb + name + "id · offer" meta ───────────────────────
    {
      id: "ad",
      accessorKey: "ad_name",
      enableSorting: true,
      size: 320,
      header: "Объявление",
      cell: ({ row }) => {
        const ad = row.original;
        return (
          <AdCell
            ad={ad}
            onClick={() => onRowOpen(ad.fb_ad_id)}
          />
        );
      },
    },

    // ── 2. Offer pill ─────────────────────────────────────────────────────
    {
      id: "offer",
      accessorKey: "offer_code",
      enableSorting: true,
      size: 100,
      header: "Оффер",
      cell: ({ getValue }) => {
        const code = getValue<string | null | undefined>();
        if (!code) return <span className="text-bg-8 font-display text-[11px]">—</span>;
        return (
          <span className="inline-flex items-center px-2 py-0.5 bg-bg-3 border border-bg-6 text-bg-10 font-display text-[10.5px] tracking-[0.04em] uppercase">
            {code}
          </span>
        );
      },
    },

    // ── 3. State badge ────────────────────────────────────────────────────
    {
      id: "state",
      accessorKey: "alert_state",
      enableSorting: true,
      size: 130,
      header: "Статус",
      cell: ({ getValue }) => {
        const state = (getValue<string>() ?? "normal") as AlertState;
        return (
          <Badge variant={alertStateToBadgeVariant(state)} size="md">
            {ALERT_STATE_LABELS[state] ?? state}
          </Badge>
        );
      },
    },

    // ── 4. Spend ──────────────────────────────────────────────────────────
    {
      id: "spend",
      accessorFn: (row) => toNum(row.metrics?.spend),
      enableSorting: true,
      size: 90,
      header: "Spend",
      meta: { align: "right" } satisfies ColumnMeta,
      cell: ({ getValue }) => {
        const v = getValue<number | null>();
        return (
          <span className="font-display text-[13px] text-bg-11 tabular-nums">
            {v != null ? formatSpend(v) : "—"}
          </span>
        );
      },
    },

    // ── 5. CPL ────────────────────────────────────────────────────────────
    {
      id: "cpl",
      accessorFn: (row) => toNum(row.metrics?.cost_per_lead),
      enableSorting: true,
      size: 80,
      header: "CPL",
      meta: { align: "right" } satisfies ColumnMeta,
      cell: ({ getValue }) => {
        const v = getValue<number | null>();
        return (
          <span className={cn("font-display text-[13px] tabular-nums", "text-bg-11")}>
            {v != null ? formatSpend(v) : "—"}
          </span>
        );
      },
    },

    // ── 6. CTR ────────────────────────────────────────────────────────────
    {
      id: "ctr",
      accessorFn: (row) => toNum(row.metrics?.ctr),
      enableSorting: true,
      size: 72,
      header: "CTR",
      meta: { align: "right" } satisfies ColumnMeta,
      cell: ({ getValue }) => {
        const v = getValue<number | null>();
        return (
          <span className="font-display text-[13px] text-bg-11 tabular-nums">
            {v != null ? `${v.toFixed(2)}%` : "—"}
          </span>
        );
      },
    },

    // ── 7. Frequency ──────────────────────────────────────────────────────
    {
      id: "frequency",
      accessorFn: (row) => toNum(row.metrics?.frequency),
      enableSorting: true,
      size: 80,
      header: "Частота",
      meta: { align: "right" } satisfies ColumnMeta,
      cell: ({ getValue }) => {
        const v = getValue<number | null>();
        return (
          <span className="font-display text-[13px] text-bg-11 tabular-nums">
            {v != null ? v.toFixed(1) : "—"}
          </span>
        );
      },
    },

    // ── 8. Leads ──────────────────────────────────────────────────────────
    {
      id: "leads",
      accessorFn: (row) => row.metrics?.leads ?? null,
      enableSorting: true,
      size: 72,
      header: "Лиды",
      meta: { align: "right" } satisfies ColumnMeta,
      cell: ({ getValue }) => {
        const v = getValue<number | null>();
        return (
          <span className="font-display text-[13px] text-bg-11 tabular-nums">
            {v != null ? formatInt(v) : "—"}
          </span>
        );
      },
    },

    // ── 9. Deposits ───────────────────────────────────────────────────────
    {
      id: "deposits",
      accessorFn: (row) => row.metrics?.deposits ?? null,
      enableSorting: true,
      size: 80,
      header: "Депозиты",
      meta: { align: "right" } satisfies ColumnMeta,
      cell: ({ getValue }) => {
        const v = getValue<number | null>();
        return (
          <span className="font-display text-[13px] text-bg-11 tabular-nums">
            {v != null ? formatInt(v) : "—"}
          </span>
        );
      },
    },

    // ── 10. Last seen ─────────────────────────────────────────────────────
    {
      id: "last_seen",
      accessorFn: (row) =>
        row.last_seen_at ? new Date(row.last_seen_at).getTime() : null,
      enableSorting: true,
      size: 100,
      header: "Посл. скан",
      meta: { align: "right" } satisfies ColumnMeta,
      cell: ({ row }) => {
        const ts = row.original.last_seen_at;
        return (
          <span className="font-display text-[11px] text-bg-9 tracking-wide tabular-nums">
            {ts ? formatRelativeTime(ts) : "—"}
          </span>
        );
      },
    },
  ];
}

// ─── Ad cell субкомпонент ────────────────────────────────────────────────────

/** Ячейка объявления: миниатюра 56×32 + название + "id · offer" meta. */
function AdCell({
  ad,
  onClick,
}: {
  ad: AdSnapshot;
  onClick: () => void;
}) {
  const isDisabled = ad.alert_state === "disabled";

  return (
    <div
      className="flex items-center gap-3 min-w-0 cursor-pointer"
      onClick={onClick}
    >
      {/* Thumb 56×32 с диагональным sheen (по спеке ads.html .ad-thumb) */}
      <div
        className={cn(
          "shrink-0 w-14 h-8 bg-bg-3 border border-bg-5 overflow-hidden relative",
          isDisabled && "opacity-40",
        )}
        aria-hidden="true"
      >
        {/* Диагональный sheen */}
        <div
          className="absolute inset-0 pointer-events-none"
          style={{
            background:
              "linear-gradient(135deg, transparent 48%, rgba(245,241,232,0.04) 50%, transparent 52%)",
          }}
        />
      </div>

      {/* Текстовый блок */}
      <div className="min-w-0 flex-1">
        {/* Название объявления */}
        <div
          className={cn(
            "font-display text-[13px] tracking-tight truncate",
            isDisabled ? "text-bg-9" : "text-bg-11",
          )}
          title={ad.ad_name}
        >
          {ad.ad_name}
        </div>
        {/* id · offer */}
        <div className="font-display text-[10.5px] text-bg-9 tracking-[0.02em] mt-0.5 truncate">
          {truncateAdId(ad.fb_ad_id)}
          {ad.offer_code ? (
            <span className="text-bg-7"> · {ad.offer_code}</span>
          ) : null}
        </div>
      </div>
    </div>
  );
}

// ─── Локальный форматтер времени ─────────────────────────────────────────────

/** Краткое относительное время: "только что", "5 мин", "2 ч", "3 д". */
function formatRelativeTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const now = Date.now();
  const diff = now - new Date(iso).getTime();
  const sec = Math.floor(diff / 1000);
  if (sec < 60) return "только что";
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min} мин`;
  const hrs = Math.floor(min / 60);
  if (hrs < 24) return `${hrs} ч`;
  const days = Math.floor(hrs / 24);
  return `${days} д`;
}
