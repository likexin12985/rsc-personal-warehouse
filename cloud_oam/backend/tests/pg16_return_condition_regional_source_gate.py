"""Selected-source proof as a real regional API actor, on preserved old history.

Called inside the owned authority gate's temporary-grant transaction. No
business writes or synthetic history verifier; role switches are test plumbing.
"""
from uuid import UUID
from sqlalchemy import event, select, text

from app.formal_access import load_formal_principal
from app.foundation_models import Permission, RolePermission
from app.stock_operation_models import StockOperationReturnInbound
from app.formal_services import stock_return_inbound_recovery
from app.formal_services.inventory_query import InventoryReadError
from app.formal_services.inventory_posting import InventoryPostingError
from app.formal_services.stock_loss_corrections import (
    return_condition_submission_source as subject, return_history,
)


def run(db, connection, *, actor, role, submit_link_id, original, source):
    assert actor.role_codes == ('provincial_manager',), actor.role_codes
    inbound_line_id = UUID(source['selection']['inbound_line_id'])
    denied = []

    def inspect(supplied=actor, headquarters=False, original_request=False):
        db.flush()
        db.execute(text('SET LOCAL ROLE star_oam_api'))
        statements = []
        def capture(_c, _cur, sql, _p, _ctx, _many):
            statements.append(sql.lstrip().split()[0].upper())
        event.listen(connection, 'before_cursor_execute', capture)
        try:
            if original_request:
                header = db.get(StockOperationReturnInbound, UUID(original['inboundId']), populate_existing=True)
                return stock_return_inbound_recovery.lookup_return_inbound_request(db, actor=supplied,
                    receipt_id=header.receipt_id, request_id=original['inboundRequestId'])
            if headquarters:
                return return_history.read(db, actor=supplied, root_disposition_id=UUID(original['rootDispositionId']))
            result = subject.inspect_submission_source(db, actor=supplied, inbound_line_id=inbound_line_id)
            assert not db.new and not db.dirty and not db.deleted
            return result.document
        finally:
            event.remove(connection, 'before_cursor_execute', capture)
            db.execute(text('SET LOCAL ROLE star_oam_migrator'))
            assert statements and set(statements) == {'SELECT'}, statements

    value = inspect()
    recovered = inspect(original_request=True)
    assert recovered['request_hash'] == original['inboundRequestHash']
    assert value['schema_version'] == 'return_condition_submission_source/1'
    assert value['actor_user_id'] == actor.user_id
    assert value['submission_permission_checked'] and not value['posting_allowed'] and not value['correction_authorized']
    exceptions = {'schema_version','actor_user_id','actor_person_id','authorization_version','submission_permission_checked'}
    assert {k:v for k,v in value.items() if k not in exceptions} == {
        k:v for k,v in source.items() if k not in exceptions}
    assert value['selection']['root_disposition_id'] == original['rootDispositionId']
    read_link = db.scalar(select(RolePermission).join(Permission).where(
        RolePermission.role_id == role.id, Permission.resource == 'inventory',
        Permission.action == 'read', Permission.field_code == ''))
    assert read_link is not None and read_link.effect == 'allow'
    read_link_id = read_link.id
    for change in ('submission_denied','inventory_denied','headquarters_history','other_actor'):
        nested = db.begin_nested()
        try:
            if change == 'submission_denied':
                db.get(RolePermission, submit_link_id).effect = 'deny'
            elif change == 'inventory_denied':
                db.get(RolePermission, read_link_id).effect = 'deny'
            supplied = load_formal_principal(db, original['administratorUserId']) if change == 'other_actor' else actor
            try:
                inspect(supplied, headquarters=change == 'headquarters_history')
            except (InventoryReadError, InventoryPostingError) as error:
                denied.append(dict(change=change, code=error.code))
            else:
                raise AssertionError('source boundary did not reject '+change)
        finally:
            nested.rollback(); db.expire_all()
        assert inspect() == value
    return dict(passed=True, actorRoles=list(actor.role_codes), realApiRole=True,
        actualOldApplicationHistory=True, historicalVerifierMocked=False, originalInboundRecoveredAfterPreparation=True,
        selectedEvidence=value, denied=denied, readStatementsOnly=True,
        writeAuthorizationProvided=False, physicalEvidenceVerified=False, productionAcceptance=False)
