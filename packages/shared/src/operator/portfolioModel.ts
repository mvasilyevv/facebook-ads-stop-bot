import type { OperatorCurrencyGroup } from "./contracts";

export interface OperatorPortfolioScaleModel {
  /** Money may be rendered only when both response scope and group are USD. */
  usdConfirmed: boolean;
  /** Shared upper bound used by every cabinet row in the currency group. */
  maximum: number;
  /** Stable desktop tick positions, including both ends of the scale. */
  ticks: readonly [number, number, number, number, number];
}

/**
 * Builds the shared, renderer-independent portfolio scale used by web and TMA.
 * The model fails closed: non-USD or unconfirmed scope receives an inert scale.
 */
export function buildOperatorPortfolioScale(
  group: OperatorCurrencyGroup,
  usdScopeConfirmed: boolean,
): OperatorPortfolioScaleModel {
  const usdConfirmed = usdScopeConfirmed && group.currency === "USD";
  const maximum = usdConfirmed
    ? roundedScaleMaximum(
        group.cabinets.flatMap((cabinet) => [
          cabinet.totals.spend,
          cabinet.totals.base,
          cabinet.totals.stop,
        ]),
      )
    : 1;
  return {
    usdConfirmed,
    maximum,
    ticks: [0, maximum * 0.25, maximum * 0.5, maximum * 0.75, maximum],
  };
}

/** Maps one decimal-string value onto the shared scale without inventing data. */
export function operatorPortfolioScalePosition(
  value: string | null,
  scale: OperatorPortfolioScaleModel,
): number | null {
  if (!scale.usdConfirmed) return null;
  const parsed = parseDecimal(value);
  if (parsed === null || scale.maximum <= 0) return null;
  return Math.max(0, Math.min(100, (parsed / scale.maximum) * 100));
}

function roundedScaleMaximum(values: Array<string | null>): number {
  const known = values
    .map(parseDecimal)
    .filter((value): value is number => value !== null);
  const highest = Math.max(1, ...known);
  return Math.ceil((highest * 1.08) / 5) * 5;
}

function parseDecimal(value: string | null): number | null {
  if (value === null || value.trim() === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}
