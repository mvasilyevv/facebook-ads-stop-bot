/**
 * OfferCard — карточка оффера в сетке Offers.
 *
 * Показывает: код, название, вертикаль, статус (active/inactive),
 * метрики из OfferCompareRow (spend, leads, deposits, cost_per_lead, active_ads_count).
 * Actions: Редактировать правила, Редактировать оффер, Удалить.
 */

import { Settings, Pencil, Trash2 } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { cn } from "@/lib/utils/cn";
import type { Offer } from "@fb/shared";
import type { components } from "@fb/shared/api/generated";

type OfferCompareRow = components["schemas"]["OfferCompareRow"];

interface OfferCardProps {
  offer: Offer;
  /** Метрики за выбранный период (может отсутствовать при загрузке). */
  metrics?: OfferCompareRow | null;
  onEditOffer: (offer: Offer) => void;
  onEditRules: (offer: Offer) => void;
  onDelete: (offer: Offer) => void;
}

// ─── Вспомогательные функции ──────────────────────────────────────────────────

/** Форматирует spend (строка вида "12345.67" → "$12 345.67"). */
function fmtSpend(val: string | null | undefined): string {
  if (!val) return "—";
  const n = parseFloat(val);
  if (isNaN(n)) return "—";
  return `$${n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

/** Форматирует cost_per_lead. */
function fmtCpl(val: string | null | undefined): string {
  if (!val) return "—";
  const n = parseFloat(val);
  if (isNaN(n)) return "—";
  return `$${n.toFixed(2)}`;
}

// ─── Кабинеты оффера (мульти-кабинет) ────────────────────────────────────────

/** Чипы кабинетов; пустой список — warning: оффер выпадает из скана. */
function OfferAccounts({ offer }: { offer: Offer }) {
  // Поле появляется в generated-типах после pnpm gen:api — до этого мягкий каст.
  const accounts =
    (offer as Offer & { ad_account_ids?: string[] }).ad_account_ids ?? [];

  if (accounts.length === 0) {
    return (
      <div className="font-display text-[10px] tracking-[0.04em] text-warning mt-2">
        кабинеты не заданы — оффер не сканируется
      </div>
    );
  }
  return (
    <div className="flex flex-wrap gap-1 mt-2" aria-label="Кабинеты оффера">
      {accounts.map((a) => (
        <span
          key={a}
          className="inline-block px-1.5 py-px bg-bg-2 border border-[var(--hairline)] rounded-[var(--radius-1)] text-bg-9 font-display text-[10px] tabular-nums"
          title={`Кабинет ${a}`}
        >
          {a.length > 8 ? `…${a.slice(-6)}` : a}
        </span>
      ))}
    </div>
  );
}

// ─── Компонент ────────────────────────────────────────────────────────────────

export function OfferCard({
  offer,
  metrics,
  onEditOffer,
  onEditRules,
  onDelete,
}: OfferCardProps) {
  const isActive = offer.is_active;

  return (
    <article
      className={cn(
        "border rounded-[var(--radius-3)] overflow-hidden bg-bg-1 flex flex-col transition-colors duration-[200ms]",
        isActive
          ? "border-[var(--hairline)] hover:border-[var(--hairline-strong)]"
          : "border-[var(--hairline)] opacity-60 hover:opacity-80",
      )}
      data-offer-id={offer.id}
      data-active={isActive}
    >
      {/* ── Header: код + badge ── */}
      <header className="px-5 pt-5 pb-4">
        <div className="flex items-start justify-between gap-3 mb-2">
          {/* Код оффера — mono, канон 15px weight 600 */}
          <span className="font-display text-[15px] tracking-[0.04em] text-bg-11 font-semibold">
            {offer.code}
          </span>
          <Badge
            variant={isActive ? "success" : "disabled"}
            size="sm"
            withDot
          >
            {isActive ? "активен" : "неактивен"}
          </Badge>
        </div>

        {/* Вертикаль */}
        {offer.vertical ? (
          <div className="font-display text-[10px] tracking-[0.12em] uppercase text-bg-8 mt-1">
            {offer.vertical}
          </div>
        ) : null}

        {/* Мульти-кабинет: кабинеты оффера (warning, если не заполнены — оффер вне скана) */}
        <OfferAccounts offer={offer} />
      </header>

      {/* ── Метрики: 4 строки key-value (Spend / Leads / CPL / Alerts) ── */}
      <div
        className="flex-1"
        style={{ borderTop: "1px solid var(--hairline)", padding: "var(--s-4) var(--s-5)", display: "flex", flexDirection: "column", gap: 10 }}
      >
        {(
          [
            ["Траты", fmtSpend(metrics?.spend), false],
            ["Лиды", metrics?.leads != null ? String(metrics.leads) : "—", false],
            ["CPL", fmtCpl(metrics?.cost_per_lead), false],
            [
              "Стоп-алерты",
              metrics?.stop_alerts_count != null ? String(metrics.stop_alerts_count) : "—",
              metrics?.stop_alerts_count != null && metrics.stop_alerts_count > 0,
            ],
          ] as [string, string, boolean][]
        ).map(([k, v, warn]) => (
          <div key={k} style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
            <span className="text-[12px] text-bg-9">{k}</span>
            <span
              className={cn(
                "font-display text-[14px] tabular-nums",
                warn ? "text-warning" : "text-bg-11",
              )}
            >
              {v}
            </span>
          </div>
        ))}
      </div>

      {/* ── Footer actions: Правила + Изменить + Удалить ── */}
      <footer
        style={{ borderTop: "1px solid var(--hairline)", padding: "var(--s-3) var(--s-4)", display: "flex", gap: "var(--s-2)" }}
      >
        <Button
          variant="secondary"
          size="sm"
          leftIcon={<Settings size={13} />}
          onClick={() => onEditRules(offer)}
          aria-label={`Правила оффера ${offer.code}`}
          style={{ flex: 1 }}
        >
          Правила
        </Button>
        <Button
          variant="ghost"
          size="sm"
          leftIcon={<Pencil size={13} />}
          onClick={() => onEditOffer(offer)}
          aria-label={`Редактировать оффер ${offer.code}`}
          style={{ flex: 1 }}
        >
          Изменить
        </Button>
        <Button
          variant="ghost-danger"
          size="sm"
          leftIcon={<Trash2 size={13} />}
          onClick={() => onDelete(offer)}
          aria-label={`Удалить оффер ${offer.code}`}
          style={{ flex: 1 }}
        >
          Удалить
        </Button>
      </footer>
    </article>
  );
}
