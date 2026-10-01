"""Money and Currency Domain Utility for Jordanian Dinar (JOD).

1 JOD = 1000 fils (minor units). All calculations are performed using integer fils.
"""
from decimal import Decimal, ROUND_HALF_UP


def jod_to_fils(amount: float | str | Decimal) -> int:
    """Convert JOD decimal amount to integer fils (minor units)."""
    d = Decimal(str(amount))
    return int((d * Decimal(1000)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def fils_to_jod(fils: int) -> Decimal:
    """Convert integer fils to JOD Decimal with 3 decimal places."""
    return (Decimal(fils) / Decimal(1000)).quantize(Decimal("0.001"))


def format_jod(fils: int, locale: str = "ar") -> str:
    """Format integer fils as customer-facing JOD string."""
    jod = fils_to_jod(fils)
    # 3.450 -> 3.45
    formatted = f"{jod:.3f}".rstrip("0").rstrip(".")
    if locale == "ar":
        return f"{formatted} د.أ"
    return f"{formatted} JOD"
