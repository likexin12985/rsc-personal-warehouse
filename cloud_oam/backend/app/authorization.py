from __future__ import annotations


FORMAL_ROLE_CODES = frozenset(
    {
        "admin",
        "provincial_manager",
        "technician",
        "star_headquarters_approver",
    }
)
RETIRED_ROLE_CODES = frozenset({"auditor", "warehouse_manager"})


def is_formal_role(role: str) -> bool:
    return role in FORMAL_ROLE_CODES
