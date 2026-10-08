"""Original and corrected scrap readback on actual candidate transactions."""
from uuid import uuid4
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from app.formal_access import load_formal_principal
from app.foundation_models import Permission, RolePermission
from app.stock_scrap_schemas import ScrapRequestLookup
from app.formal_services.inventory_query import InventoryReadError
from app.formal_services.stock_scrap import request_lookup
from pg16_stock_scrap_structure_gate import original_columns, facts


def exercise(context):
    owner, api = (context['engines'][name] for name in ('star_oam_migrator', 'star_oam_api'))
    cases = context['scrap_lookup_cases']
    assert len(cases) == 2 and {c.source.kind for _, c, _ in cases} == {'original', 'correction'}
    with Session(owner) as db:
        permissions = select(Permission.id).where(Permission.resource == 'stock_operation',
            Permission.action.in_(('dispose_loss', 'correct_loss')))
        grants = list(db.scalars(select(RolePermission).where(RolePermission.permission_id.in_(permissions))))
        assert len(grants) == 2
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
            assert not any(g.action in ('dispose_loss', 'correct_loss') for g in actor.entitlements)
            request = ScrapRequestLookup(operator_person_id=actor.person_id, original=command)
            result = request_lookup.lookup(db, actor=actor, request=request)
            assert result['request_state'] == 'found' and result['result'] == expected and result['retry_allowed'] is False
            absent = command.model_copy(update=dict(request_id=uuid4().hex, idempotency_key=uuid4().hex))
            result = request_lookup.lookup(db, actor=actor, request=request.model_copy(update={'original': absent}))
            assert result['request_state'] == 'not_found' and result['result'] is None and result['retry_allowed'] is False
            for field in ('execution_reason', 'idempotency_key', 'expected_plan_hash'):
                changed = command.model_copy(update={field: 'f'*64 if field == 'expected_plan_hash' else uuid4().hex})
                try:
                    request_lookup.lookup(db, actor=actor, request=request.model_copy(update={'original': changed}))
                except InventoryReadError as error:
                    assert error.code == 'stock_scrap_request_conflict'
                else:
                    raise AssertionError('changed original scrap request accepted: ' + field)
        print('native READ ONLY ' + command.source.kind + ' scrap after two recoveries PASS', flush=True)
    with owner.connect() as db:
        assert facts(db, columns) == before
    return dict(exactHistoricalScrapRequestsFound=2,absentRequestsNotReplayable=2,
        changedRequestsRejected=6,nativeReadOnlyAfterWriteGrantsRemoved=True,fullDatabaseSnapshotUnchanged=True)
