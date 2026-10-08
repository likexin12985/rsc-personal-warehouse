"""Private execute/release writer; caller owns the entire commit or rollback.

This unpublished candidate requires the settlement input/target-account SQL
guards and full native acceptance before any HTTP route or grant activation.
An already used request is recovered through lookup, never implicitly replayed.
"""
from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import select
from app.formal_access import lock_formal_principal_graph
from app.inventory_models import StockAccount, InventoryMovement
from app.return_condition_schema import TRANSITIONS
from app.return_condition_settlement_requests import validate_settlement
from app.formal_services import inventory_posting as posting
from . import return_condition_authority as authority, return_condition_history as graph
from . import return_condition_history_read as history, return_condition_business_events as business
from . import return_condition_coordinates as coordinates, return_condition_keys as keys
from . import return_condition_identity as identity, return_condition_submission as initial
from . import return_condition_settlement_source as preparation, return_condition_settlement_permit as permits
from . import return_condition_settlement_inputs as inputs
from .return_condition_posting_authority import discard
from .return_condition_event_files import read_event_evidence


def settle(db, *, actor, request):
    request = validate_settlement(request)
    posting._lock_inventory_ledger_head_for_atomic_batch(db)
    lock_formal_principal_graph(db, (actor.user_id,))
    # A found outcome is never replayed by this service. Current stage and
    # authority are mandatory; callers recover completed writes through lookup.
    admitted = authority.authorize_action(db, actor=actor, case_id=request.case_id,
        expected_event_id=request.expected_event_id, kind=request.action)
    actor = admitted.actor
    key = posting._storage_hash('stock-condition:' + request.idempotency_key)
    initial._unused(db, actor, request, key)
    coordinates.require_unused(db, actor=actor, request=request)
    prepared = preparation.inspect_settlement_source(db, actor=actor, request=request)
    if prepared.admission != admitted:
        initial.fail('authority_changed', '执行前身份或原审批发生变化，请重新核验')
    tables = graph.tables()
    case = dict(db.execute(select(tables['stock_condition_cases']).where(
        tables['stock_condition_cases'].c.id == request.case_id)).mappings().one())
    previous = dict(db.execute(select(tables['stock_condition_events']).where(
        tables['stock_condition_events'].c.id == request.expected_event_id)).mappings().one())
    groups, fingerprint = graph.capture(db, case['inbound_line_id'])
    if fingerprint != prepared.history_hash:
        graph.changed()
    targets = [after for kind, before, after in TRANSITIONS
        if kind == request.action and before == prepared.case_state.status]
    if len(targets) != 1: permits.invalid()
    at = datetime.now(timezone.utc)
    files = initial._files(db, actor, request, at)
    if prepared.target is None:
        target = StockAccount(id=uuid4(), created_at=at, updated_at=at, **dict(prepared.target_dimensions))
        db.add(target); db.flush()
    else:
        target = db.get(StockAccount, prepared.target.id, populate_existing=True)
        if target is None: permits.invalid()
    selected = tuple(sorted(prepared.case_state.claim.selected.serial_ids, key=str))
    event = dict(id=uuid4(), created_at=at, actor_user_id=actor.user_id,
        actor_person_id=actor.person_id, authorization_version=actor.authorization_version,
        request_id=request.request_id, idempotency_key_hash=key, reason=request.reason,
        case_id=case['id'], submit_event_id=case['submit_event_id'], inbound_line_id=case['inbound_line_id'],
        source_account_id=case['source_account_id'], frozen_account_id=case['frozen_account_id'], quantity=case['quantity'],
        event_sequence=len(groups['stock_condition_events']) + 1, kind=request.action,
        from_state=previous['to_state'], to_state=targets[0], previous_event_id=previous['id'],
        previous_sequence=previous['event_sequence'], decision_event_id=previous['id'], decision_kind=previous['kind'],
        posting_transaction_id=None, posting_movement_id=None,
        movement_type='status_change' if request.action == 'execute' else 'unfreeze',
        from_account_id=prepared.frozen.id, to_account_id=target.id)
    manifest = [dict(file_id=f.file_id, metadata_sha256=f.metadata_sha256) for f in files]
    binding = identity.identity(case, event, serial_ids=selected, evidence=manifest,
        previous_request_hash=request.expected_event_hash)
    event.update(plan_jsonb=binding.plan, plan_hash=binding.plan_hash, command_jsonb=binding.command, request_hash=binding.request_hash)
    command, posting_key, posting_hash = identity.inventory_identity(event, selected)
    command = posting._validate_posting_command(command)
    permit = permits.issue(db, request=request, prepared=prepared, command=command, event=event,
        key_hash=posting_key, request_hash=posting_hash)
    try:
        posting._require_unused_business_keys(db, command)
        committed = posting._post_new_transaction(db, actor=actor, command=command,
            idempotency_key_hash=posting_key, request_hash=posting_hash,
            request_reference=posting._request_reference(request.request_id),
            permission_resource='stock_operation', permission_action=authority.ACTIONS[request.action],
            reversed_transaction_id=None, event_suffix='posted', occurred_at=at, condition_authority=permit)
        movement = db.scalars(select(InventoryMovement.id).where(
            InventoryMovement.transaction_id == committed.result.transaction_id)).one()
        event.update(posting_transaction_id=committed.result.transaction_id, posting_movement_id=movement)
        rows = {'stock_condition_events': [event], 'stock_condition_files': [
            dict(event_id=event['id'], created_at=at, **f) for f in manifest]}
        for name, entries in rows.items():
            if entries: db.execute(tables[name].insert(), entries)
        db.flush()
        inputs.record(db, request=request, case=case, event=event)
        business.record(db, case=case, event=event, recipient=prepared.basis.source.custodian_person_id)
        keys.record(db, event=event, request=request)
        initial._persisted(db, rows)
        if read_event_evidence(db, event_id=event['id']) != files:
            initial.fail('evidence_changed', '执行或释放附件持久证明发生变化')
        proved = history.read(db, actor=actor, inbound_line_id=case['inbound_line_id'])
        states = [s for s in proved.graph.projection.cases if s.case_id == case['id']]
        if len(states) != 1 or states[0].status != event['to_state']: graph.changed()
        inputs.match_original(db, request=request, case=case, event=event)
        return business.verify(db, case=case, event=event, recipient=prepared.basis.source.custodian_person_id)
    finally:
        discard(db, permit)
