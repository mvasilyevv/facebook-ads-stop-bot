/**
 * Reviewed currency exponents shared by every frontend money boundary.
 *
 * This is intentionally repository-owned instead of derived from the host ICU:
 * `Intl.supportedValuesOf("currency")` is optional and omits valid codes such as
 * VED. Keep this map in exact parity with `core.money.SUPPORTED_CURRENCY_EXPONENTS`.
 * A valid ISO identifier without a reviewed exponent remains unsupported.
 */

export type CurrencyExponent = 0 | 2 | 3;

const EXPONENT_ZERO = [
  "BIF",
  "CLP",
  "DJF",
  "GNF",
  "ISK",
  "JPY",
  "KMF",
  "KRW",
  "PYG",
  "RWF",
  "UGX",
  "VND",
  "VUV",
  "XAF",
  "XOF",
  "XPF",
] as const;

const EXPONENT_TWO = [
  "AED",
  "AFN",
  "ALL",
  "AMD",
  "ANG",
  "AOA",
  "ARS",
  "AUD",
  "AWG",
  "AZN",
  "BAM",
  "BBD",
  "BDT",
  "BGN",
  "BMD",
  "BND",
  "BOB",
  "BRL",
  "BSD",
  "BTN",
  "BWP",
  "BYN",
  "BZD",
  "CAD",
  "CDF",
  "CHF",
  "CNY",
  "COP",
  "CRC",
  "CVE",
  "CZK",
  "DKK",
  "DOP",
  "DZD",
  "EGP",
  "ERN",
  "ETB",
  "EUR",
  "FJD",
  "FKP",
  "GBP",
  "GEL",
  "GHS",
  "GIP",
  "GMD",
  "GTQ",
  "GYD",
  "HKD",
  "HNL",
  "HTG",
  "HUF",
  "IDR",
  "ILS",
  "INR",
  "IRR",
  "JMD",
  "KES",
  "KGS",
  "KHR",
  "KPW",
  "KYD",
  "KZT",
  "LAK",
  "LBP",
  "LKR",
  "LRD",
  "LSL",
  "MAD",
  "MDL",
  "MGA",
  "MKD",
  "MMK",
  "MNT",
  "MOP",
  "MRU",
  "MUR",
  "MVR",
  "MWK",
  "MXN",
  "MYR",
  "MZN",
  "NAD",
  "NGN",
  "NIO",
  "NOK",
  "NPR",
  "NZD",
  "PAB",
  "PEN",
  "PGK",
  "PHP",
  "PKR",
  "PLN",
  "QAR",
  "RON",
  "RSD",
  "RUB",
  "SAR",
  "SBD",
  "SCR",
  "SDG",
  "SEK",
  "SGD",
  "SHP",
  "SLE",
  "SOS",
  "SRD",
  "SSP",
  "STN",
  "SVC",
  "SYP",
  "SZL",
  "THB",
  "TJS",
  "TMT",
  "TOP",
  "TRY",
  "TTD",
  "TWD",
  "TZS",
  "UAH",
  "USD",
  "UYU",
  "UZS",
  "VED",
  "VES",
  "WST",
  "YER",
  "ZAR",
  "ZMW",
  "ZWG",
] as const;

const EXPONENT_THREE = [
  "BHD",
  "IQD",
  "JOD",
  "KWD",
  "LYD",
  "OMR",
  "TND",
] as const;

function entries(
  codes: readonly string[],
  exponent: CurrencyExponent,
): Array<[string, CurrencyExponent]> {
  return codes.map((code) => [code, exponent]);
}

export const SUPPORTED_CURRENCY_EXPONENTS: Readonly<
  Record<string, CurrencyExponent>
> = Object.freeze(
  Object.fromEntries([
    ...entries(EXPONENT_ZERO, 0),
    ...entries(EXPONENT_TWO, 2),
    ...entries(EXPONENT_THREE, 3),
  ]) as Record<string, CurrencyExponent>,
);

export function supportedCurrencyExponent(
  value: unknown,
): CurrencyExponent | null {
  if (
    typeof value !== "string" ||
    value !== value.trim().toUpperCase() ||
    !Object.hasOwn(SUPPORTED_CURRENCY_EXPONENTS, value)
  ) {
    return null;
  }
  return SUPPORTED_CURRENCY_EXPONENTS[value] ?? null;
}
