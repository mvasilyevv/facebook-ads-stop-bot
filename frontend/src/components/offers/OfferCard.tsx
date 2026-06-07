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
        "border bg-bg-1 flex flex-col transition-colors duration-[200ms]",
        isActive
          ? "border-bg-5 hover:border-bg-6"
          : "border-bg-4 opacity-60 hover:opacity-80",
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
            {isActive ? "active" : "inactive"}
          </Badge>
        </div>

        {/* Вертикаль */}
        {offer.vertical ? (
          <div className="font-display text-[10px] tracking-[0.12em] uppercase text-bg-8 mt-1">
            {offer.vertical}
          </div>
        ) : null}
      </header>

      {/* ── Метрики: 4 строки key-value (Spend / Leads / CPL / Alerts) ── */}
      <div
        className="flex-1"
        style={{ borderTop: "1px solid var(--bg-5)", padding: "var(--s-4) var(--s-5)", display: "flex", flexDirection: "column", gap: 10 }}
      >
        {(
          [
            ["Spend", fmtSpend(metrics?.spend), false],
            ["Leads", metrics?.leads != null ? String(metrics.leads) : "—", false],
            ["CPL", fmtCpl(metrics?.cost_per_lead), false],
            [
              "Alerts",
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
        style={{ borderTop: "1px solid var(--bg-5)", padding: "var(--s-3) var(--s-4)", display: "flex", gap: "var(--s-2)" }}
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

