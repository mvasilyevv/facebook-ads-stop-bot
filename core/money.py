"""Strict monetary identity helpers.

Amounts are never assigned an implicit currency.  A value without a validated
three-letter ISO-style code remains unknown and must not participate in money
aggregation or safety decisions.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

# ISO 4217 active currency/fund/precious-metal codes. ``XXX`` (no currency)
# and ``XTS`` (testing) are deliberately excluded because neither is usable
# for a money decision.  Keeping the allowlist in the repository makes direct
# DB/user input deterministic and avoids treating arbitrary ``ZZZ`` as money.
_ISO_4217_CODES = frozenset(
    """
    AED AFN ALL AMD ANG AOA ARS AUD AWG AZN BAM BBD BDT BGN BHD BIF BMD BND
    BOB BOV BRL BSD BTN BWP BYN BZD CAD CDF CHE CHF CHW CLF CLP CNY COP COU
    CRC CUP CVE CZK DJF DKK DOP DZD EGP ERN ETB EUR FJD FKP GBP GEL GHS GIP
    GMD GNF GTQ GYD HKD HNL HTG HUF IDR ILS INR IQD IRR ISK JMD JOD JPY KES
    KGS KHR KMF KPW KRW KWD KYD KZT LAK LBP LKR LRD LSL LYD MAD MDL MGA MKD
    MMK MNT MOP MRU MUR MVR MWK MXN MXV MYR MZN NAD NGN NIO NOK NPR NZD OMR
    PAB PEN PGK PHP PKR PLN PYG QAR RON RSD RUB RWF SAR SBD SCR SDG SEK SGD
    SHP SLE SOS SRD SSP STN SVC SYP SZL THB TJS TMT TND TOP TRY TTD TWD TZS
    UAH UGX USD USN UYI UYU UYW UZS VED VES VND VUV WST XAF XAG XAU XCD XDR
    XOF XPD XPF XPT XSU XUA YER ZAR ZMW ZWG
    """.split()
)

# Reviewed exponents for currencies accepted by money-changing product paths.
# There is deliberately no "otherwise 2" branch: a valid ISO identifier is not
# enough evidence to convert, compare or format an amount.
_EXPONENT_ZERO = frozenset(
    "BIF CLP DJF GNF ISK JPY KMF KRW PYG RWF UGX VND VUV XAF XOF XPF".split()
)
_EXPONENT_THREE = frozenset("BHD IQD JOD KWD LYD OMR TND".split())
_EXPONENT_TWO = frozenset(
    """
    AED AFN ALL AMD ANG AOA ARS AUD AWG AZN BAM BBD BDT BGN BMD BND BOB
    BRL BSD BTN BWP BYN BZD CAD CDF CHF CNY COP CRC CVE CZK DKK DOP DZD
    EGP ERN ETB EUR FJD FKP GBP GEL GHS GIP GMD GTQ GYD HKD HNL HTG HUF
    IDR ILS INR IRR JMD KES KGS KHR KPW KYD KZT LAK LBP LKR LRD LSL MAD
    MDL MGA MKD MMK MNT MOP MRU MUR MVR MWK MXN MYR MZN NAD NGN NIO NOK
    NPR NZD PAB PEN PGK PHP PKR PLN QAR RON RSD RUB SAR SBD SCR SDG SEK
    SGD SHP SLE SOS SRD SSP STN SVC SYP SZL THB TJS TMT TOP TRY TTD TWD
    TZS UAH USD UYU UZS VED VES WST YER ZAR ZMW ZWG
    """.split()
)

SUPPORTED_CURRENCY_EXPONENTS: dict[str, int] = {
    **{code: 0 for code in _EXPONENT_ZERO},
    **{code: 2 for code in _EXPONENT_TWO},
    **{code: 3 for code in _EXPONENT_THREE},
}


class UnsupportedCurrencyExponentError(ValueError):
    """Currency has no reviewed minor-unit exponent contract."""


class CurrencyExponentMismatchError(ValueError):
    """A supplied exponent disagrees with the reviewed currency contract."""


class InvalidCurrencyAmountError(ValueError):
    """A direct monetary amount is invalid for its confirmed currency."""


def validated_currency_code(raw: object) -> str | None:
    """Return a normalized three-letter currency code or ``None``.

    Meta and tracker payloads are the authorities for the actual code.  This
    helper validates their evidence; it does not invent a default.
    """

    value = str(raw or "").strip().upper()
    if value not in _ISO_4217_CODES:
        return None
    return value


def currency_exponent(currency: object) -> int:
    """Return the reviewed ISO exponent or fail closed."""

    code = validated_currency_code(currency)
    if code is None or code not in SUPPORTED_CURRENCY_EXPONENTS:
        raise UnsupportedCurrencyExponentError(
            f"currency {code or '<empty>'!r} has no reviewed exponent"
        )
    return SUPPORTED_CURRENCY_EXPONENTS[code]


def require_currency_exponent(currency: object, exponent: object) -> tuple[str, int]:
    """Validate an explicit currency/exponent pair and return canonical values."""

    code = validated_currency_code(currency)
    expected = currency_exponent(code)
    if isinstance(exponent, bool) or not isinstance(exponent, int):
        raise CurrencyExponentMismatchError(f"currency {code} exponent must be explicit")
    supplied = exponent
    if supplied != expected:
        raise CurrencyExponentMismatchError(
            f"currency {code} requires exponent {expected}, got {exponent!r}"
        )
    return code, expected


def currency_quantum(currency: object, exponent: object) -> Decimal:
    """Return ``10^-exponent`` for an explicit reviewed currency pair."""

    _, confirmed_exponent = require_currency_exponent(currency, exponent)
    return Decimal(1).scaleb(-confirmed_exponent)


def require_exact_currency_amount(
    value: object,
    *,
    currency: object,
    exponent: object,
    field: str = "amount",
    allow_zero: bool = True,
) -> Decimal:
    """Validate a direct major-unit amount without rounding it.

    Derived rates such as CPC may legitimately have sub-minor precision and
    must not use this helper. It is for configured CPA, cumulative spend and
    other amounts that claim to be exactly denominated in the currency.
    """

    quantum = currency_quantum(currency, exponent)
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise InvalidCurrencyAmountError(f"{field} is not a decimal") from exc
    if not amount.is_finite() or amount < 0 or (not allow_zero and amount == 0):
        raise InvalidCurrencyAmountError(f"{field} is outside the safe range")
    if amount != amount.quantize(quantum):
        raise InvalidCurrencyAmountError(
            f"{field} has excess precision for {str(currency).strip().upper()}"
        )
    return amount


__all__ = [
    "CurrencyExponentMismatchError",
    "InvalidCurrencyAmountError",
    "SUPPORTED_CURRENCY_EXPONENTS",
    "UnsupportedCurrencyExponentError",
    "currency_exponent",
    "currency_quantum",
    "require_currency_exponent",
    "require_exact_currency_amount",
    "validated_currency_code",
]
