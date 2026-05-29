/**
 * OfferCard — карточка оффера в grid-листинге.
 * Показывает метрики за N дней из useOffersCompare.
 */

import { Settings2, Trash2, Pencil } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { Badge } from "@/components/ui/Badge";
import { Card } from "@/components/ui/Card";
import { formatSpend, formatInt } from "@/lib/utils/format";
import type { Offer, OfferCompareRow } from "@/lib/types/api";

interface OfferCardProps {
  offer: Offer;
  /** Метрики из compare-endpoint за N дней (может отсутствовать). */
  metrics?: OfferCompareRow;
  onEdit: (offer: Offer) => void;
  onDelete: (offer: Offer) => void;
  onRules: (offer: Offer) => void;
}

export function OfferCard({ offer, metrics, onEdit, onDelete, onRules }: OfferCardProps) {
  return (
    <Card
      padded
      className="flex flex-col gap-5"
      action={
        <div className="flex items-center gap-1.5">
          <Button
            variant="ghost"
            size="xs"
            aria-label="Правила"
            onClick={() => onRules(offer)}
          >
            <Settings2 size={13} aria-hidden="true" />
          </Button>
          <Button
            variant="ghost"
            size="xs"
            aria-label="Редактировать"
            onClick={() => onEdit(offer)}
          >
            <Pencil size={13} aria-hidden="true" />
          </Button>
          <Button
            variant="ghost"
            size="xs"
            aria-label="Удалить"
            onClick={() => onDelete(offer)}
          >
            <Trash2 size={13} className="text-danger" aria-hidden="true" />
          </Button>
        </div>
      }
    >
      {/* Заголовок оффера */}
      <div>
        <div className="flex items-center gap-2 mb-1">
          <span className="font-display font-semibold text-[15px] text-bg-11 tracking-tight">
            {offer.code}
          </span>
          {offer.vertical ? (
            <Badge variant="neutral" size="sm" withDot={false}>
              {offer.vertical}
            </Badge>
          ) : null}
          {!offer.is_active ? (
            <Badge variant="disabled" size="sm" withDot={false}>
              inactive
            </Badge>
          ) : null}
        </div>
        <div className="text-[13px] text-bg-10 truncate">{offer.name}</div>
      </div>

      {/* Метрики за период */}
      {metrics ? (
        <div className="grid grid-cols-4 gap-x-4 gap-y-3 border-t border-bg-5 pt-4">
          <MetricCell label="Spend" value={formatSpend(metrics.spend)} />
          <MetricCell label="Leads" value={formatInt(metrics.leads)} />
          <MetricCell label="Deposits" value={formatInt(metrics.deposits)} />
          <MetricCell label="Alerts" value={formatInt(metrics.stop_alerts_count)} dimmed={metrics.stop_alerts_count === 0} />
        </div>
      ) : (
        <div className="grid grid-cols-4 gap-x-4 gap-y-3 border-t border-bg-5 pt-4">
          {["Spend", "Leads", "Deposits", "Alerts"].map((label) => (
            <MetricCell key={label} label={label} value="—" />
          ))}
        </div>
      )}
    </Card>
  );
}

/** Ячейка метрики в карточке — лейбл + значение. */
function MetricCell({
  label,
  value,
  dimmed = false,
}: {
  label: string;
  value: string;
  dimmed?: boolean;
}) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-[0.1em] text-bg-8 font-display mb-0.5">
        {label}
      </div>
      <div
        className={[
          "font-numeric text-[13px] tabular-nums",
          dimmed ? "text-bg-8" : "text-bg-11",
        ].join(" ")}
      >
        {value}
      </div>
    </div>
  );
}
