"""All actual original recovery commands reread after two successor generations."""
from uuid import uuid4
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from app.formal_access import load_formal_principal
from app.foundation_models import Permission, RolePermission
from app.stock_scrap_recovery_schemas import ScrapRecoveryRequestLookup
from app.formal_services.inventory_query import InventoryReadError
from app.formal_services.stock_scrap import recovery_lookup, recovery_authority
from pg16_stock_scrap_structure_gate import original_columns, facts


def exercise(context):
    owner, api = (context['engines'][name] for name in ('star_oam_migrator', 'star_oam_api'))
    cases = context['scrap_recovery_lookup_cases']
    assert len(cases) == 8
    # Read recovery must survive removal of the write capability. Change only
    # this runner's owned synthetic grants, after all physical writes finish.
    with Session(owner) as db:
        permissions = select(Permission.id).where(Permission.resource == 'stock_operation',
            Permission.action.in_(tuple(recovery_authority.ACTIONS.values())))
        grants = list(db.scalars(select(RolePermission).where(RolePermission.permission_id.in_(permissions))))
        assert len(grants) == 4
        for grant in grants:
            db.delete(grant)
        db.commit()
    with owner.connect() as db:
        columns = original_columns(db)
        before = facts(db, columns)
    for actor_id, command, expected in cases:
        with Session(api) as db:
            db.execute(text('SET TRANSACTION READ ONLY'))
            actor = load_formal_principal(db, actor_id)
            assert not any(g.action in recovery_authority.ACTIONS.values() for g in actor.entitlements)
            request = ScrapRecoveryRequestLookup(operator_person_id=actor.person_id, original=command)
            actual = recovery_lookup.lookup(db, actor=actor, request=request)
            assert actual['request_state'] == 'found' and actual['result'] == expected and actual['retry_allowed'] is False
            absent = command.model_copy(update=dict(request_id=uuid4().hex, idempotency_key=uuid4().hex))
            missing = recovery_lookup.lookup(db, actor=actor, request=request.model_copy(update={'original': absent}))
            assert missing['request_state'] == 'not_found' and missing['result'] is None and missing['retry_allowed'] is False
            for field in ('reason', 'idempotency_key'):
                changed = command.model_copy(update={field: uuid4().hex})
                try:
                    recovery_lookup.lookup(db, actor=actor, request=request.model_copy(update={'original': changed}))
                except InventoryReadError as error:
                    assert error.code == 'stock_scrap_recovery_request_conflict'
                else:
                    raise AssertionError('changed original request accepted: ' + field)
        print('native READ ONLY original recovery ' + command.action + ' after successors PASS', flush=True)
    with owner.connect() as db:
        assert facts(db, columns) == before, 'read-only recovery changed database facts'
    return dict(historicalRecoveryRequestsFound=8,absentRecoveryRequestsNotReplayable=8,
        changedRecoveryRequestsRejected=16, nativeReadOnlyAfterWriteGrantsRemoved=True,
        retainedDatabaseSnapshotUnchanged=True)
