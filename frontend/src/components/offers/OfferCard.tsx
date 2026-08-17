/**
 * OfferCard — карточка оффера в сетке Offers.
 *
 * Показывает подтверждённую конфигурацию оффера. Performance-метрики живут
 * только в state-aware Analytics, чтобы catalog card не превращал unknown в zero.
 * Actions: Редактировать правила, Редактировать оффер, Деактивировать.
 */

import { Settings, Pencil, PowerOff } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { cn } from "@/lib/utils/cn";
import type { Offer } from "@fb/shared";
import { formatSpend } from "@fb/shared/format/number";

interface OfferCardProps {
  offer: Offer;
  onEditOffer: (offer: Offer) => void;
  onEditRules: (offer: Offer) => void;
  onDeactivate: (offer: Offer) => void;
}

// ─── Вспомогательные функции ──────────────────────────────────────────────────

function fmtMoney(
  val: string | null | undefined,
  currency: string | null | undefined,
): string {
  if (val == null) return "Не задан";
  const formatted = formatSpend(val, currency);
  return formatted === "—" ? "Валюта не задана" : formatted;
}

// ─── Кабинеты оффера (мульти-кабинет) ────────────────────────────────────────

/** Чипы кабинетов; пустой список — warning: оффер выпадает из скана. */
function OfferAccounts({ offer }: { offer: Offer }) {
  const accounts = offer.ad_account_ids ?? [];

  if (accounts.length === 0) {
    return (
      <div className="font-display text-[12px] tracking-[0.04em] text-warning mt-2">
        кабинеты не заданы — оффер не сканируется
      </div>
    );
  }
  return (
    <div className="flex flex-wrap gap-1 mt-2" role="group" aria-label="Кабинеты оффера">
      {accounts.map((a) => (
        <span
          key={a}
          className="inline-block px-1.5 py-px bg-bg-2 border border-[var(--color-hairline)] rounded-[var(--radius-1)] text-bg-9 font-display text-[12px] tabular-nums"
          title={`Кабинет ${a}`}
        >
          {a.length > 8 ? `…${a.slice(-6)}` : a}
        </span>
      ))}
    </div>
  );
}

// ─── Компонент ────────────────────────────────────────────────────────────────

export function OfferCard({ offer, onEditOffer, onEditRules, onDeactivate }: OfferCardProps) {
  const isActive = offer.is_active;

  return (
    <article
      className={cn(
        "border rounded-[var(--radius-3)] overflow-hidden bg-bg-1 flex flex-col transition-colors duration-[200ms]",
        isActive
          ? "border-[var(--color-hairline)] hover:border-[var(--color-hairline-strong)]"
          : "border-[var(--color-hairline)] opacity-60 hover:opacity-80",
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
          <Badge variant={isActive ? "success" : "disabled"} size="sm" withDot>
            {isActive ? "активен" : "неактивен"}
          </Badge>
        </div>

        {/* Мульти-кабинет: кабинеты оффера (warning, если не заполнены — оффер вне скана) */}
        <OfferAccounts offer={offer} />
      </header>

      {/* ── Конфигурация. Метрики доступны в state-aware Analytics. ── */}
      <div
        className="flex-1"
        style={{
          borderTop: "1px solid var(--color-hairline)",
          padding: "var(--space-4) var(--space-5)",
          display: "flex",
          flexDirection: "column",
          gap: 10,
        }}
      >
        {(
          [
            ["Целевой CPA", fmtMoney(offer.cpa_threshold, offer.currency)],
            [
              "GEO",
              offer.countries && offer.countries.length > 0
                ? offer.countries.join(", ")
                : "Не заданы",
            ],
            ["Кабинетов", String(offer.ad_account_ids?.length ?? 0)],
          ] as [string, string][]
        ).map(([k, v]) => (
          <div
            key={k}
            style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}
          >
            <span className="text-[12px] text-bg-9">{k}</span>
            <span className="font-display text-[14px] tabular-nums text-bg-11">{v}</span>
          </div>
        ))}
      </div>

      {/* ── Footer actions: Правила + Изменить + Деактивировать ── */}
      <footer
        style={{
          borderTop: "1px solid var(--color-hairline)",
          padding: "var(--space-3) var(--space-4)",
          display: "flex",
          flexWrap: "wrap",
          gap: "var(--space-2)",
        }}
      >
        <Button
          variant="secondary"
          size="sm"
          leftIcon={<Settings size={13} />}
          onClick={() => onEditRules(offer)}
          aria-label={`Правила оффера ${offer.code}`}
          style={{ flex: "1 1 auto" }}
        >
          Правила
        </Button>
        <Button
          variant="ghost"
          size="sm"
          leftIcon={<Pencil size={13} />}
          onClick={() => onEditOffer(offer)}
          aria-label={`Редактировать оффер ${offer.code}`}
          style={{ flex: "1 1 auto" }}
        >
          Изменить
        </Button>
        {isActive ? (
          <Button
            variant="ghost-danger"
            size="sm"
            leftIcon={<PowerOff size={13} />}
            onClick={() => onDeactivate(offer)}
            aria-label={`Деактивировать оффер ${offer.code}`}
            style={{ flex: "1 1 auto" }}
          >
            Деактивировать
          </Button>
        ) : null}
      </footer>
    </article>
  );
}
