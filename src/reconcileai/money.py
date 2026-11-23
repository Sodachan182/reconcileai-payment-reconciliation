"""Exact monetary parsing and formatting.

All monetary values in ReconcileAI are :class:`decimal.Decimal`. Binary floats are
never used for stored amounts or equality checks. Values read from spreadsheets as
floats are converted through their shortest ``repr`` and rejected if they carry more
fractional digits than the currency allows — nothing is silently rounded.
"""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

CURRENCY_PRECISION: dict[str, int] = {"VND": 0, "USD": 2, "EUR": 2}
SUPPORTED_CURRENCIES = frozenset(CURRENCY_PRECISION)

_CURRENCY_TOKENS = re.compile(r"(vnd|vnđ|đ|usd|eur|\$|€|₫)", re.IGNORECASE)


class AmountError(ValueError):
    """Raised when a value cannot be interpreted as an exact monetary amount."""


def precision(currency: str) -> int:
    try:
        return CURRENCY_PRECISION[currency.upper()]
    except KeyError as exc:
        raise AmountError(f"Unsupported currency '{currency}'") from exc


def quantum(currency: str) -> Decimal:
    return Decimal(1).scaleb(-precision(currency))


def _from_string(text: str, currency: str) -> Decimal:
    s = _CURRENCY_TOKENS.sub("", text).strip()
    s = s.replace(" ", " ").replace("'", "")
    negative = False
    if s.startswith("(") and s.endswith(")"):
        negative, s = True, s[1:-1].strip()
    if s.startswith("-"):
        negative, s = True, s[1:].strip()
    elif s.startswith("+"):
        s = s[1:].strip()
    s = s.replace(" ", "")
    if not s:
        raise AmountError("empty amount")
    if not re.fullmatch(r"[0-9.,]+", s):
        raise AmountError(f"'{text}' is not a number")

    digits = precision(currency)
    dots, commas = s.count("."), s.count(",")
    if dots and commas:
        # The right-most separator is the decimal separator.
        dec_sep = "." if s.rfind(".") > s.rfind(",") else ","
        thou_sep = "," if dec_sep == "." else "."
        if s.count(dec_sep) > 1:
            raise AmountError(f"'{text}' has conflicting separators")
        int_part, frac_part = s.split(dec_sep)
        int_part = _strip_thousands(int_part, thou_sep, text)
    elif dots or commas:
        sep = "." if dots else ","
        parts = s.split(sep)
        if len(parts) > 2:
            int_part, frac_part = _strip_thousands(s, sep, text), ""
        elif len(parts[1]) == 3 and parts[0] not in ("", "0"):
            if digits:
                # "1.250" could be 1,250 or 1.25 in a currency with cents: never guess.
                raise AmountError(f"'{text}' is ambiguous (thousands or decimal separator?); "
                                  f"write it as e.g. 1250.00 or 1,250.00")
            # VND has no minor unit, so "1.250" / "1,250" can only be thousands grouping.
            int_part, frac_part = parts[0] + parts[1], ""
        elif digits == 0 and parts[1].strip("0"):
            raise AmountError(
                f"'{text}' has a fractional part but {currency} has no minor unit"
            )
        else:
            int_part, frac_part = parts
    else:
        int_part, frac_part = s, ""

    if not int_part:
        int_part = "0"
    if len(frac_part) > digits and frac_part[digits:].strip("0"):
        raise AmountError(
            f"'{text}' has {len(frac_part)} decimals; {currency} allows {digits}"
        )
    value = Decimal(f"{int_part}.{frac_part or '0'}")
    return -value if negative else value


def _strip_thousands(part: str, sep: str, original: str) -> str:
    groups = part.split(sep)
    if not groups[0] or len(groups[0]) > 3 or any(len(g) != 3 for g in groups[1:]):
        raise AmountError(f"'{original}' has irregular thousands grouping")
    return "".join(groups)


def parse_amount(value: object, currency: str = "VND") -> Decimal:
    """Parse ``value`` into an exact Decimal quantized to the currency's minor unit.

    Accepts ints, Decimals, floats (via ``repr``) and strings such as ``"1.250.000"``,
    ``"1,250,000"``, ``"1 250 000 đ"`` or ``"1,250.50"`` (USD). Raises
    :class:`AmountError` rather than rounding.
    """
    if value is None:
        raise AmountError("empty amount")
    if isinstance(value, bool):
        raise AmountError("boolean is not an amount")
    try:
        if isinstance(value, Decimal):
            dec = value
        elif isinstance(value, int):
            dec = Decimal(value)
        elif isinstance(value, float):
            if value != value or value in (float("inf"), float("-inf")):
                raise AmountError("not a finite number")
            dec = Decimal(repr(value))
        else:
            text = str(value).strip()
            if not text or text.lower() in {"nan", "none", "null"}:
                raise AmountError("empty amount")
            dec = _from_string(text, currency)
    except InvalidOperation as exc:
        raise AmountError(f"'{value}' is not a number") from exc

    q = quantum(currency)
    quantized = dec.quantize(q, rounding=ROUND_HALF_UP)
    if quantized != dec:
        raise AmountError(
            f"{value} has more precision than {currency} allows ({precision(currency)} decimals)"
        )
    return quantized


def is_blank(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and value != value:
        return True
    return isinstance(value, str) and not value.strip()


def format_amount(value: Decimal | None, currency: str = "VND", *, signed: bool = False) -> str:
    """Format with thousands separators and the currency's fixed decimals."""
    if value is None:
        return ""
    q = quantum(currency)
    v = Decimal(value).quantize(q, rounding=ROUND_HALF_UP)
    text = f"{abs(v):,.{precision(currency)}f}"
    if v < 0:
        return "−" + text
    if signed and v > 0:
        return "+" + text
    return text


def to_minor_units(value: Decimal, currency: str) -> int:
    """Exact integer minor units (e.g. cents). Raises if not representable."""
    scaled = value.scaleb(precision(currency))
    if scaled != scaled.to_integral_value():
        raise AmountError(f"{value} not representable in {currency} minor units")
    return int(scaled)


def from_minor_units(units: int, currency: str) -> Decimal:
    return Decimal(units).scaleb(-precision(currency)).quantize(quantum(currency))


def dsum(values) -> Decimal:
    """Exact sum of Decimals (``sum`` with a Decimal start value)."""
    total = Decimal(0)
    for v in values:
        total += v
    return total
