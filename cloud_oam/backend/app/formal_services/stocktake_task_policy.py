"""Pure state and visibility policy for formal non-opening stocktakes."""

from __future__ import annotations

import re
from typing import Final


class StocktakeTaskPolicyError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


TASK_TYPES: Final[frozenset[str]] = frozenset(
    {"full", "sample", "ad_hoc", "personal", "termination"}
)
_FIXED_QUANTITY_RE: Final[re.Pattern[str]] = re.compile(
    r"^-?(?:0|[1-9]\d*)\.\d{3}$"
)

_TRANSITIONS: Final[dict[str, frozenset[str]]] = {
    "draft": frozenset({"issued", "cancelled"}),
    "issued": frozenset({"frozen", "cancelled"}),
    "frozen": frozenset({"counting", "cancelled"}),
    "counting": frozenset({"submitted", "cancelled"}),
    "submitted": frozenset({"region_review"}),
    "region_review": frozenset({"hq_review", "recount_required", "cancelled"}),
    "hq_review": frozenset({"approved", "recount_required", "cancelled"}),
    "recount_required": frozenset({"counting", "cancelled"}),
    "approved": frozenset({"posted"}),
    "posted": frozenset({"closed"}),
    "closed": frozenset(),
    "cancelled": frozenset(),
}


def require_stocktake_task_type(task_type: str) -> str:
    if task_type not in TASK_TYPES:
        raise StocktakeTaskPolicyError(
            "stocktake_task_type_invalid",
            "非期初盘点类型无效",
        )
    return task_type


def require_stocktake_transition(from_status: str, to_status: str) -> None:
    if to_status not in _TRANSITIONS.get(from_status, frozenset()):
        raise StocktakeTaskPolicyError(
            "stocktake_task_transition_invalid",
            f"盘点任务不允许从 {from_status} 进入 {to_status}",
        )


def require_atomic_start_path(from_status: str, states: tuple[str, ...]) -> None:
    """A start command may record issued/frozen/counting in one transaction."""

    current = from_status
    for target in states:
        require_stocktake_transition(current, target)
        current = target
    if states != ("issued", "frozen", "counting"):
        raise StocktakeTaskPolicyError(
            "stocktake_task_start_path_invalid",
            "盘点启动必须完整记录已下发、已冻结、盘点中三个状态",
        )


def may_show_book_quantity(
    *,
    blind_count: bool,
    round_status: str | None,
    difference_set_sealed: bool,
) -> bool:
    """Blind counts stay quantity-blind until the round and differences seal."""

    if not blind_count:
        return True
    return round_status in {"submitted", "superseded"} and difference_set_sealed


def difference_posting_movement(difference_qty: str) -> str | None:
    """Map an exact fixed-point difference to a posting class, never a balance overwrite."""

    if not isinstance(difference_qty, str) or not _FIXED_QUANTITY_RE.fullmatch(
        difference_qty
    ):
        raise StocktakeTaskPolicyError(
            "stocktake_difference_quantity_invalid",
            "盘点差异数量必须是三位小数定点字符串",
        )
    whole, fraction = difference_qty.removeprefix("-").split(".")
    units = int(whole) * 1000 + int(fraction)
    if difference_qty.startswith("-"):
        units = -units
    if units > 0:
        return "stocktake_gain"
    if units < 0:
        return "stocktake_loss"
    return None


__all__ = [
    "StocktakeTaskPolicyError",
    "TASK_TYPES",
    "difference_posting_movement",
    "may_show_book_quantity",
    "require_atomic_start_path",
    "require_stocktake_task_type",
    "require_stocktake_transition",
]
