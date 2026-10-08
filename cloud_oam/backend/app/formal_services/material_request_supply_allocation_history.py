"""Prove allocation and supply commands in their immutable version order.

This grants no planning or inventory write permission and never rewinds an ORM
row. New plans consume only verified unallocated, unplanned capacity. Other fulfillment paths must
be proved by their own readers before they can be accepted here.
"""
from decimal import Decimal
from hashlib import sha256
from sqlalchemy import or_, select

from app.demand_models import MaterialRequestCommand, ApprovalAction, ApprovalStep
from app.foundation_models import AuditEvent, StateTransitionEvent, RoleAssignment, Role
from app.inventory_models import (StockAllocation, StockAllocationSerial, StockAccount,
                                 InventorySerial, MaterialInventoryPolicy)
from .audit_chain import verify_audit_event_in_read_snapshot

_LATER_FULFILLMENT_OPERATIONS = frozenset(
    {"reserve", "release", "pick", "outbound", "shipment", "receipt", "personal_inbound"}
)

def allocation_history(db, *, request, lines, supply_commands, allow_later_fulfillment=False):
    # Late import avoids a module cycle and shares the existing strict envelope
    # parser, bounded reads and detached historical projection representation.
    from . import material_request_supply_command_status as history
    from . import material_request_supply as supply
    invalid = history._invalid
    def rows(db, statement):
        result = history._rows(db, statement.limit(1001))
        if len(result) > 1000:
            invalid()
        return result
    # Start at the actual final decision rather than assuming a plan was the
    # first fulfillment command. This also proves the pre-plan allocation
    # history needed to calculate the first remaining supply plan.
    from types import SimpleNamespace
    from .material_request_approval_history import verified_final_approved_quantities
    from .material_request_query import MaterialRequestReadError
    try:
        approved_by_line = verified_final_approved_quantities(db, request=request)
    except MaterialRequestReadError:
        invalid()
    anchors = rows(db, select(MaterialRequestCommand).join(ApprovalAction,
        ApprovalAction.command_id == MaterialRequestCommand.id).join(ApprovalStep,
        ApprovalStep.id == ApprovalAction.step_id).where(
        MaterialRequestCommand.request_id == request.id,
        ApprovalStep.step_no == 3,
        ApprovalStep.status.in_({'approved', 'partially_approved'}),
        ApprovalStep.decided_at == request.decided_at,
        ApprovalAction.action.in_({'approve', 'partial_approve', 'verify_external_accept'})))
    if len(anchors) != 1 or not lines or set(approved_by_line) != {line.id for line in lines}:
        invalid()
    anchor = anchors[0]
    last = SimpleNamespace(revision_id=lines[0].revision_id, revision_no=lines[0].revision_no)
    original_axes = dict(supply._NEUTRAL_AXES)
    commands = rows(db, select(MaterialRequestCommand).where(
        MaterialRequestCommand.request_id == request.id,
        MaterialRequestCommand.target_version > anchor.target_version,
    ).order_by(MaterialRequestCommand.target_version, MaterialRequestCommand.id))
    allowed_operations = {'allocate', *history._OPERATIONS}
    if allow_later_fulfillment:
        allowed_operations |= _LATER_FULFILLMENT_OPERATIONS
    if (len(commands) != request.version - anchor.target_version
            or any(c.operation not in allowed_operations
                   or c.target_version != anchor.target_version + i + 1 for i, c in enumerate(commands))
            or {c.id for c in commands if c.operation in history._OPERATIONS}
               != {c.id for c in supply_commands}):
        invalid()
    facts = rows(db, select(StockAllocation).where(StockAllocation.request_id == request.id))
    by_version = {f.request_version: f for f in facts}
    if len(by_version) != len(facts) or set(by_version) != {c.target_version for c in commands if c.operation == 'allocate'}:
        invalid()
    by_line = {line.id: line for line in lines}
    quantities = {line.id: Decimal('0.000') for line in lines}
    if any(line.cancelled_qty != 0 for line in lines):
        invalid()
    approved = sum((line.final_approved_qty for line in lines), Decimal('0.000'))
    axes = dict(original_axes)
    state_frame = dict(original_axes)
    previous_time = supply._as_utc(anchor.occurred_at)
    frames = {}
    previous_audit_version = None
    used_audits, used_events = set(), set()
    plan_states = {}
    later_seen = False
    for command in commands:
        if command.operation in history._OPERATIONS:
            result = history._command_result(command)
            expected_frame = state_frame if later_seen else axes
            result_axes = dict(result.state_axes)
            if (result_axes != {'request_status': request.status, **expected_frame}
                    or supply._as_utc(command.occurred_at) < previous_time):
                invalid()
            line = by_line.get(result.request_line_id)
            if line is None:
                invalid()
            if command.operation == 'create_supply_task':
                planned = sum((Decimal(row.original_equivalent_qty) for row in plan_states.values()
                    if row.request_line_id == line.id and row.task_status in supply._ACTIVE_TASK_STATUSES), Decimal('0.000'))
                if (result.supply_task_id in plan_states
                        or Decimal(result.original_equivalent_qty) + planned + quantities[line.id] > line.final_approved_qty):
                    invalid()
            plan_states[result.supply_task_id] = result
            frames[command.target_version] = dict(expected_frame)
            previous_time = supply._as_utc(command.occurred_at)
            continue
        if command.operation in _LATER_FULFILLMENT_OPERATIONS:
            if (not allow_later_fulfillment or axes['allocation_status'] == 'not_allocated'
                    or later_seen and command.operation == 'allocate'):
                invalid()
            # Downstream facts are verified by the reservation/remainder
            # readers. Keep their version frame so a later supply command can
            # still be checked against the allocation state that preceded it.
            later_seen = True
            candidate = command.result_jsonb.get('state_axes') if isinstance(command.result_jsonb, dict) else None
            if isinstance(candidate, dict) and candidate.get('request_status') == request.status:
                candidate_axes = {key: candidate[key] for key in original_axes if key in candidate}
                if set(candidate_axes) != set(original_axes) or candidate_axes.get('allocation_status') != axes['allocation_status']:
                    invalid()
                state_frame = candidate_axes
            frames[command.target_version] = dict(state_frame)
            previous_time = supply._as_utc(command.occurred_at)
            continue
        if later_seen:
            invalid()
        fact = by_version[command.target_version]
        line = by_line.get(fact.request_line_id)
        account = db.get(StockAccount, fact.source_stock_account_id, populate_existing=True)
        if (line is None or account is None or account.material_id != line.material_id
                or fact.revision_id != last.revision_id or fact.revision_no != last.revision_no
                or line.revision_id != fact.revision_id or line.revision_no != fact.revision_no
                or fact.status != 'allocated' or fact.allocated_qty <= 0
                or fact.allocated_qty != fact.allocated_qty.quantize(Decimal('.001'))
                or command.actor_user_id != fact.actor_user_id or command.actor_person_id != fact.actor_person_id
                or command.authorization_version != fact.authorization_version
                or command.authorization_version <= 0 or command.actor_role_assignment_id is None
                or command.idempotency_key_hash != fact.idempotency_key_hash
                or command.request_hash != fact.request_hash
                or command.request_reference != f'/api/v1/material-requests/{request.id}/allocations'
                or any(supply._as_utc(value) != supply._as_utc(command.occurred_at)
                       for value in (command.created_at, fact.created_at, fact.updated_at))
                or supply._as_utc(command.occurred_at) < previous_time):
            invalid()
        quantities[line.id] += fact.allocated_qty
        if quantities[line.id] > line.final_approved_qty:
            invalid()
        for value in (command.idempotency_key_hash, command.request_hash, command.result_hash):
            if not isinstance(value, str) or history._HASH.fullmatch(value) is None:
                invalid()
        grant = db.get(RoleAssignment, command.actor_role_assignment_id, populate_existing=True)
        role = db.get(Role, grant.role_id, populate_existing=True) if grant else None
        when = supply._as_utc(command.occurred_at)
        if (grant is None or role is None or role.code not in {'admin', 'provincial_manager'}
                or grant.user_id != command.actor_user_id or supply._as_utc(grant.valid_from) > when
                or (grant.valid_to is not None and supply._as_utc(grant.valid_to) <= when)
                or (grant.revoked_at is not None and supply._as_utc(grant.revoked_at) <= when)):
            invalid()
        previous_status = axes['allocation_status']
        axes['allocation_status'] = ('allocated' if sum(quantities.values(), Decimal('0')) == approved
                                     else 'partially_allocated')
        state_frame['allocation_status'] = axes['allocation_status']
        expected = dict(kind='allocation', schema_version='1.0', request_id=str(request.id),
            request_no=request.request_no, request_version=command.target_version,
            revision_id=str(fact.revision_id), revision_no=fact.revision_no,
            request_line_id=str(line.id), allocation_id=str(fact.id), allocation_no=fact.allocation_no,
            source_stock_account_id=str(account.id), allocated_qty=format(fact.allocated_qty, '.3f'),
            allocation_status=fact.status, state_axes=dict(axes))
        if command.result_jsonb != expected or command.result_hash != supply._canonical_hash(expected):
            invalid()
        if command.request_jsonb != dict(schema='rsc.material_request_allocation_command.v1',
                operation='allocate', request_id=str(request.id), revision_id=str(fact.revision_id),
                revision_no=fact.revision_no, request_line_id=str(line.id), allocation_id=str(fact.id),
                target_version=command.target_version, payload_sha256=command.request_hash,
                comment_sha256=sha256(b'').hexdigest(), sensitive_fields='excluded'):
            invalid()
        serials = rows(db, select(StockAllocationSerial).where(StockAllocationSerial.allocation_id == fact.id))
        policies = rows(db, select(MaterialInventoryPolicy).where(
            MaterialInventoryPolicy.material_id == line.material_id,
            MaterialInventoryPolicy.effective_from <= fact.created_at,
            or_(MaterialInventoryPolicy.effective_to.is_(None), MaterialInventoryPolicy.effective_to > fact.created_at)))
        if len(policies) != 1:
            invalid()
        serial_mode = policies[0].tracking_mode in {'serial', 'lot_and_serial'}
        if ((serial_mode and len(serials) != fact.allocated_qty) or (not serial_mode and serials)
                or len({s.serial_id for s in serials}) != len(serials)):
            invalid()
        for binding in serials:
            serial = db.get(InventorySerial, binding.serial_id, populate_existing=True)
            if (serial is None or serial.material_id != line.material_id
                    or not supply._same_timestamp(binding.created_at, fact.created_at)):
                invalid()
        audits = rows(db, select(AuditEvent).where(AuditEvent.stream_key == 'material_request',
            AuditEvent.action == 'material_request_allocation_created',
            AuditEvent.aggregate_type == 'stock_allocation', AuditEvent.aggregate_id == str(fact.id)))
        events = rows(db, select(StateTransitionEvent).where(
            StateTransitionEvent.idempotency_key == 'allocation-state-' + fact.idempotency_key_hash))
        changed = previous_status != axes['allocation_status']
        if len(audits) != 1 or len(events) != int(changed):
            invalid()
        audit = audits[0]
        if (audit.actor_user_id != command.actor_user_id
                or audit.before_jsonb != dict(request_version=command.target_version - 1, allocation_status=previous_status)
                or audit.after_jsonb != dict(allocation_id=str(fact.id), allocation_no=fact.allocation_no,
                    request_id=str(request.id), request_line_id=str(line.id), source_stock_account_id=str(account.id),
                    allocated_qty=format(fact.allocated_qty, '.3f'), source_balance_version=fact.source_balance_version,
                    source_ledger_cursor=fact.source_ledger_cursor, request_version=command.target_version)
                or not supply._same_timestamp(audit.occurred_at, command.occurred_at)
                or (previous_audit_version is not None and audit.stream_version <= previous_audit_version)):
            invalid()
        if changed:
            event = events[0]
            if (event.aggregate_type != 'material_request' or event.aggregate_id != str(request.id)
                    or event.actor_id != command.actor_user_id or event.from_status != previous_status
                    or event.to_status != axes['allocation_status'] or event.reason != 'material_request_allocation_created'
                    or not supply._same_timestamp(event.occurred_at, command.occurred_at)
                    or not supply._same_timestamp(event.created_at, command.occurred_at)
                    or event.metadata_jsonb != dict(allocation_id=str(fact.id), allocated_qty=format(fact.allocated_qty, '.3f'),
                        command_id=str(command.id), idempotency_key_hash=command.idempotency_key_hash,
                        request_version=command.target_version)):
                invalid()
        verify_audit_event_in_read_snapshot(db, stream_key='material_request', event_id=audit.id)
        used_audits.add(audit.id)
        used_events.update(e.id for e in events)
        previous_time = supply._as_utc(command.occurred_at)
        previous_audit_version = audit.stream_version
    # The current request must be exactly the verified suffix, not an arbitrary
    # larger version, mutated status, or an unobserved inventory operation.
    current_axes = supply._fulfillment_axes(request)
    if allow_later_fulfillment and later_seen:
        if current_axes['allocation_status'] != axes['allocation_status']:
            invalid()
    elif (current_axes != axes
            or not supply._same_timestamp(request.updated_at, previous_time)):
        invalid()
    all_audits = rows(db, select(AuditEvent).where(AuditEvent.stream_key == 'material_request',
        AuditEvent.action == 'material_request_allocation_created',
        AuditEvent.after_jsonb['request_id'].as_string() == str(request.id)))
    all_events = rows(db, select(StateTransitionEvent).where(
        StateTransitionEvent.aggregate_type == 'material_request',
        StateTransitionEvent.aggregate_id == str(request.id),
        StateTransitionEvent.reason == 'material_request_allocation_created'))
    if {a.id for a in all_audits} != used_audits or {e.id for e in all_events} != used_events:
        invalid()
    return frames, tuple(sorted(all_audits, key=lambda row: row.stream_version)), previous_time
