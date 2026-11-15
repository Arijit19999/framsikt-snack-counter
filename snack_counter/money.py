"""Exact money handling.

Amounts are stored as integer minor units (øre / paise), never floats, and
every arithmetic operation checks that both sides use the same currency.
Mixing NOK and INR by accident is the most likely bug in this domain, so it
fails loudly instead of producing a wrong total.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

# Minor units per major unit for every currency we support.
MINOR_UNITS: dict[str, int] = {"NOK": 100, "INR": 100}


class CurrencyMismatchError(ValueError):
    """Raised when two amounts in different currencies are combined."""


@dataclass(frozen=True)
class Money:
    minor: int
    currency: str

    def __post_init__(self) -> None:
        if self.currency not in MINOR_UNITS:
            raise ValueError(f"Unsupported currency: {self.currency}")
        if not isinstance(self.minor, int):
            raise TypeError("Money.minor must be an int (minor units)")

    # ---- construction -------------------------------------------------
    @staticmethod
    def of(amount: str | int | Decimal, currency: str) -> "Money":
        """Money.of("1199.50", "NOK"). Floats are refused on purpose."""
        if isinstance(amount, float):
            raise TypeError("Use str or Decimal for money, never float")
        if currency not in MINOR_UNITS:
            raise ValueError(f"Unsupported currency: {currency}")
        minor = Decimal(str(amount)) * MINOR_UNITS[currency]
        if minor != minor.to_integral_value():
            raise ValueError(f"{amount} has more decimals than {currency} allows")
        return Money(int(minor), currency)

    @staticmethod
    def zero(currency: str) -> "Money":
        return Money(0, currency)

    # ---- arithmetic -----------------------------------------------------
    def _same(self, other: "Money") -> None:
        if not isinstance(other, Money):
            raise TypeError(f"Expected Money, got {type(other).__name__}")
        if other.currency != self.currency:
            raise CurrencyMismatchError(
                f"Cannot combine {self.currency} with {other.currency}"
            )

    def __add__(self, other: "Money") -> "Money":
        self._same(other)
        return Money(self.minor + other.minor, self.currency)

    def __sub__(self, other: "Money") -> "Money":
        self._same(other)
        return Money(self.minor - other.minor, self.currency)

    def __lt__(self, other: "Money") -> bool:
        self._same(other)
        return self.minor < other.minor

    def __le__(self, other: "Money") -> bool:
        self._same(other)
        return self.minor <= other.minor

    def __gt__(self, other: "Money") -> bool:
        self._same(other)
        return self.minor > other.minor

    def __ge__(self, other: "Money") -> bool:
        self._same(other)
        return self.minor >= other.minor

    def min(self, other: "Money") -> "Money":
        return self if self <= other else other

    @property
    def is_positive(self) -> bool:
        return self.minor > 0

    @property
    def amount(self) -> Decimal:
        """Major-unit amount as an exact Decimal, e.g. Decimal('1199.50')."""
        return Decimal(self.minor) / MINOR_UNITS[self.currency]

    def convert(self, rate: Decimal, to_currency: str) -> "Money":
        """Convert with `rate` = units of `to_currency` per 1 unit of self.

        Rounded half-up to the target's minor unit, once per conversion.
        """
        if not isinstance(rate, Decimal):
            raise TypeError("FX rate must be a Decimal")
        target = (
            Decimal(self.minor)
            * rate
            * MINOR_UNITS[to_currency]
            / MINOR_UNITS[self.currency]
        )
        return Money(int(target.quantize(Decimal(1), rounding=ROUND_HALF_UP)), to_currency)

    def __str__(self) -> str:
        return f"{self.amount:,.2f} {self.currency}"
