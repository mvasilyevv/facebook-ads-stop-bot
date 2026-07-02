/**
 * FunnelKpiPlate — блок воронки залива на базе KpiPlate.
 * full: сетка 2×3 (spend/клики/лиды/реги/депы + CPL).
 * compact: одна строка 3 плитки (spend/лиды/CPL) для Dashboard.
 * data=undefined → скелетоны; null-значения полей derived/totals → «—» (KpiPlate сам обрабатывает).
 */
import type { FunnelDerived, FunnelTotals } from "@fb/shared";
import { formatSpend, formatInt } from "@fb/shared";
import { KpiPlate, Skeleton } from "@/components/ui";
import type { KpiVariant } from "@/components/ui";

export interface FunnelKpiData {
  totals: FunnelTotals;
  derived: FunnelDerived;
}

interface FunnelKpiPlateProps {
  data?: FunnelKpiData;
  loading?: boolean;
  /** compact — одна строка 3 плитки (spend/лиды/CPL) для Dashboard. */
  compact?: boolean;
}

function SkeletonPlates({ count }: { count: number }) {
  return (
    <>
      {Array.from({ length: count }, (_, i) => (
        <div key={i} className="bg-bg-1 p-3 space-y-2">
          <Skeleton className="h-3 w-14" />
          <Skeleton className="h-7 w-16" />
          <Skeleton className="h-3 w-10" />
        </div>
      ))}
    </>
  );
}

export function FunnelKpiPlate({ data, loading, compact = false }: FunnelKpiPlateProps) {
  const totals = data?.totals;
  const derived = data?.derived;

  const fullItems: { eyebrow: string; label: string; value: string | number; variant: KpiVariant }[] = [
    { eyebrow: "СПЕНД", label: "потрачено", value: formatSpend(totals?.spend ?? null), variant: "default" },
    { eyebrow: "КЛИКИ", label: "переходов", value: formatInt(totals?.clicks ?? null), variant: "info" },
    { eyebrow: "ЛИДЫ", label: "всего", value: formatInt(totals?.leads ?? null), variant: "ok" },
    { eyebrow: "РЕГИСТРАЦИИ", label: "всего", value: formatInt(totals?.registrations ?? null), variant: "info" },
    { eyebrow: "ДЕПОЗИТЫ", label: "всего", value: formatInt(totals?.deposits ?? null), variant: "ok" },
    { eyebrow: "CPL", label: "цена лида", value: formatSpend(derived?.cpl ?? null), variant: "default" },
  ];

  const compactItems: { eyebrow: string; label: string; value: string | number; variant: KpiVariant }[] = [
    { eyebrow: "СПЕНД", label: "потрачено", value: formatSpend(totals?.spend ?? null), variant: "default" },
    { eyebrow: "ЛИДЫ", label: "всего", value: formatInt(totals?.leads ?? null), variant: "ok" },
    { eyebrow: "CPL", label: "цена лида", value: formatSpend(derived?.cpl ?? null), variant: "default" },
  ];

  const items = compact ? compactItems : fullItems;
  const cols = compact ? "grid-cols-3" : "grid-cols-2";

  return (
    <div className={`grid ${cols} gap-px bg-[var(--hairline)] rounded-[var(--radius-3)] overflow-hidden`}>
      {loading ? (
        <SkeletonPlates count={items.length} />
      ) : (
        items.map((item) => (
          <KpiPlate
            key={item.eyebrow}
            eyebrow={item.eyebrow}
            label={item.label}
            value={item.value}
            variant={item.variant}
          />
        ))
      )}
    </div>
  );
}
