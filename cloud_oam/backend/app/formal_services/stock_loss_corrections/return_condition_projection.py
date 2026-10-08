"""Fold a complete, adapter-verified correction history without changing stock.

Sequence is the adapter's strict committed event order, not a client timestamp.
Check every prefix: a later release cannot excuse an earlier double claim.
Rejected/cancelled decisions retain their exact frozen share until the release
posting exists. Execution permanently consumes historical exception budget.
This module does not verify current permissions, files, physical evidence,
request uniqueness, or actual ledger/audit/outbox existence; DB guards and a
service adapter must do those before any write path can be installed.
"""
from dataclasses import replace

from app.formal_services.stock_loss_planning import require_reason
from .return_condition_contracts import Action, CaseState, Claim, Posting, Projection
from .return_condition_planning import (
    freeze_outline, identifier, integer, need, quantity, serials,
    settlement_outline, units, validate_basis,
)


ACTIVE = frozenset({'awaiting_regional', 'awaiting_headquarters', 'needs_evidence',
    'approved', 'rejected_pending_release', 'cancelled_pending_release'})


def _user(value):
    need(type(value) is str and 0 < len(value) <= 200 and value == value.strip()
         and all(ord(c) >= 32 for c in value), 'user_invalid')
    value.encode('utf-8')


def _evidence(event):
    require_reason(event.reason)
    event.reason.encode('utf-8')
    need(type(event.evidence_file_ids) is tuple, 'evidence_tuple_required')
    for item in event.evidence_file_ids:
        identifier(item)
    need(len(set(event.evidence_file_ids)) == len(event.evidence_file_ids), 'duplicate_evidence')
    need(len(event.evidence_file_ids) <= 20, 'evidence_count_invalid')


def _posting(actual, outline, last_cursor, transactions, movements):
    need(type(actual) is Posting, 'posting_required')
    for value in (actual.transaction_id, actual.movement_id, actual.from_account_id, actual.to_account_id):
        identifier(value)
    integer(actual.ledger_cursor)
    units(actual.quantity)
    serials(actual.serial_ids)
    need(actual.ledger_cursor > last_cursor, 'posting_order_invalid')
    need(actual.transaction_id not in transactions and actual.movement_id not in movements,
         'posting_reused')
    need((actual.from_account_id, actual.to_account_id, actual.quantity,
          frozenset(actual.serial_ids), actual.movement_type) ==
         (outline.from_account_id, outline.to_account_id, outline.quantity,
          frozenset(outline.serial_ids), outline.movement_type), 'posting_not_exact')
    transactions.add(actual.transaction_id)
    movements.add(actual.movement_id)
    return actual.ledger_cursor


def _budget(basis, cases):
    held, corrected, held_sn, corrected_sn = 0, 0, set(), set()
    for state in cases.values():
        if state.status not in ACTIVE and state.status != 'executed':
            continue
        chosen = state.claim.selected
        sn = set(chosen.serial_ids)
        need(not sn.intersection(held_sn | corrected_sn), 'serial_already_claimed')
        if state.status == 'executed':
            corrected += units(chosen.quantity)
            corrected_sn.update(sn)
        else:
            held += units(chosen.quantity)
            held_sn.update(sn)
    unclaimed = units(basis.affected.quantity) - held - corrected
    need(unclaimed >= 0, 'exception_overclaimed')
    return Projection(tuple(cases.values()), quantity(held), quantity(corrected), quantity(unclaimed),
        frozenset(held_sn), frozenset(corrected_sn),
        frozenset(basis.affected.serial_ids) - held_sn - corrected_sn)


def _actor(event, state, *, requester=False):
    same_user = event.actor_user_id == state.claim.requester_user_id
    same_person = event.actor_person_id == state.claim.requester_person_id
    if requester:
        need(same_user and same_person, 'requester_required')
    else:
        need(not same_user and not same_person, 'self_review_forbidden')


def _decision(event, state):
    """Authority/scope must already be proved, not inferred from these verbs."""
    need(event.decision_id is None and event.posting is None and event.target is None,
         'unexpected_posting_or_binding')
    kind, stage = event.kind, state.status
    if kind == 'supplement':
        _actor(event, state, requester=True)
        need(stage == 'needs_evidence', 'supplement_stage_conflict')
        need(bool(event.evidence_file_ids), 'physical_evidence_required')
        return replace(state, status='awaiting_regional', regional_decision_id=None,
            regional_reviewer_user_id=None, regional_reviewer_person_id=None,
            terminal_decision_id=None)
    if kind == 'withdraw':
        _actor(event, state, requester=True)
        need(stage in {'awaiting_regional', 'awaiting_headquarters', 'needs_evidence'},
             'withdraw_stage_conflict')
        return replace(state, status='cancelled_pending_release', terminal_decision_id=event.id)
    _actor(event, state)
    if kind in {'verify_region', 'return_evidence', 'reject_region'}:
        need(stage == 'awaiting_regional', 'regional_stage_conflict')
        if kind == 'verify_region':
            need(bool(event.evidence_file_ids), 'physical_evidence_required')
            return replace(state, status='awaiting_headquarters', regional_decision_id=event.id,
                regional_reviewer_user_id=event.actor_user_id,
                regional_reviewer_person_id=event.actor_person_id)
        return replace(state, status='needs_evidence' if kind == 'return_evidence'
            else 'rejected_pending_release',
            terminal_decision_id=event.id if kind == 'reject_region' else None)
    if kind in {'approve_hq', 'return_region', 'reject_hq', 'cancel_approved'}:
        need(stage == ('approved' if kind == 'cancel_approved' else 'awaiting_headquarters'),
             'headquarters_stage_conflict')
        need(state.regional_decision_id is not None, 'regional_decision_required')
        need(event.actor_user_id != state.regional_reviewer_user_id and
             event.actor_person_id != state.regional_reviewer_person_id,
             'independent_headquarters_review_required')
        if kind == 'return_region':
            return replace(state, status='awaiting_regional', regional_decision_id=None,
                regional_reviewer_user_id=None, regional_reviewer_person_id=None,
                terminal_decision_id=None)
        return replace(state, status={'approve_hq': 'approved', 'reject_hq': 'rejected_pending_release',
            'cancel_approved': 'cancelled_pending_release'}[kind], terminal_decision_id=event.id)
    need(False, 'unknown_action')


def project(basis, events):
    """Validate ALL committed events and return immutable state/share accounting.

No historical cut-off option: omitted later facts must never reopen already
consumed exception budget. Completeness is proved by the adapter's DB snapshot.
"""
    validate_basis(basis)
    need(type(events) is tuple, 'immutable_history_required')
    cases, seen, transactions = {}, set(), {basis.original_transaction_id}
    movements, last_cursor, sequence = {basis.original_movement_id}, basis.original_ledger_cursor, 0
    remaining, occupied_serials = units(basis.affected.quantity), set()
    for event in events:
        need(type(event) in {Claim, Action}, 'event_type_invalid')
        identifier(event.id)
        identifier(event.case_id)
        integer(event.sequence)
        need(event.id not in seen and event.sequence > sequence, 'event_reused_or_unordered')
        seen.add(event.id)
        sequence = event.sequence
        _evidence(event)
        if type(event) is Claim:
            identifier(event.inbound_line_id)
            identifier(event.requester_person_id)
            _user(event.requester_user_id)
            need(bool(event.evidence_file_ids), 'physical_evidence_required')
            need(event.case_id not in cases and event.inbound_line_id == basis.inbound_line_id,
                 'claim_binding_invalid')
            outline = freeze_outline(basis, case_id=event.case_id,
                requester_person_id=event.requester_person_id, selected=event.selected, frozen=event.frozen)
            chosen_serials = set(event.selected.serial_ids)
            need(not chosen_serials.intersection(occupied_serials), 'serial_already_claimed')
            remaining -= units(event.selected.quantity)
            need(remaining >= 0, 'exception_overclaimed')
            occupied_serials.update(chosen_serials)
            last_cursor = _posting(event.posting, outline, last_cursor, transactions, movements)
            cases[event.case_id] = CaseState(event.case_id, event, 'awaiting_regional')
        else:
            identifier(event.actor_person_id)
            _user(event.actor_user_id)
            need(type(event.kind) is str, 'unknown_action')
            need(event.case_id in cases, 'orphan_action')
            state = cases[event.case_id]
            if event.kind in {'release', 'execute'}:
                _actor(event, state, requester=True)
                identifier(event.decision_id)
                need(event.decision_id == state.terminal_decision_id, 'decision_binding_invalid')
                execute = event.kind == 'execute'
                need(state.status == 'approved' if execute else state.status in
                    {'rejected_pending_release', 'cancelled_pending_release'}, 'settlement_stage_conflict')
                outline = settlement_outline(basis, claim=state.claim, target=event.target, execute=execute)
                last_cursor = _posting(event.posting, outline, last_cursor, transactions, movements)
                status = 'executed' if execute else ('released_rejected'
                    if state.status == 'rejected_pending_release' else 'released_cancelled')
                cases[event.case_id] = replace(state, status=status)
                if not execute:
                    remaining += units(state.claim.selected.quantity)
                    occupied_serials.difference_update(state.claim.selected.serial_ids)
            else:
                cases[event.case_id] = _decision(event, state)
    # Independent final fold cross-checks the incremental prefix budget. Avoid
    # rescanning every case for every approval in a long exception history.
    result = _budget(basis, cases)
    need(units(result.unclaimed_quantity) == remaining and occupied_serials ==
         result.held_serial_ids | result.corrected_serial_ids, 'budget_projection_mismatch')
    return result
