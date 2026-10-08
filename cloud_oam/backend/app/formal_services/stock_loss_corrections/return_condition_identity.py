"""Canonical internal condition facts and inventory commands, not authority.

Inputs must be independently loaded/verified facts. These helpers do not grant
permission, prove evidence or make a command safe to execute or replay.
"""
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from app.formal_services import inventory_posting as posting


def _id(value):
    return str(value) if value is not None else None


def _quantity(value):
    if type(value) is not Decimal or not value.is_finite() or value <= 0:
        raise ValueError('positive exact Decimal required')
    # String trimming is independent of the ambient Decimal context.
    result = format(value, 'f')
    return result.rstrip('0').rstrip('.') if '.' in result else result


def actor_document(event):
    return dict(user_id=event['actor_user_id'],person_id=_id(event['actor_person_id']),
                authorization_version=event['authorization_version'])


def plan_document(case, event, serial_ids):
    serials = tuple(sorted(serial_ids,key=str))
    if any(type(v) is not UUID or not v.int for v in serials) or len(set(serials)) != len(serials):
        raise ValueError('unique nonzero UUID serials required')
    return dict(schema_version='condition_plan/1',case_id=_id(case['id']),event_id=_id(event['id']),
        action=event['kind'],actor=actor_document(event),history_hash=case['history_hash'],source_hash=case['source_hash'],
        root_disposition_id=_id(case['root_disposition_id']),inbound_id=_id(case['inbound_id']),
        original_transaction_id=_id(case['original_transaction_id']),original_movement_id=_id(case['original_movement_id']),
        original_ledger_cursor=case['original_ledger_cursor'],custody_assignment_id=_id(case['custody_assignment_id']),
        recorded_condition=case['recorded_condition'],target_condition=case['target_condition'],
        affected_quantity=_quantity(case['affected_quantity']),tracking_mode=case['tracking_mode'],
        quantity_scale=case['quantity_scale'],allow_fraction=case['allow_fraction'],
        inbound_line_id=_id(case['inbound_line_id']),source_account_id=_id(case['source_account_id']),
        frozen_account_id=_id(case['frozen_account_id']),quantity=_quantity(case['quantity']),
        serial_ids=[str(s) for s in serials],previous_event_id=_id(event['previous_event_id']),
        decision_event_id=_id(event['decision_event_id']),movement_type=event['movement_type'],
        from_account_id=_id(event['from_account_id']),to_account_id=_id(event['to_account_id']))


def command_document(case, event, *, plan_hash, evidence, previous_request_hash):
    files = sorted(evidence,key=lambda f:str(f['file_id']))
    if len({f['file_id'] for f in files}) != len(files):
        raise ValueError('duplicate evidence reference')
    return dict(schema_version='condition_command/1',action=event['kind'],case_id=_id(case['id']),
        event_id=_id(event['id']),request_id=event['request_id'],actor=actor_document(event),
        idempotency_key_hash=event['idempotency_key_hash'],expected_plan_hash=plan_hash,
        previous_event_id=_id(event['previous_event_id']),previous_request_hash=previous_request_hash,
        reason=event['reason'],evidence=[dict(file_id=_id(f['file_id']),metadata_sha256=f['metadata_sha256']) for f in files])


@dataclass(frozen=True)
class Identity:
    plan: dict
    plan_hash: str
    command: dict
    request_hash: str


def identity(case, event, *, serial_ids, evidence, previous_request_hash):
    plan = plan_document(case,event,serial_ids)
    plan_hash = posting._canonical_hash(plan)
    command = command_document(case,event,plan_hash=plan_hash,evidence=evidence,previous_request_hash=previous_request_hash)
    return Identity(plan,plan_hash,command,posting._canonical_hash(command))


def inventory_command(event, serial_ids):
    if event['kind'] not in ('submit','execute','release'):
        raise ValueError('review events cannot post inventory')
    if event['created_at'].tzinfo is None or event['created_at'].utcoffset() is None:
        raise ValueError('aware posting time required')
    serials = tuple(sorted(serial_ids,key=str))
    if len(set(serials)) != len(serials):
        raise ValueError('duplicate posting serial')
    key = f"stock-condition:posting:{event['id']}:{event['idempotency_key_hash']}"
    return posting.InventoryPostingCommand(transaction_no='INV-COND-'+event['idempotency_key_hash'][:24].upper(),
        movement_type=event['movement_type'],source_document_type='stock_condition_event',source_document_id=str(event['id']),
        posting_key=key,effective_at=event['created_at'],movements=(posting.InventoryMovementCommand(
            from_account_id=event['from_account_id'],to_account_id=event['to_account_id'],
            quantity=event['quantity'],serial_ids=serials),))


def inventory_identity(event, serial_ids):
    command = inventory_command(event,serial_ids)
    document = dict(operation='post',actor=actor_document(event),command=posting._posting_document(command))
    return command, posting._storage_hash(command.posting_key), posting._canonical_hash(document)
