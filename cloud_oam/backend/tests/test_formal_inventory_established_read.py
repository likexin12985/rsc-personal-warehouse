from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

import app.formal_services.inventory_query as inventory_query_service
from app.formal_access import Entitlement, FormalPrincipal, ScopeGrant
from app.foundation_models import OutboxEvent
from app.formal_services.inventory_query import (
    InventoryReadError,
    inventory_summary,
    inventory_transaction_detail,
    list_inventory_accounts,
    personal_warehouse,
)
from app.inventory_models import (
    CustodyAssignment,
    InventoryLedgerHead,
    InventoryMovement,
    InventoryMovementSerial,
    InventorySerial,
    InventoryTransaction,
    SerialCurrentPosition,
    StockAccount,
    StockBalance,
    StockLocation,
)
from app.stocktake_models import InventoryOpeningEstablishment

import test_inventory_posting as posting_fixtures


@pytest.fixture()
def posting_db() -> Session:
    fixture = posting_fixtures.db.__wrapped__()
    session = next(fixture)
    try:
        yield session
    finally:
        try:
            next(fixture)
        except StopIteration:
            pass


@pytest.fixture()
def posting_world(
    posting_db: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    return posting_fixtures.world.__wrapped__(posting_db, monkeypatch)


def _reader(
    world,
    *,
    role_code: str = "provincial_manager",
    scope_type: str = "organization",
    scope_id: str | None = None,
) -> FormalPrincipal:
    assignment_id = (
        world.regional_assignment.id
        if role_code == "provincial_manager"
        else uuid.uuid4()
    )
    resolved_scope_id = scope_id or (
        str(world.person.id)
        if scope_type == "person"
        else str(world.organization.id)
    )
    grant = ScopeGrant(
        assignment_id=assignment_id,
        role_code=role_code,
        scope_type=scope_type,
        scope_id=resolved_scope_id,
        valid_from=posting_fixtures.NOW - timedelta(days=1),
        valid_to=None,
    )
    return FormalPrincipal(
        user_id=world.user.id,
        person_id=world.person.id,
        account_status="active",
        employment_status="active",
        authorization_version=world.user.authorization_version,
        access_mode="active",
        assignments=(grant,),
        entitlements=(
            Entitlement(
                assignment_id=assignment_id,
                role_code=role_code,
                scope_type=scope_type,
                scope_id=resolved_scope_id,
                resource="inventory",
                action="read",
                field_code="",
                effect="allow",
            ),
        ),
    )


def _make_personal_account(db: Session, world, *, established: bool = True):
    parent = StockLocation(
        id=uuid.uuid4(),
        code=f"REGION-{uuid.uuid4().hex[:10]}",
        name="测试区域仓",
        location_type="region",
        owner_org_id=world.organization.id,
        parent_id=None,
        custodian_person_id=None,
        status="active",
        created_at=posting_fixtures.NOW - timedelta(days=2),
        updated_at=posting_fixtures.NOW - timedelta(days=2),
    )
    location = StockLocation(
        id=uuid.uuid4(),
        code=f"PERSONAL-{uuid.uuid4().hex[:10]}",
        name="库存工程师个人仓",
        location_type="personal",
        owner_org_id=world.organization.id,
        parent_id=parent.id,
        custodian_person_id=world.person.id,
        status="active",
        created_at=posting_fixtures.NOW - timedelta(days=2),
        updated_at=posting_fixtures.NOW - timedelta(days=2),
    )
    db.add(parent)
    db.flush()
    db.add(location)
    db.flush()
    account = StockAccount(
        id=uuid.uuid4(),
        owner_org_id=world.organization.id,
        custodian_person_id=world.person.id,
        location_id=location.id,
        material_id=world.material.id,
        condition_code="new",
        availability_bucket="available",
        lot_id=None,
        created_at=posting_fixtures.NOW - timedelta(days=1),
        updated_at=posting_fixtures.NOW - timedelta(days=1),
    )
    db.add(account)
    db.flush()
    db.add(
        CustodyAssignment(
            id=uuid.uuid4(),
            location_id=location.id,
            custodian_person_id=world.person.id,
            valid_from=posting_fixtures.NOW - timedelta(days=1),
            valid_to=None,
        )
    )
    db.flush()
    facts = None
    if established:
        facts = posting_fixtures.establish_account_for_posting(db, world, account)
        db.info["opening_facts_by_account"][account.id] = facts
    return account, location, facts


def test_complete_opening_reveals_exact_balance_and_transaction(
    posting_db: Session,
    posting_world,
):
    account = posting_fixtures.make_account(
        posting_db,
        organization=posting_world.organization,
        material=posting_world.material,
    )
    facts = posting_db.info["opening_facts_by_account"][account.id]
    positive = posting_fixtures.make_positive_opening_facts(
        posting_db,
        facts,
        quantity=Decimal("12.345"),
    )
    posting_db.commit()
    actor = _reader(posting_world)

    summary = inventory_summary(posting_db, actor=actor)
    assert summary.opening_balance_status == "established"
    assert summary.quantity_status == "material_filter_required"
    assert summary.available_qty is None
    assert summary.ledger_cursor == positive.transaction.ledger_cursor

    page = list_inventory_accounts(posting_db, actor=actor, limit=100)
    item = next(row for row in page.items if row.stock_account_id == account.id)
    assert page.opening_balance_status == "established"
    assert item.quantity_status == "available"
    assert item.quantity == "12.345"
    assert item.ledger_cursor == positive.transaction.ledger_cursor

    detail = inventory_transaction_detail(
        posting_db,
        actor=actor,
        transaction_id=positive.transaction.id,
    )
    assert detail.ledger_cursor == positive.transaction.ledger_cursor
    assert detail.movements[0].to_account_id == account.id
    assert detail.movements[0].quantity == "12.345"


def test_missing_opening_keeps_every_quantity_hidden(
    posting_db: Session,
    posting_world,
):
    account = posting_fixtures.make_account(
        posting_db,
        organization=posting_world.organization,
        material=posting_world.material,
        established=False,
    )
    posting_db.commit()
    actor = _reader(posting_world)

    summary = inventory_summary(posting_db, actor=actor)
    page = list_inventory_accounts(posting_db, actor=actor, limit=100)
    item = next(row for row in page.items if row.stock_account_id == account.id)
    assert summary.opening_balance_status == "not_established"
    assert summary.quantity_status == "opening_not_established"
    assert item.quantity_status == "opening_not_established"
    assert item.quantity is None


def test_tampered_opening_graph_fails_closed_instead_of_hiding_as_unopened(
    posting_db: Session,
    posting_world,
):
    account = posting_fixtures.make_account(
        posting_db,
        organization=posting_world.organization,
        material=posting_world.material,
    )
    facts = posting_db.info["opening_facts_by_account"][account.id]
    posting_db.commit()
    assert inventory_summary(
        posting_db,
        actor=_reader(posting_world),
    ).opening_balance_status == "established"

    facts.establishment.count_manifest_sha256 = "f" * 64
    posting_db.flush()
    with pytest.raises(InventoryReadError) as captured:
        inventory_summary(posting_db, actor=_reader(posting_world))
    assert captured.value.code == "inventory_opening_establishment_invalid"
    assert captured.value.status_code == 503


def test_tampered_opening_terminal_outbox_fails_strong_read_replay(
    posting_db: Session,
    posting_world,
):
    account = posting_fixtures.make_account(
        posting_db,
        organization=posting_world.organization,
        material=posting_world.material,
    )
    facts = posting_db.info["opening_facts_by_account"][account.id]
    posting_db.commit()
    actor = _reader(posting_world)
    assert inventory_summary(
        posting_db,
        actor=actor,
    ).opening_balance_status == "established"

    terminal_outbox = posting_db.scalar(
        select(OutboxEvent).where(
            OutboxEvent.aggregate_type == "stocktake_task",
            OutboxEvent.aggregate_id == str(facts.task.id),
            OutboxEvent.event_type == "stocktake.opening.posted",
        )
    )
    assert terminal_outbox is not None
    terminal_outbox.payload_jsonb = {
        **terminal_outbox.payload_jsonb,
        "ledger_cursor": -1,
    }
    posting_db.flush()

    with pytest.raises(InventoryReadError) as captured:
        inventory_summary(posting_db, actor=actor)
    assert captured.value.code == "inventory_opening_establishment_invalid"
    assert captured.value.status_code == 503


def test_mixed_scope_hides_every_quantity_until_all_visible_pairs_are_established(
    posting_db: Session,
    posting_world,
):
    established_account = posting_fixtures.make_account(
        posting_db,
        organization=posting_world.organization,
        material=posting_world.material,
    )
    unopened_account = posting_fixtures.make_account(
        posting_db,
        organization=posting_world.organization,
        material=posting_world.material,
        established=False,
    )
    posting_db.commit()
    actor = _reader(posting_world)

    summary = inventory_summary(posting_db, actor=actor)
    page = list_inventory_accounts(posting_db, actor=actor, limit=100)
    narrow_page = list_inventory_accounts(posting_db, actor=actor, limit=1)
    by_id = {row.stock_account_id: row for row in page.items}
    assert summary.opening_balance_status == "not_established"
    assert page.opening_balance_status == "not_established"
    assert narrow_page.opening_balance_status == "not_established"
    assert len(narrow_page.items) == 1
    assert narrow_page.items[0].quantity_status == "opening_not_established"
    assert narrow_page.items[0].quantity is None
    assert by_id[established_account.id].quantity_status == "opening_not_established"
    assert by_id[established_account.id].quantity is None
    assert by_id[unopened_account.id].quantity_status == "opening_not_established"
    assert by_id[unopened_account.id].quantity is None


def test_zero_opening_is_a_real_established_zero_not_an_implicit_default(
    posting_db: Session,
    posting_world,
):
    account = posting_fixtures.make_account(
        posting_db,
        organization=posting_world.organization,
        material=posting_world.material,
    )
    posting_db.commit()
    actor = _reader(posting_world)

    summary = inventory_summary(posting_db, actor=actor)
    page = list_inventory_accounts(posting_db, actor=actor, limit=100)
    item = next(row for row in page.items if row.stock_account_id == account.id)
    assert summary.ledger_cursor == 0
    assert summary.projected_at is None
    assert summary.opening_balance_status == "established"
    assert item.quantity_status == "available"
    assert item.quantity == "0.000"
    assert item.ledger_cursor == 0


def test_personal_warehouse_uses_the_same_full_evidence_gate(
    posting_db: Session,
    posting_world,
):
    account, location, facts = _make_personal_account(posting_db, posting_world)
    assert facts is not None
    posting_fixtures.make_positive_opening_facts(
        posting_db,
        facts,
        quantity=Decimal("3.000"),
    )
    posting_db.commit()
    actor = _reader(
        posting_world,
        role_code="technician",
        scope_type="person",
        scope_id=str(posting_world.person.id),
    )

    warehouse = personal_warehouse(posting_db, actor=actor)
    assert warehouse.location_id == location.id
    assert warehouse.opening_balance_status == "established"
    assert [(row.stock_account_id, row.quantity) for row in warehouse.items] == [
        (account.id, "3.000")
    ]

    facts.establishment.scope_manifest_sha256 = "e" * 64
    posting_db.flush()
    with pytest.raises(InventoryReadError) as captured:
        personal_warehouse(posting_db, actor=actor)
    assert captured.value.code == "inventory_opening_establishment_invalid"


def test_personal_warehouse_without_opening_returns_metadata_but_no_items(
    posting_db: Session,
    posting_world,
):
    _, location, facts = _make_personal_account(
        posting_db,
        posting_world,
        established=False,
    )
    assert facts is None
    posting_db.commit()
    actor = _reader(
        posting_world,
        role_code="technician",
        scope_type="person",
        scope_id=str(posting_world.person.id),
    )

    warehouse = personal_warehouse(posting_db, actor=actor)
    assert warehouse.location_id == location.id
    assert warehouse.opening_balance_status == "not_established"
    assert warehouse.items == []


def test_one_multi_scope_task_is_replayed_only_once_per_read(
    posting_db: Session,
    posting_world,
    monkeypatch: pytest.MonkeyPatch,
):
    first = posting_fixtures.make_account(
        posting_db,
        organization=posting_world.organization,
        material=posting_world.material,
    )
    facts = posting_db.info["opening_facts_by_account"][first.id]
    second = posting_fixtures.make_account(
        posting_db,
        organization=posting_world.organization,
        material=posting_world.material,
        established=False,
    )
    posting_fixtures.add_zero_scope_to_opening_task(posting_db, facts, second)
    posting_db.commit()

    finalize = inventory_query_service.finalize_service
    original_root = finalize._lock_opening_terminal_task_batch_root
    original_graph = finalize._lock_opening_terminal_task_batch_graph
    original_pure = finalize._validate_opening_terminal_batch_from_prelocked_graph
    calls: list[tuple[str, tuple[uuid.UUID, ...]]] = []

    def root_once(db: Session, *, task_ids):
        checked = tuple(task_ids)
        calls.append(("root", checked))
        return original_root(db, task_ids=checked)

    def graph_once(db: Session, *, root, supplied_user_ids=()):
        calls.append(("graph", tuple(row.id for row in root.tasks)))
        return original_graph(
            db,
            root=root,
            supplied_user_ids=supplied_user_ids,
        )

    def pure_once(db: Session, *, proof, **kwargs):
        calls.append(("pure", tuple(row.root.task.id for row in proof.task_graphs)))
        return original_pure(db, proof=proof, **kwargs)

    monkeypatch.setattr(finalize, "_lock_opening_terminal_task_batch_root", root_once)
    monkeypatch.setattr(finalize, "_lock_opening_terminal_task_batch_graph", graph_once)
    monkeypatch.setattr(
        finalize,
        "_validate_opening_terminal_batch_from_prelocked_graph",
        pure_once,
    )

    page = list_inventory_accounts(
        posting_db,
        actor=_reader(posting_world),
        limit=100,
    )
    assert page.opening_balance_status == "established"
    assert {row.quantity for row in page.items} == {"0.000"}
    assert calls == [
        ("root", (facts.task.id,)),
        ("graph", (facts.task.id,)),
        ("pure", (facts.task.id,)),
    ]


def test_establishment_cursor_after_read_snapshot_fails_closed(
    posting_db: Session,
    posting_world,
):
    account = posting_fixtures.make_account(
        posting_db,
        organization=posting_world.organization,
        material=posting_world.material,
    )
    establishment = posting_db.info["opening_facts_by_account"][account.id].establishment
    establishment.established_ledger_cursor = 1
    posting_db.flush()
    with pytest.raises(InventoryReadError) as captured:
        list_inventory_accounts(
            posting_db,
            actor=_reader(posting_world),
            limit=100,
        )
    assert captured.value.code == "inventory_opening_establishment_invalid"


def test_two_terminal_tasks_use_one_query_batch_in_uuid_order(
    posting_db: Session,
    posting_world,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    accounts = [
        posting_fixtures.make_account(
            posting_db,
            organization=posting_world.organization,
            material=posting_world.material,
        )
        for _ in range(2)
    ]
    posting_db.commit()
    expected_task_ids = tuple(
        sorted(
            {
                posting_db.info["opening_facts_by_account"][account.id].task.id
                for account in accounts
            },
            key=str,
        )
    )
    assert len(expected_task_ids) == 2

    finalize = inventory_query_service.finalize_service
    original_root = finalize._lock_opening_terminal_task_batch_root
    original_graph = finalize._lock_opening_terminal_task_batch_graph
    original_pure = finalize._validate_opening_terminal_batch_from_prelocked_graph
    calls: list[tuple[str, tuple[uuid.UUID, ...], int | None]] = []

    def traced_root(db: Session, *, task_ids):
        proof = original_root(db, task_ids=task_ids)
        calls.append(
            ("root", tuple(row.id for row in proof.tasks), proof.current_ledger_cursor)
        )
        return proof

    def traced_graph(db: Session, *, root, supplied_user_ids=()):
        calls.append(("graph", tuple(row.id for row in root.tasks), None))
        return original_graph(
            db,
            root=root,
            supplied_user_ids=supplied_user_ids,
        )

    def traced_pure(db: Session, *, proof, expected_ledger_cursor=None, **kwargs):
        calls.append(
            (
                "pure",
                tuple(row.root.task.id for row in proof.task_graphs),
                expected_ledger_cursor,
            )
        )
        return original_pure(
            db,
            proof=proof,
            expected_ledger_cursor=expected_ledger_cursor,
            **kwargs,
        )

    monkeypatch.setattr(finalize, "_lock_opening_terminal_task_batch_root", traced_root)
    monkeypatch.setattr(finalize, "_lock_opening_terminal_task_batch_graph", traced_graph)
    monkeypatch.setattr(
        finalize,
        "_validate_opening_terminal_batch_from_prelocked_graph",
        traced_pure,
    )

    summary = inventory_summary(posting_db, actor=_reader(posting_world))

    assert summary.opening_balance_status == "established"
    assert calls == [
        ("root", expected_task_ids, summary.ledger_cursor),
        ("graph", expected_task_ids, None),
        ("pure", expected_task_ids, summary.ledger_cursor),
    ]


def test_closed_query_prelocks_reconciliation_batch_before_one_audit(
    posting_db: Session,
    posting_world,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    accounts = [
        posting_fixtures.make_account(
            posting_db,
            organization=posting_world.organization,
            material=posting_world.material,
        )
        for _ in range(2)
    ]
    posting_db.commit()
    task_ids = tuple(
        sorted(
            {
                posting_db.info["opening_facts_by_account"][account.id].task.id
                for account in accounts
            },
            key=str,
        )
    )
    closed_task_id = task_ids[1]
    actor = _reader(posting_world)
    events: list[tuple[str, object]] = []
    principal_proof = object()
    graph_proof = SimpleNamespace(principal_graph=principal_proof)
    reconciliation_proof = object()
    audit_head = object()
    expected_audit_proof = object()

    def root(_db: Session, *, task_ids):
        checked = tuple(sorted(task_ids, key=str))
        events.append(("root", checked))
        return SimpleNamespace(
            tasks=tuple(
                SimpleNamespace(
                    id=task_id,
                    status="closed" if task_id == closed_task_id else "posted",
                )
                for task_id in checked
            ),
            current_ledger_cursor=0,
        )

    def reconciliation_users(_db: Session, *, task_ids):
        events.append(("reconciliation-users", tuple(task_ids)))
        return ("historical-reconciliation-user",)

    def opening_graph(_db: Session, *, root, supplied_user_ids=()):
        del root
        events.append(("opening-graph", tuple(supplied_user_ids)))
        return graph_proof

    def reconciliation_graph(_db: Session, *, task_ids, principal_graph):
        assert principal_graph is principal_proof
        events.append(("reconciliation-graph", tuple(task_ids)))
        return reconciliation_proof

    def audit(_db: Session, *, stream_key):
        events.append(("audit", stream_key))
        return audit_head, expected_audit_proof

    def opening_pure(
        _db: Session,
        *,
        proof,
        audit_proof: object,
        require_closed_task_ids=(),
        expected_ledger_cursor=None,
    ):
        assert proof is graph_proof
        assert audit_proof is expected_audit_proof
        events.append(
            (
                "opening-pure",
                (tuple(require_closed_task_ids), expected_ledger_cursor),
            )
        )
        return ()

    def reconciliation_pure(_db: Session, *, proof, audit_proof):
        assert proof is reconciliation_proof
        assert audit_proof is expected_audit_proof
        events.append(("reconciliation-pure", closed_task_id))
        return {
            closed_task_id: SimpleNamespace(status="approved"),
        }

    monkeypatch.setattr(
        inventory_query_service.finalize_service,
        "_lock_opening_terminal_task_batch_root",
        root,
    )
    monkeypatch.setattr(
        inventory_query_service.reconciliation_service,
        "_opening_control_reconciliation_historical_user_ids",
        reconciliation_users,
    )
    monkeypatch.setattr(
        inventory_query_service.finalize_service,
        "_lock_opening_terminal_task_batch_graph",
        opening_graph,
    )
    monkeypatch.setattr(
        inventory_query_service.reconciliation_service,
        "_lock_opening_control_reconciliation_batch_graph",
        reconciliation_graph,
    )
    monkeypatch.setattr(
        inventory_query_service,
        "_lock_audit_chain_head_with_proof",
        audit,
    )
    monkeypatch.setattr(
        inventory_query_service.finalize_service,
        "_validate_opening_terminal_batch_from_prelocked_graph",
        opening_pure,
    )
    monkeypatch.setattr(
        inventory_query_service.reconciliation_service,
        "_opening_control_reconciliation_statuses_from_prelocked_batch",
        reconciliation_pure,
    )

    summary = inventory_summary(posting_db, actor=actor)

    assert summary.opening_balance_status == "established"
    assert events == [
        ("root", task_ids),
        ("reconciliation-users", (closed_task_id,)),
        (
            "opening-graph",
            (actor.user_id, "historical-reconciliation-user"),
        ),
        ("reconciliation-graph", (closed_task_id,)),
        ("audit", "inventory"),
        ("opening-pure", ((closed_task_id,), summary.ledger_cursor)),
        ("reconciliation-pure", closed_task_id),
    ]


def test_query_rejects_projection_cursor_drift_before_batch_graph(
    posting_db: Session,
    posting_world,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    posting_fixtures.make_account(
        posting_db,
        organization=posting_world.organization,
        material=posting_world.material,
    )
    posting_db.commit()
    finalize = inventory_query_service.finalize_service
    original_root = finalize._lock_opening_terminal_task_batch_root

    def drifted_root(db: Session, *, task_ids):
        root = original_root(db, task_ids=task_ids)
        return replace(
            root,
            current_ledger_cursor=root.current_ledger_cursor + 1,
        )

    monkeypatch.setattr(finalize, "_lock_opening_terminal_task_batch_root", drifted_root)
    monkeypatch.setattr(
        finalize,
        "_lock_opening_terminal_task_batch_graph",
        lambda *_args, **_kwargs: pytest.fail(
            "cursor drift must fail before the batch graph"
        ),
    )

    with pytest.raises(InventoryReadError) as captured:
        inventory_summary(posting_db, actor=_reader(posting_world))

    assert captured.value.code == "inventory_opening_establishment_invalid"


@pytest.mark.parametrize(
    ("field_name", "tampered_value"),
    (
        ("quantity", Decimal("12.344")),
        ("ledger_cursor", 0),
        ("version", 0),
    ),
)
def test_accounts_fail_closed_when_balance_projection_is_tampered(
    posting_db: Session,
    posting_world,
    field_name: str,
    tampered_value: Decimal | int,
):
    account = posting_fixtures.make_account(
        posting_db,
        organization=posting_world.organization,
        material=posting_world.material,
    )
    facts = posting_db.info["opening_facts_by_account"][account.id]
    posting_fixtures.make_positive_opening_facts(
        posting_db,
        facts,
        quantity=Decimal("12.345"),
    )
    posting_db.commit()
    balance = posting_db.get(StockBalance, account.id)
    assert balance is not None
    setattr(balance, field_name, tampered_value)
    posting_db.flush()

    with pytest.raises(InventoryReadError) as captured:
        list_inventory_accounts(
            posting_db,
            actor=_reader(posting_world),
            limit=100,
        )
    assert captured.value.code == "inventory_projection_integrity_invalid"
    assert captured.value.status_code == 503


def test_accounts_projection_integrity_gate_is_not_limited_by_page_window(
    posting_db: Session,
    posting_world,
):
    accounts = [
        posting_fixtures.make_account(
            posting_db,
            organization=posting_world.organization,
            material=posting_world.material,
        )
        for _ in range(2)
    ]
    for account in accounts:
        posting_fixtures.make_positive_opening_facts(
            posting_db,
            posting_db.info["opening_facts_by_account"][account.id],
            quantity=Decimal("1.000"),
        )
    posting_db.commit()
    outside_first_page = max(accounts, key=lambda row: str(row.id))
    balance = posting_db.get(StockBalance, outside_first_page.id)
    assert balance is not None
    balance.quantity = Decimal("2.000")
    posting_db.flush()

    with pytest.raises(InventoryReadError) as captured:
        list_inventory_accounts(
            posting_db,
            actor=_reader(posting_world),
            limit=1,
        )
    assert captured.value.code == "inventory_projection_integrity_invalid"
    assert captured.value.status_code == 503


def test_personal_warehouse_fails_closed_when_balance_projection_is_tampered(
    posting_db: Session,
    posting_world,
):
    account, _, facts = _make_personal_account(posting_db, posting_world)
    assert facts is not None
    positive = posting_fixtures.make_positive_opening_facts(
        posting_db,
        facts,
        quantity=Decimal("3.000"),
    )
    posting_db.commit()
    balance = posting_db.get(StockBalance, account.id)
    assert balance is not None
    balance.ledger_cursor = positive.transaction.ledger_cursor - 1
    posting_db.flush()

    with pytest.raises(InventoryReadError) as captured:
        personal_warehouse(
            posting_db,
            actor=_reader(
                posting_world,
                role_code="technician",
                scope_type="person",
                scope_id=str(posting_world.person.id),
            ),
        )
    assert captured.value.code == "inventory_projection_integrity_invalid"
    assert captured.value.status_code == 503


def test_transaction_detail_fails_closed_when_balance_projection_is_tampered(
    posting_db: Session,
    posting_world,
):
    account = posting_fixtures.make_account(
        posting_db,
        organization=posting_world.organization,
        material=posting_world.material,
    )
    positive = posting_fixtures.make_positive_opening_facts(
        posting_db,
        posting_db.info["opening_facts_by_account"][account.id],
        quantity=Decimal("2.000"),
    )
    posting_db.commit()
    balance = posting_db.get(StockBalance, account.id)
    assert balance is not None
    balance.quantity = Decimal("1.000")
    posting_db.flush()

    with pytest.raises(InventoryReadError) as captured:
        inventory_transaction_detail(
            posting_db,
            actor=_reader(posting_world),
            transaction_id=positive.transaction.id,
        )
    assert captured.value.code == "inventory_projection_integrity_invalid"
    assert captured.value.status_code == 503


@pytest.mark.parametrize("movement_type", ["consume", "stocktake_loss"])
def test_external_outbound_null_position_and_serial_projection_integrity(
    posting_db: Session,
    posting_world,
    movement_type,
):
    serial_material = posting_fixtures.make_material(
        posting_db,
        posting_world.source,
        tracking_mode="serial",
        quantity_scale=0,
        allow_fraction=False,
    )
    account = posting_fixtures.make_account(
        posting_db,
        organization=posting_world.organization,
        material=serial_material,
    )
    facts = posting_db.info["opening_facts_by_account"][account.id]
    serial = InventorySerial(
        id=uuid.uuid4(),
        material_id=serial_material.id,
        serial_no=f"SN-{uuid.uuid4().hex}",
        qr_code=f"QR-{uuid.uuid4().hex}",
        lot_id=None,
        lifecycle_status="active",
        created_at=facts.count_line.counted_at,
        updated_at=facts.count_line.counted_at,
    )
    posting_db.add(serial)
    posting_db.flush()
    opening = posting_fixtures.make_positive_opening_facts(
        posting_db,
        facts,
        quantity=Decimal("1.000"),
        serials=(serial,),
    )
    posting_db.commit()

    head = posting_db.get(
        InventoryLedgerHead,
        posting_fixtures.INVENTORY_LEDGER_HEAD_ID,
    )
    assert head is not None
    outbound_transaction = InventoryTransaction(
        id=uuid.uuid4(),
        transaction_no=f"TX-{uuid.uuid4().hex}",
        movement_type=movement_type,
        source_document_type="work_order_material" if movement_type == "consume" else "test_case",
        source_document_id=f"DOC-{uuid.uuid4().hex}",
        posting_key=f"inventory:test:{uuid.uuid4().hex}",
        idempotency_key_hash="a" * 64,
        request_hash="b" * 64,
        status="posted",
        effective_at=posting_fixtures.NOW,
        posted_at=posting_fixtures.NOW,
        ledger_cursor=head.next_cursor,
        reversed_transaction_id=None,
        actor_user_id=posting_world.user.id,
        created_at=posting_fixtures.NOW,
    )
    head.next_cursor += 1
    posting_db.add(outbound_transaction)
    posting_db.flush()
    outbound_movement = InventoryMovement(
        id=uuid.uuid4(),
        transaction_id=outbound_transaction.id,
        line_no=1,
        from_account_id=account.id,
        to_account_id=None,
        external_boundary_code="work_order_material_consume" if movement_type == "consume" else "external-recipient",
        quantity=Decimal("1.000"),
        created_at=posting_fixtures.NOW,
    )
    posting_db.add(outbound_movement)
    posting_db.flush()
    posting_db.add(
        InventoryMovementSerial(
            movement_id=outbound_movement.id,
            transaction_id=outbound_transaction.id,
            serial_id=serial.id,
            created_at=posting_fixtures.NOW,
        )
    )
    balance = posting_db.get(StockBalance, account.id)
    assert balance is not None
    balance.quantity = Decimal("0.000")
    balance.ledger_cursor = outbound_transaction.ledger_cursor
    balance.version = 2
    position = posting_db.get(SerialCurrentPosition, serial.id)
    assert position is not None
    position.stock_account_id = None
    position.last_movement_id = outbound_movement.id
    position.updated_at = posting_fixtures.NOW
    if movement_type == "consume":
        serial.lifecycle_status = "consumed"
        serial.updated_at = posting_fixtures.NOW
    posting_db.commit()
    actor = _reader(posting_world)
    position = posting_db.get(SerialCurrentPosition, serial.id)
    assert position is not None
    assert position.stock_account_id is None
    outbound_movement_id = position.last_movement_id

    page = list_inventory_accounts(posting_db, actor=actor, limit=100)
    item = next(row for row in page.items if row.stock_account_id == account.id)
    assert item.quantity == "0.000"
    detail = inventory_transaction_detail(
        posting_db,
        actor=actor,
        transaction_id=outbound_transaction.id,
    )
    assert detail.movements[0].to_account_id is None

    expected_lifecycle = serial.lifecycle_status
    serial.lifecycle_status = "active" if expected_lifecycle == "consumed" else "consumed"
    posting_db.flush()
    with pytest.raises(InventoryReadError) as lifecycle_drift:
        list_inventory_accounts(posting_db, actor=actor, limit=100)
    assert lifecycle_drift.value.code == "inventory_projection_integrity_invalid"
    assert lifecycle_drift.value.status_code == 503
    serial.lifecycle_status = expected_lifecycle
    posting_db.flush()

    position.last_movement_id = opening.movement.id
    posting_db.flush()
    with pytest.raises(InventoryReadError) as captured:
        inventory_transaction_detail(
            posting_db,
            actor=actor,
            transaction_id=outbound_transaction.id,
        )
    assert captured.value.code == "inventory_projection_integrity_invalid"
    assert captured.value.status_code == 503

    position.last_movement_id = outbound_movement_id
    position.stock_account_id = account.id
    posting_db.flush()
    with pytest.raises(InventoryReadError) as wrong_account:
        inventory_transaction_detail(
            posting_db,
            actor=actor,
            transaction_id=outbound_transaction.id,
        )
    assert wrong_account.value.code == "inventory_projection_integrity_invalid"
    assert wrong_account.value.status_code == 503


def test_projection_head_change_during_integrity_check_remains_retryable(
    posting_db: Session,
    posting_world,
    monkeypatch: pytest.MonkeyPatch,
):
    posting_fixtures.make_account(
        posting_db,
        organization=posting_world.organization,
        material=posting_world.material,
    )
    posting_db.commit()
    original = inventory_query_service._ensure_projection_snapshot_current

    def advance_head_then_verify(db: Session, snapshot) -> None:
        head = db.get(
            InventoryLedgerHead,
            posting_fixtures.INVENTORY_LEDGER_HEAD_ID,
        )
        assert head is not None
        head.next_cursor += 1
        db.flush()
        original(db, snapshot)

    monkeypatch.setattr(
        inventory_query_service,
        "_ensure_projection_snapshot_current",
        advance_head_then_verify,
    )

    with pytest.raises(InventoryReadError) as captured:
        list_inventory_accounts(
            posting_db,
            actor=_reader(posting_world),
            limit=100,
        )
    assert captured.value.code == "inventory_projection_changed"
    assert captured.value.status_code == 409
