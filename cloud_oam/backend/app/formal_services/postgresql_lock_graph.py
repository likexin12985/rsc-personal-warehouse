"""Fixed PostgreSQL owner-lock graph entrypoints for formal services.

The production API role deliberately lacks ``UPDATE`` on read-only master and
evidence tables, so it cannot issue direct PostgreSQL row-lock clauses against
them.  Migration-owned ``SECURITY DEFINER`` functions expose only the bounded,
deterministic lock graphs needed by formal business transactions.  SQLite has
no equivalent concurrency claim and therefore treats these entrypoints as
no-ops.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session


_PG_LOCK_OPENING_CONTROL_IMPORT = (
    "public.rsc_lock_opening_control_import_0027"
)
_PG_LOCK_OPENING_STOCKTAKE_START_REFERENCE = (
    "public.rsc_lock_opening_stocktake_start_reference_0027"
)
_PG_LOCK_OPENING_STOCKTAKE_TASK_EVIDENCE = (
    "public.rsc_lock_opening_stocktake_task_evidence_0027"
)
_PG_LOCK_INVENTORY_REFERENCE_GRAPH = (
    "public.rsc_lock_inventory_reference_graph_0027"
)
_PG_LOCK_INVENTORY_SERIAL_GRAPH = (
    "public.rsc_lock_inventory_serial_graph_0027"
)
_PG_LOCK_OPENING_TERMINAL_REFERENCE_UNION = (
    "public.rsc_lock_opening_terminal_reference_union_0028"
)
_PG_LOCK_NONOPENING_STOCKTAKE_REVIEW_GRAPH = (
    "public.rsc_lock_nonopening_stocktake_review_graph_0032"
)
_PG_LOCK_NONOPENING_STOCKTAKE_POSTING_GRAPH = (
    "public.rsc_lock_nonopening_stocktake_posting_graph_0035"
)
_PG_LOCK_NONOPENING_STOCKTAKE_CLOSE_GRAPH = (
    "public.rsc_lock_nonopening_stocktake_close_graph_0038"
)
_PG_LOCK_NONOPENING_STOCKTAKE_DIFFERENCE_REPLAY_GRAPH = (
    "public.rsc_lock_nonopening_stocktake_difference_replay_graph_0057"
)
_PG_LOCK_NONOPENING_STOCKTAKE_COUNT_HISTORY_GRAPH = (
    "public.rsc_lock_nonopening_stocktake_count_history_graph_0062"
)
_PG_LOCK_MATERIAL_REQUEST_WORK_ORDER = (
    "public.rsc_lock_material_request_work_order_reference_0042"
)


def lock_opening_control_import(
    db: Session,
    source_system_id: uuid.UUID,
    sync_run_id: uuid.UUID,
) -> None:
    """Lock one sealed control-import graph through its owner boundary."""

    if not _is_postgresql(db):
        return
    db.execute(
        text(
            f"SELECT {_PG_LOCK_OPENING_CONTROL_IMPORT}("
            "CAST(:source_system_id AS uuid), CAST(:sync_run_id AS uuid))"
        ),
        {
            "source_system_id": str(source_system_id),
            "sync_run_id": str(sync_run_id),
        },
    )


def lock_opening_stocktake_start_reference(
    db: Session,
    region_org_id: uuid.UUID,
    owner_org_ids: Sequence[uuid.UUID],
    location_ids: Sequence[uuid.UUID],
    material_ids: Sequence[uuid.UUID],
    effective_at: datetime,
) -> None:
    """Lock the shared reference graph used to start an opening task."""

    if not _is_postgresql(db):
        return
    scope_pairs = _ordered_scope_pairs(owner_org_ids, location_ids)
    db.execute(
        text(
            f"SELECT {_PG_LOCK_OPENING_STOCKTAKE_START_REFERENCE}("
            "CAST(:region_org_id AS uuid), CAST(:owner_org_ids AS uuid[]), "
            "CAST(:location_ids AS uuid[]), CAST(:material_ids AS uuid[]), "
            "CAST(:effective_at AS timestamptz))"
        ),
        {
            "region_org_id": str(region_org_id),
            "owner_org_ids": [str(owner_id) for owner_id, _ in scope_pairs],
            "location_ids": [str(location_id) for _, location_id in scope_pairs],
            "material_ids": _ordered_uuid_strings(material_ids),
            "effective_at": effective_at,
        },
    )


def lock_opening_stocktake_task_evidence(
    db: Session,
    task_id: uuid.UUID,
    round_id: uuid.UUID,
) -> None:
    """Lock task-local, read-only stocktake evidence in owner-defined order."""

    if not _is_postgresql(db):
        return
    db.execute(
        text(
            f"SELECT {_PG_LOCK_OPENING_STOCKTAKE_TASK_EVIDENCE}("
            "CAST(:task_id AS uuid), CAST(:round_id AS uuid))"
        ),
        {"task_id": str(task_id), "round_id": str(round_id)},
    )


def lock_inventory_reference_graph(
    db: Session,
    account_ids: Sequence[uuid.UUID],
    effective_at: datetime,
) -> None:
    """Lock shared account/material reference rows in owner-defined order."""

    if not _is_postgresql(db):
        return
    db.execute(
        text(
            f"SELECT {_PG_LOCK_INVENTORY_REFERENCE_GRAPH}("
            "CAST(:account_ids AS uuid[]), CAST(:effective_at AS timestamptz))"
        ),
        {
            "account_ids": _ordered_uuid_strings(account_ids),
            "effective_at": effective_at,
        },
    )


def lock_inventory_serial_graph(
    db: Session,
    serial_ids: Sequence[uuid.UUID],
) -> None:
    """Lock immutable serial masters before mutable current-position rows."""

    if not _is_postgresql(db):
        return
    db.execute(
        text(
            f"SELECT {_PG_LOCK_INVENTORY_SERIAL_GRAPH}("
            "CAST(:serial_ids AS uuid[]))"
        ),
        {"serial_ids": _ordered_uuid_strings(serial_ids)},
    )


def lock_opening_terminal_reference_union(
    db: Session,
    task_ids: Sequence[uuid.UUID],
    owner_org_ids: Sequence[uuid.UUID],
    location_ids: Sequence[uuid.UUID],
    material_ids: Sequence[uuid.UUID],
    account_ids: Sequence[uuid.UUID],
) -> None:
    """Lock one exact shared-master union for terminal opening tasks."""

    if not _is_postgresql(db):
        return
    scope_pairs = _ordered_scope_pairs(owner_org_ids, location_ids)
    db.execute(
        text(
            f"SELECT {_PG_LOCK_OPENING_TERMINAL_REFERENCE_UNION}("
            "CAST(:task_ids AS uuid[]), CAST(:owner_org_ids AS uuid[]), "
            "CAST(:location_ids AS uuid[]), CAST(:material_ids AS uuid[]), "
            "CAST(:account_ids AS uuid[]))"
        ),
        {
            "task_ids": _ordered_uuid_strings(task_ids),
            "owner_org_ids": [str(owner_id) for owner_id, _ in scope_pairs],
            "location_ids": [str(location_id) for _, location_id in scope_pairs],
            "material_ids": _ordered_uuid_strings(material_ids),
            "account_ids": _ordered_uuid_strings(account_ids),
        },
    )


def lock_nonopening_stocktake_review_graph(
    db: Session,
    task_id: uuid.UUID,
    round_id: uuid.UUID,
) -> None:
    """Lock one non-opening count/difference/review graph in owner order.

    The API role intentionally keeps immutable stocktake evidence SELECT-only.
    Revision 0032 owns this bounded ``SECURITY DEFINER`` entrypoint; SQLite
    provides no row-locking claim and therefore remains a no-op here.
    """

    if not _is_postgresql(db):
        return
    db.execute(
        text(
            f"SELECT {_PG_LOCK_NONOPENING_STOCKTAKE_REVIEW_GRAPH}("
            "CAST(:task_id AS uuid), CAST(:round_id AS uuid))"
        ),
        {"task_id": str(task_id), "round_id": str(round_id)},
    )


def lock_nonopening_stocktake_difference_replay_graph(
    db: Session,
    task_id: uuid.UUID,
    round_id: uuid.UUID,
    actor_user_id: str,
) -> None:
    """Lock the complete submitted initial-difference replay graph.

    Revision 0057 owns the inventory-head-first lock order, including scoped
    and replay endpoint accounts, bounded ledger facts, evidence files and the
    full reference union.  SQLite has no equivalent concurrency claim.
    """

    if not _is_postgresql(db):
        return
    db.execute(
        text(
            f"SELECT {_PG_LOCK_NONOPENING_STOCKTAKE_DIFFERENCE_REPLAY_GRAPH}("
            "CAST(:task_id AS uuid), CAST(:round_id AS uuid), "
            "CAST(:actor_user_id AS text))"
        ),
        {
            "task_id": str(task_id),
            "round_id": str(round_id),
            "actor_user_id": actor_user_id,
        },
    )


def lock_nonopening_stocktake_count_history_graph(
    db: Session,
    task_id: uuid.UUID,
    round_id: uuid.UUID,
    actor_user_id: str,
) -> None:
    """Own a complete multi-round historical count graph before source reads.

    Revision 0062 derives every owner coordinate in the database, including
    terminal evidence and the complete evidence-file union. Authorization and
    historical/audit proof remain the caller's responsibility. SQLite makes
    no claim about PostgreSQL concurrency and is an explicit no-op.
    """

    if not _is_postgresql(db):
        return
    db.execute(
        text(
            f"SELECT {_PG_LOCK_NONOPENING_STOCKTAKE_COUNT_HISTORY_GRAPH}("
            "CAST(:task_id AS uuid), CAST(:round_id AS uuid), "
            "CAST(:actor_user_id AS text))"
        ),
        {
            "task_id": str(task_id),
            "round_id": str(round_id),
            "actor_user_id": actor_user_id,
        },
    )


def lock_nonopening_stocktake_posting_graph(
    db: Session,
    task_id: uuid.UUID,
) -> None:
    """Lock one complete non-opening posting graph in migration-owned order."""

    if not _is_postgresql(db):
        return
    db.execute(
        text(
            f"SELECT {_PG_LOCK_NONOPENING_STOCKTAKE_POSTING_GRAPH}("
            "CAST(:task_id AS uuid))"
        ),
        {"task_id": str(task_id)},
    )


def lock_nonopening_stocktake_close_graph(
    db: Session,
    task_id: uuid.UUID,
) -> None:
    """Lock posting plus repeatable reconciliation/close evidence in order."""

    if not _is_postgresql(db):
        return
    db.execute(
        text(
            f"SELECT {_PG_LOCK_NONOPENING_STOCKTAKE_CLOSE_GRAPH}("
            "CAST(:task_id AS uuid))"
        ),
        {"task_id": str(task_id)},
    )


def lock_material_request_work_order(
    db: Session,
    work_order_id: uuid.UUID,
) -> None:
    """Lock one SELECT-only OAM work-order projection through its owner."""

    if not _is_postgresql(db):
        return
    db.execute(
        text(
            f"SELECT {_PG_LOCK_MATERIAL_REQUEST_WORK_ORDER}("
            "CAST(:work_order_id AS uuid))"
        ),
        {"work_order_id": str(work_order_id)},
    )


def _ordered_uuid_strings(values: Sequence[uuid.UUID]) -> list[str]:
    return [str(value) for value in sorted(set(values), key=str)]


def _ordered_scope_pairs(
    owner_org_ids: Sequence[uuid.UUID],
    location_ids: Sequence[uuid.UUID],
) -> tuple[tuple[uuid.UUID, uuid.UUID], ...]:
    """Keep owner/location coordinates paired while canonicalizing the graph."""

    if len(owner_org_ids) != len(location_ids):
        raise ValueError("owner_org_ids and location_ids must form parallel pairs")
    return tuple(
        sorted(
            set(zip(owner_org_ids, location_ids, strict=True)),
            key=lambda pair: (str(pair[0]), str(pair[1])),
        )
    )


def _is_postgresql(db: Session) -> bool:
    return db.get_bind().dialect.name == "postgresql"


__all__ = [
    "lock_inventory_reference_graph",
    "lock_inventory_serial_graph",
    "lock_material_request_work_order",
    "lock_opening_control_import",
    "lock_opening_stocktake_start_reference",
    "lock_opening_stocktake_task_evidence",
    "lock_opening_terminal_reference_union",
    "lock_nonopening_stocktake_posting_graph",
    "lock_nonopening_stocktake_close_graph",
    "lock_nonopening_stocktake_difference_replay_graph",
    "lock_nonopening_stocktake_count_history_graph",
    "lock_nonopening_stocktake_review_graph",
]
