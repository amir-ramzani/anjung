"""Money in integer minor units. No floats anywhere (LLD section 3.4)."""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum


class Currency(StrEnum):
    MYR = "MYR"


@dataclass(frozen=True, slots=True)
class Money:
    amount_minor: int
    currency: Currency = Currency.MYR

    def __post_init__(self) -> None:
        if isinstance(self.amount_minor, bool) or not isinstance(self.amount_minor, int):
            raise TypeError("amount_minor must be an int")

    def _check(self, other: "Money") -> None:
        if self.currency != other.currency:
            raise ValueError("currency mismatch")

    def __add__(self, other: "Money") -> "Money":
        self._check(other)
        return Money(self.amount_minor + other.amount_minor, self.currency)

    def __sub__(self, other: "Money") -> "Money":
        self._check(other)
        return Money(self.amount_minor - other.amount_minor, self.currency)

    def __neg__(self) -> "Money":
        return Money(-self.amount_minor, self.currency)

    def times_ratio(self, num: int, den: int) -> "Money":
        """self * num / den, rounded half away from zero, integer arithmetic only."""
        if den <= 0 or num < 0:
            raise ValueError("ratio needs den > 0 and num >= 0")
        sign = -1 if self.amount_minor < 0 else 1
        q = (abs(self.amount_minor) * num * 2 + den) // (den * 2)
        return Money(sign * q, self.currency)

    def format(self) -> str:
        sign = "-" if self.amount_minor < 0 else ""
        whole, frac = divmod(abs(self.amount_minor), 100)
        return f"{sign}{self.currency.value} {whole:,}.{frac:02d}"


def prorate(rent: Money, days_used: int, days_in_month: int) -> Money:
    return rent.times_ratio(days_used, days_in_month)


def allocate(total: Money, weights: Sequence[int]) -> list[Money]:
    """Split `total` by integer weights (largest remainder). The parts always sum to `total`."""
    if not weights or any(w < 0 for w in weights) or sum(weights) == 0:
        raise ValueError("weights must be non-negative with a positive sum")
    wsum = sum(weights)
    sign = -1 if total.amount_minor < 0 else 1
    amt = abs(total.amount_minor)
    base = [amt * w // wsum for w in weights]
    rem = amt - sum(base)
    order = sorted(range(len(weights)), key=lambda i: (-(amt * weights[i] % wsum), i))
    for i in order[:rem]:
        base[i] += 1
    return [Money(sign * b, total.currency) for b in base]
