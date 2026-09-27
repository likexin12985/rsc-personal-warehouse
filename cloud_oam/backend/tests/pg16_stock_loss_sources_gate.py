"""Loss-source checks over real API-role personal opening facts on owned PG16.

The caller must provide a fresh disposable cluster. The future loss permission
is a synthetic fixture; this does not seed or authorize production operation.
"""
import datetime
import pytest
from sqlalchemy import select, text, event
from sqlalchemy.orm import Session
from app.foundation_models import Role
from app.formal_access import load_formal_principal
from pg16_opening_publication_fixture import prepare_stocktake_inventory
from pg16_opening_fixture_gate import all_reconciliation_facts
from test_formal_access import make_organization, make_user, assign
import test_postgresql16_release_gate as gate


def run(engines, *, tracking, after_preview=None):
    if tracking not in ('quantity', 'serial'):
        raise ValueError('tracking must be quantity or serial')
    owner, api, edge = (engines[k] for k in ('star_oam_migrator', 'star_oam_api', 'edge_inbox'))
    with owner.connect() as db:
        assert db.scalar(text('SELECT current_database()')) == 'rsc_pg16_release_gate'
        assert db.scalar(text('SELECT current_user')) == 'star_oam_migrator'
        assert int(db.scalar(text('SHOW server_version_num')))//10000 == 16
        assert not db.scalar(text('SELECT EXISTS (SELECT 1 FROM inventory_transactions)'))
        assert not db.scalar(text('SELECT EXISTS (SELECT 1 FROM users)'))
    result = dict(tracking=tracking, passed=False, productionAcceptance=False)
    with Session(owner) as db:
        hq = make_organization(db, name='Synthetic loss HQ')
        region = make_organization(db, name='Synthetic loss region', parent=hq)
        admin, _ = make_user(db, hq, name='Synthetic loss admin')
        manager, _ = make_user(db, region, name='Synthetic loss manager')
        roles = {r.code: r for r in db.scalars(select(Role))}
        assign(db, admin, roles['admin'], scope_type='national', scope_id='*')
        assign(db, manager, roles['provincial_manager'], scope_type='organization', scope_id=str(region.id))
        db.commit()
        admin_id, manager_id, hq_id = admin.id, manager.id, hq.id
    fixture = prepare_stocktake_inventory(owner, edge, actor_user_id=admin_id, assignee_user_id=manager_id, control_material=tracking)
    if tracking == 'serial':
        fixture = dict(fixture, material_id=fixture['concurrency_material_id'], material_sku_code=fixture['concurrency_material_sku_code'], selected_serial_no=fixture['concurrency_serial_no'])
    from datetime import timedelta
    from uuid import uuid4
    from app.inventory_models import StockLocation, StockAccount, CustodyAssignment
    from app.foundation_models import Permission, RolePermission
    from app.models import User
    from pg16_personal_stock_fixture import establish_personal_stock
    from app.formal_services import stock_loss_sources as loss
    from app.formal_services.inventory_query import InventoryReadError
    from app.formal_services.inventory_posting import InventoryPostingError
    from app.stock_loss_schemas import StockLossSelectionIn, StockLossSelectionLineIn
    with Session(owner) as db:
        engineer, person = make_user(db, db.get(type(hq), fixture['region_org_id']), name='Synthetic loss engineer')
        assign(db, engineer, db.scalar(select(Role).where(Role.code=='technician')), scope_type='person', scope_id=str(person.id))
        person_id, engineer_id = person.id, engineer.id
        reviewer, _ = make_user(db, db.get(type(hq), hq_id), name='Synthetic source reviewer')
        assign(db, reviewer, db.scalar(select(Role).where(Role.code=='admin')), scope_type='national', scope_id='*')
        now = datetime.datetime.now(datetime.timezone.utc)
        location = StockLocation(id=uuid4(), code='LOSS-PERSONAL-'+uuid4().hex,
            name='Synthetic loss personal warehouse', location_type='personal',
            owner_org_id=fixture['region_org_id'], parent_id=fixture['location_id'],
            custodian_person_id=person_id, status='active')
        db.add(location); db.flush()
        db.add(CustodyAssignment(location_id=location.id, custodian_person_id=person_id,
            valid_from=now-timedelta(days=1)))
        account = StockAccount(id=uuid4(), owner_org_id=fixture['region_org_id'],
            custodian_person_id=person_id, location_id=location.id, material_id=fixture['material_id'],
            condition_code='new', availability_bucket='available')
        db.add(account)
        permission = Permission(resource='stock_operation', action='submit_loss', field_code='',
            description='Synthetic future loss permission; local test database only')
        db.add(permission); db.flush()
        grant = RolePermission(role_id=db.scalar(select(Role.id).where(Role.code=='technician')),
            permission_id=permission.id, effect='allow')
        db.add(grant); db.commit()
        account_id, grant_id = account.id, grant.id
        fixture = dict(fixture, difference_peer_location_id=location.id,
            difference_peer_account_id=account_id, reconciliation_reviewer_id=reviewer.id)
    with Session(api) as db:
        actor = load_formal_principal(db, engineer_id)
        with pytest.raises(InventoryReadError) as caught:
            loss.loss_sources(db, actor=actor)
        assert caught.value.code == 'stock_loss_opening_required'
    result['openingRequired'] = True
    establish_personal_stock(api, fixture, admin=admin_id, manager=manager_id, engineer=engineer_id, reviewer=fixture['reconciliation_reviewer_id'])
    from app.formal_services import formal_files, stock_loss_plan
    from app.foundation_models import FileObject
    from app.stock_loss_schemas import StockLossPreviewIn
    from test_formal_files_service import FakeStorage, SECRET
    storage = FakeStorage()
    with Session(api) as db:
        actor = load_formal_principal(db, engineer_id)
        evidence = formal_files.create_file_upload_intent(db, actor=actor,
            command=formal_files.FileUploadIntentInput(purpose='stock_loss_evidence',
                original_filename='synthetic-loss.jpg', size_bytes=128, mime_type='image/jpeg', sha256='a'*64),
            idempotency_key=uuid4().hex, idempotency_hmac_secret=SECRET, trace_request_id=uuid4().hex,
            storage=storage, upload_ttl_seconds=60)
        evidence_id = evidence.file_id
        storage.materialize(db.get(FileObject, evidence_id))
        formal_files.complete_file_upload(db, actor=actor, file_id=evidence_id,
            trace_request_id=uuid4().hex, storage=storage)
        db.commit()
    before = all_reconciliation_facts(owner)
    statements = []
    def require_query_only(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement.split()[0].upper())
        assert statement.lstrip().upper().startswith(('SELECT ', 'SAVEPOINT ', 'RELEASE SAVEPOINT ', 'ROLLBACK TO SAVEPOINT ')), 'source preview emitted a non-query statement'
    event.listen(api, 'before_cursor_execute', require_query_only)
    try:
        with Session(api) as db:
            assert db.scalar(text('SELECT current_user')) == 'star_oam_api'
            actor = load_formal_principal(db, engineer_id)
            choices = loss.loss_sources(db, actor=actor)
            assert len(choices.items)==1 and choices.items[0].stock_account_id==account_id
            assert choices.items[0].quantity=='1.000'
            request = StockLossSelectionIn(operator_person_id=actor.person_id,
                lines=(StockLossSelectionLineIn(stock_account_id=account_id, quantity='1' if tracking=='serial' else '0.250', serial_verifications=(dict(serial_id=fixture['concurrency_serial_id'], sku_code=fixture['concurrency_material_sku_code'], serial_no=fixture['concurrency_serial_no'], qr_code=fixture['concurrency_serial_qr_code']),) if tracking=='serial' else ()),))
            preview = loss.preview_selection(db, actor=actor, request=request)
            assert preview.planning_status=='source_selection_only' and preview.lines[0].selected_quantity==('1.000' if tracking=='serial' else '0.250') and len(preview.lines[0].selected_serials)==int(tracking=='serial')
            assert preview.basis_hash==loss.preview_selection(db, actor=actor, request=request).basis_hash
            command_request = StockLossPreviewIn(**request.model_dump(), reason='Synthetic physical loss requires independent review',
                                                 evidence_file_ids=(evidence_id,))
            command_preview, document = stock_loss_plan.preview_loss(db, actor=actor, request=command_request)
            repeat, _ = stock_loss_plan.preview_loss(db, actor=actor, request=command_request)
            assert command_preview.plan_hash == repeat.plan_hash and command_preview.planning_status=='preview_only'
            assert command_preview.evidence[0].file_id==evidence_id and document['evidence'][0]['metadata_sha256']
            assert 'storage_key' not in command_preview.model_dump_json() and 'qr_code' not in command_preview.model_dump_json()
            with pytest.raises(InventoryReadError) as invalid_file:
                stock_loss_plan.preview_loss(db, actor=actor,
                    request=command_request.model_copy(update={'evidence_file_ids':(uuid4(),)}))
            assert invalid_file.value.code=='stock_loss_evidence_invalid'
            result['wholeCommandPreviewWithCompletedEvidence'] = True
            rejected = []
            for field in (('sku_code', 'serial_no', 'qr_code', 'serial_id') if tracking=='serial' else ()):
                value = request.model_dump(mode='json')
                value['lines'][0]['serial_verifications'][0][field] = str(uuid4()) if field=='serial_id' else 'WRONG'
                with pytest.raises(InventoryReadError):
                    loss.preview_selection(db, actor=actor, request=StockLossSelectionIn.model_validate(value))
                rejected.append(field)
            result['invalidSerialProofsRejected'] = rejected
            db.commit()
    finally:
        event.remove(api, 'before_cursor_execute', require_query_only)
    assert statements and set(statements)=={'SELECT'}
    result['apiRoleNoBusinessWritesPreview'] = True
    result['readStatementCount'] = len(statements)
    result['existingOpeningProofRowLocksRequired'] = True
    with Session(owner) as db:
        db.get(RolePermission, grant_id).effect = 'deny'
        db.commit()
    with Session(api) as db:
        with pytest.raises((InventoryReadError, InventoryPostingError)):
            loss.preview_selection(db, actor=actor, request=request)
    result['liveGrantRevocationRejected'] = True
    assert all_reconciliation_facts(owner)==before
    result.update(passed=True, factsUnchanged=True, migrationHead=gate.HEAD_REVISION,
                  syntheticPermissionOnly=True, snPG16PreviewProved=tracking=='serial')
    if after_preview is not None:
        with Session(owner) as db:
            db.get(RolePermission, grant_id).effect = 'allow'
            db.commit()
        result['submission'] = after_preview(dict(engines=engines, tracking=tracking,
            engineer_id=engineer_id, admin_id=admin_id, person_id=person_id, account_id=account_id,
            location_id=location.id, grant_id=grant_id, request=command_request))
    return result
