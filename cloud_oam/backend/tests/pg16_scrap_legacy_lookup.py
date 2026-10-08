"""Old approval outcomes and new-key collisions in actual API READ ONLY."""
from uuid import uuid4
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from app.foundation_models import Permission, RolePermission
from app.formal_access import load_formal_principal
from app.formal_services.inventory_query import InventoryReadError
from app.formal_services.stock_scrap import legacy_request_lookup
from pg16_stock_scrap_structure_gate import original_columns, facts


def exercise(context):
    owner, api = (context['engines'][name] for name in ('star_oam_migrator','star_oam_api'))
    command, expected = context['legacy_approval_lookup_case']
    with Session(owner) as db:
        permission = select(Permission.id).where(Permission.resource == 'stock_operation',
            Permission.action == 'approve_loss_correction', Permission.field_code == '')
        grants = list(db.scalars(select(RolePermission).where(RolePermission.permission_id.in_(permission))))
        assert len(grants) == 1
        db.delete(grants[0]); db.commit()
    with owner.connect() as db:
        columns = original_columns(db)
        before = facts(db, columns)
    with Session(api) as db:
        db.execute(text('SET TRANSACTION READ ONLY'))
        actor = load_formal_principal(db, context['admin_id'])
        assert not any(g.action == 'approve_loss_correction' for g in actor.entitlements)
        result = legacy_request_lookup.lookup(db, actor=actor, request=command)
        assert result['request_state'] == 'found' and result['result'] == expected and result['retry_allowed'] is False
        absent = command.model_copy(update=dict(request_id=uuid4().hex,idempotency_key=uuid4().hex))
        result = legacy_request_lookup.lookup(db, actor=actor, request=absent)
        assert result['request_state'] == 'not_found' and result['retry_allowed'] is False
    new_commands = [context['scrap_lookup_cases'][0][1], context['scrap_recovery_lookup_cases'][0][1],
        context['original_closed_request'][1],
        next(c for _,c,_ in context['closed_scrap_requests'] if getattr(c,'action',None)=='apply_scrap_recovery')]
    assert context['scrap_recovery_lookup_cases'][0][0] != context['admin_id'], 'cross-actor key proof required'
    for new in new_commands:
        with Session(api) as db:
            db.execute(text('SET TRANSACTION READ ONLY'))
            actor = load_formal_principal(db, context['admin_id'])
            reused = command.model_copy(update=dict(request_id=uuid4().hex,idempotency_key=new.idempotency_key))
            try:
                legacy_request_lookup.lookup(db, actor=actor, request=reused)
            except InventoryReadError as error:
                assert error.status_code == 503 and error.code == 'loss_request_binding_outcome_unknown'
            else:
                raise AssertionError('new binding became a clean old-command absence')
    with owner.connect() as db:
        assert facts(db, columns) == before
    print('legacy READ ONLY outcome and cross-actor new-registry unknown PASS', flush=True)
    return dict(historicalApprovalFoundAfterWriteGrantRemoved=True,cleanAbsenceNotReplayable=True,
        newRegistryCollisionsUnknown=2,newSealCollisionsUnknown=2,fullDatabaseSnapshotUnchanged=True)
