"""Private transaction-bound authority for initial condition freeze only.

The regional custodian can belong to headquarters; account access therefore
uses the exact asset owner proved by this case, not that person's home office.
There is no general region posting bypass or approval/execution permit here.
"""
from dataclasses import dataclass
from app.inventory_models import StockAccount
from app.formal_services import inventory_posting as posting
from . import return_condition_authority as authority

KEY='stock_condition.live_submission_permit'


@dataclass(frozen=True)
class SubmissionPermit:
    transaction: object
    actor: object
    admission: object
    command: object
    event: dict
    source_cursor: int
    key_hash: str
    request_hash: str


def invalid():
    posting._fail('condition_posting_authority_invalid','precondition_failed',
        '成色纠正冻结必须由专用服务绑定本次事务与准确原入库')


def issue(db, *, admission, command, event, source_cursor, key_hash, request_hash):
    if KEY in db.info:
        invalid()
    permit=SubmissionPermit(db.get_nested_transaction() or db.get_transaction(),admission.actor,
        admission,command,dict(event),source_cursor,key_hash,request_hash)
    db.info[KEY]=permit
    return permit


def require(db, *, actor, command, permit, permission_resource, permission_action,
        reversed_transaction_id, opening_task_id, current_cursor, idempotency_key_hash,
        request_hash, request_reference, occurred_at, event_suffix,
        receipt_authority, scrap_authority, scrap_recovery_authority):
    if (type(permit) is not SubmissionPermit or db.info.get(KEY) is not permit or permit.transaction is None
            or (db.get_nested_transaction() or db.get_transaction()) is not permit.transaction
            or not permit.transaction.is_active or permit.actor!=actor or permit.command!=command
            or command.source_document_type!='stock_condition_event' or permit.event['kind']!='submit'
            or command.source_document_id!=str(permit.event['id']) or command.movement_type!='freeze'
            or permission_resource!='stock_operation' or permission_action!=authority.ACTIONS['submit']
            or reversed_transaction_id is not None or opening_task_id is not None
            or any(v is not None for v in (receipt_authority,scrap_authority,scrap_recovery_authority))
            or current_cursor!=permit.source_cursor or idempotency_key_hash!=permit.key_hash
            or request_hash!=permit.request_hash or request_reference!=posting._request_reference(permit.event['request_id'])
            or occurred_at!=permit.event['created_at'] or event_suffix!='posted' or len(command.movements)!=1):
        invalid()
    current=authority.authorize_submission(db,actor=actor,inbound_line_id=permit.admission.inbound_line_id)
    if current!=permit.admission:
        invalid()
    move=command.movements[0]
    source=db.get(StockAccount,current.source_account_id,populate_existing=True)
    held=db.get(StockAccount,move.to_account_id,populate_existing=True)
    if (source is None or held is None or move.from_account_id!=source.id or held.id==source.id
            or held.availability_bucket!='frozen' or source.availability_bucket!='available'
            or any(getattr(source,k)!=getattr(held,k) for k in (
                'owner_org_id','custodian_person_id','location_id','material_id','lot_id','condition_code'))):
        invalid()
    return {source.id:source,held.id:held}


def discard(db, permit):
    if db.info.get(KEY) is permit:
        db.info.pop(KEY,None)
