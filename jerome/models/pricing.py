from dataclasses import dataclass
from decimal import Decimal

from jerome.models.base import Usage


@dataclass(frozen=True)
class Price:
    """Dollars per million tokens."""

    input: Decimal
    output: Decimal
    cache_read: Decimal
    cache_write: Decimal


def _p(i, o, r, w) -> Price:
    return Price(Decimal(i), Decimal(o), Decimal(r), Decimal(w))


DEFAULT_PRICES = {"claude-opus-5-5": _p(4, 20, "0.20", 5)}


def cost(price: Price, usage: Usage) -> Decimal:
    total = (
        usage.input_tokens * price.input
        + usage.output_tokens * price.output
        + usage.cache_read_tokens * price.cache_read
        + usage.cache_write_tokens * price.cache_write
    )
    return Decimal(total) / Decimal(1_000_000)
