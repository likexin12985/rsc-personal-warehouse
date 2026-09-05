"""Minimal non-opening historical facts; never a permission to replay a write."""

from typing import Literal

from .opening_count_command_status_schemas import (
    OpeningCountCommandStatusOut,
    OpeningCountHistoricalCommandOut,
)


# Reuse only the strict, non-sensitive wire primitives, not opening services,
# authorization, state machines or OAM control-total semantics.
class StocktakeCountHistoricalCommandOut(OpeningCountHistoricalCommandOut):
    pass


class StocktakeCountCommandStatusOut(OpeningCountCommandStatusOut):
    operation: Literal["initial_count", "recount_count"]
    command: StocktakeCountHistoricalCommandOut | None
