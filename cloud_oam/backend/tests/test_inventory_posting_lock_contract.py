from __future__ import annotations

import inspect
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.dialects import postgresql

import app.formal_services.inventory_posting as posting_service
from app.inventory_models import StockAccount


class _DialectSession:
    def __init__(self, dialect_name: str) -> None:
        self._bind = SimpleNamespace(
            dialect=SimpleNamespace(name=dialect_name)
        )

    def get_bind(self):
        return self._bind


def _postgresql_sql(statement) -> str:
    return str(statement.compile(dialect=postgresql.dialect()))


def test_transaction_bound_proofs_never_use_recyclable_object_addresses() -> None:
    service_directory = Path(posting_service.__file__).resolve().parent
    forbidden_fragments = (
        "session_identity",
        "transaction_identity",
        "id(db)",
        "id(transaction)",
    )

    for path in sorted(service_directory.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        for fragment in forbidden_fragments:
            assert fragment not in source, (
                f"{path.name} must bind transaction proofs by strong object "
                f"reference, not {fragment!r}"
            )


def test_select_only_reference_rows_use_owner_lock_only_on_postgresql() -> None:
    base_statement = select(StockAccount).order_by(StockAccount.id)

    production_statement = posting_service._select_only_reference_statement(
        _DialectSession("postgresql"),
        base_statement,
    )
    local_statement = posting_service._select_only_reference_statement(
        _DialectSession("sqlite"),
        base_statement,
    )

    assert "FOR UPDATE" not in _postgresql_sql(production_statement)
    assert "FOR UPDATE" in _postgresql_sql(local_statement)


def test_posting_lock_graph_preserves_the_global_order() -> None:
    source = inspect.getsource(posting_service._post_new_transaction)
    ordered_markers = (
        "select(InventoryLedgerHead)",
        "lock_inventory_reference_graph",
        "_authorize_account_ids",
        "_require_active_account_masters",
        "_load_effective_policies",
        "_lock_and_validate_serials",
        "_lock_or_create_balances",
        "append_audit_event",
    )

    offsets = [source.index(marker) for marker in ordered_markers]
    assert offsets == sorted(offsets)

    serial_source = inspect.getsource(
        posting_service._lock_and_validate_serials
    )
    serial_offsets = [
        serial_source.index(marker)
        for marker in (
            "lock_inventory_serial_graph",
            "select(InventorySerial)",
            "select(SerialCurrentPosition)",
        )
    ]
    assert serial_offsets == sorted(serial_offsets)


def test_opening_scope_read_depends_on_locked_task_and_immutable_evidence() -> None:
    source = inspect.getsource(
        posting_service._require_active_opening_task_scopes
    )

    task_query = source.index("select(FormalStocktakeTask)")
    scope_adapter = source.index("_select_only_reference_statement")
    scope_query = source.index("select(FormalStocktakeScope)")
    freeze_query = source.index("select(InventoryFreeze)")

    assert task_query < scope_adapter < scope_query < freeze_query
    assert source.count(".with_for_update()") == 2
    assert "immutable task evidence" in source


def test_terminal_opening_planner_locks_one_principal_union_before_evidence() -> None:
    source = inspect.getsource(
        posting_service._plan_and_lock_terminal_opening_graphs
    )
    ordered_markers = (
        ".with_for_update(of=FormalStocktakeTask)",
        "_lock_opening_task_principal_graph",
        "lock_opening_stocktake_task_evidence",
        "lock_inventory_reference_graph",
        "lock_inventory_serial_graph",
    )
    offsets = [source.index(marker) for marker in ordered_markers]

    assert offsets == sorted(offsets)
    assert source.count("_lock_opening_task_principal_graph") == 1
    principal_source = inspect.getsource(
        posting_service._lock_opening_task_principal_graph
    )
    assert principal_source.count("lock_formal_principal_graph") == 1
    assert "_opening_task_principal_user_ids_batch" in principal_source
    batch_user_source = inspect.getsource(
        posting_service._opening_task_principal_user_ids_batch
    )
    assert batch_user_source.count("union_all") == 1
    assert batch_user_source.count("db.scalars(") == 1
    pure_source = inspect.getsource(
        posting_service._validate_prelocked_opening_task_principal_graph
    )
    assert "lock_formal_principal_graph" not in pure_source
    assert ".with_for_update" not in pure_source


def test_public_opening_inventory_replay_uses_the_sealed_batch_boundary() -> None:
    source = inspect.getsource(
        posting_service.validate_opening_task_evidence_for_replay
    )

    ordered_markers = (
        "_lock_opening_terminal_task_batch_root",
        "_lock_opening_terminal_task_batch_graph",
        "lock_audit_chain_head",
        "_validate_opening_inventory_batch_from_prelocked_graph",
    )
    offsets = [source.index(marker) for marker in ordered_markers]
    assert offsets == sorted(offsets)
    assert "_validate_opening_task_evidence(" not in source
