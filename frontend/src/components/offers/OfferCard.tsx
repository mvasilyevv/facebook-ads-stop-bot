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
      {/* ── Header ── */}
      <header className="px-5 pt-5 pb-4 border-b border-bg-3">
        <div className="flex items-start justify-between gap-3 mb-2">
          {/* Код оффера — акцент mono */}
          <span className="font-display text-[13px] tracking-[0.06em] text-accent font-medium">
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

      {/* ── Метрики ── */}
      <div className="px-5 py-4 flex-1">
        <div className="grid grid-cols-2 gap-x-4 gap-y-3">
          <MetricCell label="Spend" value={fmtSpend(metrics?.spend)} />
          <MetricCell label="CPL" value={fmtCpl(metrics?.cost_per_lead)} />
          <MetricCell label="Leads" value={metrics?.leads != null ? String(metrics.leads) : "—"} />
          <MetricCell
            label="Deposits"
            value={metrics?.deposits != null ? String(metrics.deposits) : "—"}
          />
          <MetricCell
            label="Active ads"
            value={
              metrics?.active_ads_count != null ? String(metrics.active_ads_count) : "—"
            }
          />
          <MetricCell
            label="Stop alerts"
            value={
              metrics?.stop_alerts_count != null
                ? String(metrics.stop_alerts_count)
                : "—"
            }
            danger={
              metrics?.stop_alerts_count != null && metrics.stop_alerts_count > 0
            }
          />
        </div>
      </div>

      {/* ── Footer actions ── */}
      <footer className="px-5 py-3 border-t border-bg-3 bg-bg-0 flex items-center gap-2 justify-end">
        <Button
          variant="ghost"
          size="sm"
          leftIcon={<Settings size={13} />}
          onClick={() => onEditRules(offer)}
          aria-label={`Правила оффера ${offer.code}`}
        >
          Правила
        </Button>
        <Button
          variant="ghost"
          size="sm"
          leftIcon={<Pencil size={13} />}
          onClick={() => onEditOffer(offer)}
          aria-label={`Редактировать оффер ${offer.code}`}
        >
          Изменить
        </Button>
        <Button
          variant="ghost-danger"
          size="sm"
          leftIcon={<Trash2 size={13} />}
          onClick={() => onDelete(offer)}
          aria-label={`Удалить оффер ${offer.code}`}
        >
          Удалить
        </Button>
      </footer>
    </article>
  );
}

// ─── Вспомогательный компонент метрики ───────────────────────────────────────

function MetricCell({
  label,
  value,
  danger,
}: {
  label: string;
  value: string;
  danger?: boolean;
}) {
  return (
    <div>
      <div className="font-display text-[9.5px] tracking-[0.12em] uppercase text-bg-8 mb-0.5">
        {label}
      </div>
      <div
        className={cn(
          "font-display text-[14px] font-medium tabular-nums",
          danger ? "text-danger" : "text-bg-11",
        )}
      >
        {value}
      </div>
    </div>
  );
}
