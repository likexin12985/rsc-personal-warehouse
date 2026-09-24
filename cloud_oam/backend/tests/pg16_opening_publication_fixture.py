"""Signed, reviewed opening fixtures for CI or a newly owned native PG16.

Only external synthetic source/storage transports are substituted. Current
publication, authorization, database guards and stock services remain real.
"""
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import uuid

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app import inventory_control_authority as authority
from app import inventory_control_projection as controls
from app import material_capture_ingress as ingress
from app import material_projection as materials
from app import material_source_authority as material_authority
from app.foundation_models import AuditChainHead, ExternalObject, ExternalObjectVersion, SourceSystem
from app.formal_access import load_formal_principal
from app.inventory_models import FormalMaterial, MaterialInventoryPolicy
from app.inventory_control_projection_models import ControlProjectionPublication, ControlProjectionLine
from app.material_capture_models import MaterialCaptureBinding
from app.material_projection_models import MaterialProjectionLine
from app.models import AuthSession
from app.routers import integrations
from app.security import create_access_token
from source_configuration_file_fixtures import source_evidence
from test_material_capture_ingress import SECRET, KEY, packet, proof
from test_material_projection import command as material_command
from test_material_source_authority import command as material_grant_command
from test_inventory_control_authority import command as control_grant_command
from test_inventory_control_mapping import command as mapping_command
from test_inventory_control_projection import publication_command
from pg16_inventory_control_normalization_gate import _capture_pipeline, _configuration, _mapping, RULES
from cloud_oam.edge_sync import oam_material_master_capture as material_capture
from cloud_oam.edge_sync.test_inventory_control_capture import expected, row as control_row


def _publish_materials(db, edge_engine, *, actor_user_id, source_id, region_id):
    assert db.execute(text('SELECT current_user,session_user')).one()==('star_oam_migrator','star_oam_migrator')
    assert int(db.scalar(text('SHOW server_version_num')))//10000==16
    actor=load_formal_principal(db,actor_user_id)
    now=authority._now(db);instance='synthetic-opening-'+uuid.uuid4().hex
    binding=MaterialCaptureBinding(source_system_id=source_id,source_instance=instance,key_id=KEY,
        key_fingerprint=ingress.key_fingerprint(SECRET,instance),created_at=now,valid_from=now,valid_to=now+timedelta(hours=1))
    auth=AuthSession(user_id=actor.user_id,refresh_token_hash=uuid.uuid4().hex+uuid.uuid4().hex,client_type='web',
        device_id=uuid.uuid4().hex,ip_address='hmac:1:'+'a'*64,created_at=now,expires_at=now+timedelta(hours=1))
    db.add_all((binding,auth))
    if db.scalar(select(AuditChainHead).where(AuditChainHead.stream_key=='authorization')) is None:
        db.add(AuditChainHead(stream_key='authorization',version=0))
    db.commit()
    evidence,_=source_evidence(db,actor.user_id);db.commit()
    world=SimpleNamespace(actor=actor,file=evidence.id,mapping_file=evidence.id,source=source_id,region=region_id,
        binding=binding.id,material_binding=binding.id,material_file=evidence.id)
    world.login=dict(access_token=create_access_token(actor.user_id,auth.id),expected_authorization_version=actor.authorization_version)
    cmd=material_grant_command(db,world)
    review=material_authority.preview_material_source_authority(db,**world.login,command=cmd)
    grant=material_authority.execute_material_source_authority(db,**world.login,command=cmd,review_sha256=review['review_sha256'])
    db.commit();world.material_grant=uuid.UUID(grant['decision_id'])
    codes=('PG16-STK-SKU-'+uuid.uuid4().hex.upper(),'PG16-CONCURRENT-SKU-'+uuid.uuid4().hex.upper())
    rows=[dict(materialCode=code,materialName='Synthetic opening '+kind,unitCode='EA',unitName='piece',materialStatus=0)
          for code,kind in zip(codes,('quantity','serial'))]
    def read_page(path,query):
        assert path=='/material_type/spare_list' and query==dict(page=1,size=1000)
        return dict(success=True,model=dict(amount=len(rows),result=rows))
    settings=integrations.settings.model_copy(update=dict(edge_sync_enabled=True,edge_sync_secret=SECRET,
        edge_sync_allowed_sources=instance,edge_material_capture_enabled=True,edge_material_capture_key_id=KEY,
        edge_sync_legacy_batches_enabled=False,edge_sync_legacy_personnel_projection_enabled=False))
    # Capture and actual HMAC verification run as the restricted receiver.
    with pytest.MonkeyPatch.context() as patch,Session(edge_engine) as edge_db:
        patch.setattr(integrations,'settings',settings)
        capture=material_capture.collect(source_instance=instance,read_page=read_page,clock=lambda:authority._now(edge_db))
        result=integrations.receive_material_master_capture(proof(packet(capture),source=instance),edge_db)
    receipt_id=uuid.UUID(result['receipt_id'])
    draft=material_command(db,world,receipt_id).model_dump()
    for row in draft['decisions']:
        row.update(tracking_mode='none' if row['sku_code']==codes[0] else 'serial',
                   quantity_scale=3 if row['sku_code']==codes[0] else 0,allow_fraction=row['sku_code']==codes[0])
    cmd=materials.MaterialPublicationCommand.model_validate(draft)
    review=materials.preview_material_publication(db,**world.login,command=cmd)
    result=materials.execute_material_publication(db,**world.login,command=cmd,review_sha256=review['review_sha256']);db.commit()
    lines={line.sku_code:line for line in db.scalars(select(MaterialProjectionLine).where(
        MaterialProjectionLine.publication_id==uuid.UUID(result['publication_id'])))}
    assert set(lines)==set(codes)
    world.code=codes[0];world.material_publication_id=uuid.UUID(result['publication_id'])
    world.material_id=lines[codes[0]].material_id;world.policy_id=lines[codes[0]].policy_id
    catalog=expected();catalog['binding']['source_instance']=instance
    world.catalog_document=catalog
    db.execute(text('''INSERT INTO public.oam_sync_scope_bindings
        (id,principal_name,capability,source_system,source_instance,scope_key,company_id,org_code,entity_type)
        VALUES (:id,'edge_inbox','edge_ingress',:source_system,:source_instance,:scope_key,:company_id,:org_code,'inventory')'''),
        dict(id=uuid.uuid4(),**catalog['binding']))
    db.commit()
    return world,lines[codes[0]],lines[codes[1]]


def _publish_control(owner, edge_engine, world):
    """Keep one explicit zero-quantity control row, not an empty shortcut."""
    row=control_row('opening-control',materialStatus='usable',materialStockType='stock')
    row.update(materialCode=world.code,qtyStock='0.000')
    with pytest.MonkeyPatch.context() as patch,TemporaryDirectory(prefix='pg16-opening-capture-') as folder:
        capture=_capture_pipeline(owner,edge_engine,world,Path(folder),patch)
        capture([row],full=True)
        with Session(owner) as db:
            source=_configuration(db,world.login,control_grant_command(db,world));db.commit()
            _configuration(db,world.login,control_grant_command(db,world,action='catalog_grant',grant=source));db.commit()
            mapping=_mapping(db,world.login,mapping_command(db,world,rules=RULES));db.commit()
            world.mapping=uuid.UUID(mapping['decision_id'])
        # The admitted full capture starts after source/catalog/rule grants.
        capture([row],full=True)
        with Session(owner) as db:
            cmd=publication_command(db,world,mapping)
            review=controls.preview_control_publication(db,**world.login,command=cmd)
            result=controls.execute_control_publication(db,**world.login,command=cmd,review_sha256=review['review_sha256']);db.commit()
            pub=db.get(ControlProjectionPublication,uuid.UUID(result['publication_id']))
            line=db.scalars(select(ControlProjectionLine).where(ControlProjectionLine.publication_id==pub.id)).one()
            value=controls._line_input(line)
            assert result['record_count']==result['origin_count']==1
            assert value.control_qty==Decimal('0') and value.material_id==world.material_id
            return dict(control_publication_id=pub.id,control_lines=(value,),control_source_system_id=pub.source_system_id,
                control_sync_run_id=pub.sync_run_id,control_scope_key='oam_inventory_control:region:'+str(world.region),
                material_publication_id=world.material_publication_id)


def prepare_stocktake_inventory(owner, edge_engine, *, actor_user_id, assignee_user_id):
    """Publish two SKU policies and one control row; never seed balances."""
    from app.foundation_models import Organization, Person, Role, RoleAssignment
    from app.inventory_models import (
        CustodyAssignment,
        FormalMaterial,
        InventorySerial,
        MaterialInventoryPolicy,
        StockAccount,
        StockLocation,
    )
    from app.models import User

    with Session(owner, expire_on_commit=False) as session:
        now = session.scalar(select(func.now()))
        assert isinstance(now, datetime) and now.tzinfo is not None
        manager = session.get(User, assignee_user_id)
        assert manager is not None and manager.person_id is not None
        manager_person = session.get(Person, manager.person_id)
        assert manager_person is not None
        assignment_row = session.execute(
            select(RoleAssignment, Role)
            .join(Role, Role.id == RoleAssignment.role_id)
            .where(
                RoleAssignment.user_id == assignee_user_id,
                RoleAssignment.status == "active",
                RoleAssignment.scope_type == "organization",
                Role.code == "provincial_manager",
                Role.status == "active",
            )
            .order_by(RoleAssignment.id)
        ).one()
        assignment, role = assignment_row
        assert role.is_external is False
        region_org_id = uuid.UUID(assignment.scope_id)
        region = session.get(Organization, region_org_id)
        assert region is not None
        assert (region.org_type, region.status) == ("region_company", "active")

        administrator = session.get(User, actor_user_id)
        assert (
            administrator is not None
            and administrator.person_id is not None
        )
        assert session.get(Person, administrator.person_id) is not None
        headquarters_assignment_row = session.execute(
            select(RoleAssignment, Role)
            .join(Role, Role.id == RoleAssignment.role_id)
            .where(
                RoleAssignment.user_id == actor_user_id,
                RoleAssignment.status == "active",
                RoleAssignment.scope_type == "national",
                RoleAssignment.scope_id == "*",
                Role.code == "admin",
                Role.status == "active",
            )
            .order_by(RoleAssignment.id)
        ).one()
        _headquarters_assignment, headquarters_role = (
            headquarters_assignment_row
        )
        assert headquarters_role.is_external is False

        oam_source = session.scalar(
            select(SourceSystem)
            .where(SourceSystem.code == "oam")
            .order_by(SourceSystem.id)
            .limit(1)
        )
        if oam_source is None:
            oam_source = SourceSystem(
                id=uuid.uuid4(),
                code="oam",
                name="PostgreSQL 16 隔离期初控制源",
                mode="read_only",
                enabled=True,
                configuration_jsonb={},
                created_at=now - timedelta(days=2),
                updated_at=now - timedelta(days=2),
            )
            session.add(oam_source)
            session.flush()
        assert (
            oam_source.code,
            oam_source.mode,
            oam_source.enabled,
        ) == ("oam", "read_only", True)

        world, primary, secondary = _publish_materials(session, edge_engine,
            actor_user_id=actor_user_id, source_id=oam_source.id, region_id=region_org_id)
        material = session.get(FormalMaterial, primary.material_id)
        material_id = material.id
        material_external_object = session.get(ExternalObject, primary.external_object_id)
        material_external_version = session.get(ExternalObjectVersion, primary.version_id)
        policy = session.get(MaterialInventoryPolicy, primary.policy_id)
        concurrency_material = session.get(FormalMaterial, secondary.material_id)
        concurrency_policy = session.get(MaterialInventoryPolicy, secondary.policy_id)
        serial = InventorySerial(id=uuid.uuid4(), material_id=material.id,
            serial_no=f"PG16-STK-SERIAL-{material.id.hex[:16].upper()}",
            qr_code=f"PG16-STK-QR-{material.id.hex.upper()}", lot_id=None, lifecycle_status="active",
            created_at=now, updated_at=now)
        concurrency_serial = InventorySerial(id=uuid.uuid4(), material_id=concurrency_material.id,
            serial_no=f"PG16-CONCURRENT-SERIAL-{concurrency_material.id.hex[:16].upper()}",
            qr_code=f"PG16-CONCURRENT-QR-{concurrency_material.id.hex.upper()}", lot_id=None, lifecycle_status="active",
            created_at=now, updated_at=now)
        location_id = uuid.uuid4()
        location = StockLocation(
            id=location_id,
            code=f"PG16-STK-{location_id.hex[:16].upper()}",
            name="PostgreSQL 16 隔离盘点仓",
            location_type="region",
            owner_org_id=region_org_id,
            parent_id=None,
            custodian_person_id=manager_person.id,
            status="active",
            created_at=now - timedelta(days=1),
            updated_at=now - timedelta(days=1),
        )
        concurrency_location_id = uuid.uuid4()
        concurrency_location = StockLocation(
            id=concurrency_location_id,
            code=(
                "PG16-CONCURRENT-"
                f"{concurrency_location_id.hex[:16].upper()}"
            ),
            name="PostgreSQL 16 并发别名隔离仓",
            location_type="region",
            owner_org_id=region_org_id,
            parent_id=None,
            custodian_person_id=manager_person.id,
            status="active",
            created_at=now - timedelta(days=1),
            updated_at=now - timedelta(days=1),
        )
        concurrency_competing_location_id = uuid.uuid4()
        concurrency_competing_location = StockLocation(
            id=concurrency_competing_location_id,
            code=(
                "PG16-CONCURRENT-PEER-"
                f"{concurrency_competing_location_id.hex[:16].upper()}"
            ),
            name="PostgreSQL 16 并发别名竞争空仓",
            location_type="region",
            owner_org_id=region_org_id,
            parent_id=None,
            custodian_person_id=manager_person.id,
            status="active",
            created_at=now - timedelta(days=1),
            updated_at=now - timedelta(days=1),
        )
        difference_peer_location_id = uuid.uuid4()
        difference_peer_location = StockLocation(
            id=difference_peer_location_id,
            code=(
                "PG16-DIFFERENCE-PEER-"
                f"{difference_peer_location_id.hex[:16].upper()}"
            ),
            name="PostgreSQL 16 差异回放锁序空仓",
            location_type="region",
            owner_org_id=region_org_id,
            parent_id=None,
            custodian_person_id=manager_person.id,
            status="active",
            created_at=now - timedelta(days=1),
            updated_at=now - timedelta(days=1),
        )
        session.add_all(
            (
                policy,
                serial,
                location,
                concurrency_policy,
                concurrency_serial,
                concurrency_location,
                concurrency_competing_location,
                difference_peer_location,
            )
        )
        session.flush()
        session.add_all(
            (
                CustodyAssignment(
                    id=uuid.uuid4(),
                    location_id=location.id,
                    custodian_person_id=manager_person.id,
                    valid_from=now - timedelta(days=1),
                    valid_to=None,
                    handover_case_id=None,
                    created_at=now - timedelta(days=1),
                    updated_at=now - timedelta(days=1),
                ),
                CustodyAssignment(
                    id=uuid.uuid4(),
                    location_id=concurrency_location.id,
                    custodian_person_id=manager_person.id,
                    valid_from=now - timedelta(days=1),
                    valid_to=None,
                    handover_case_id=None,
                    created_at=now - timedelta(days=1),
                    updated_at=now - timedelta(days=1),
                ),
                CustodyAssignment(
                    id=uuid.uuid4(),
                    location_id=concurrency_competing_location.id,
                    custodian_person_id=manager_person.id,
                    valid_from=now - timedelta(days=1),
                    valid_to=None,
                    handover_case_id=None,
                    created_at=now - timedelta(days=1),
                    updated_at=now - timedelta(days=1),
                ),
                CustodyAssignment(
                    id=uuid.uuid4(),
                    location_id=difference_peer_location.id,
                    custodian_person_id=manager_person.id,
                    valid_from=now - timedelta(days=1),
                    valid_to=None,
                    handover_case_id=None,
                    created_at=now - timedelta(days=1),
                    updated_at=now - timedelta(days=1),
                ),
            )
        )
        account = StockAccount(
            id=uuid.uuid4(),
            owner_org_id=region_org_id,
            custodian_person_id=None,
            location_id=location.id,
            material_id=material.id,
            condition_code="new",
            availability_bucket="available",
            lot_id=None,
            created_at=now - timedelta(days=1),
            updated_at=now - timedelta(days=1),
        )
        concurrency_account = StockAccount(
            id=uuid.uuid4(),
            owner_org_id=region_org_id,
            custodian_person_id=None,
            location_id=concurrency_location.id,
            material_id=concurrency_material.id,
            condition_code="new",
            availability_bucket="available",
            lot_id=None,
            created_at=now - timedelta(days=1),
            updated_at=now - timedelta(days=1),
        )
        difference_peer_account = StockAccount(
            id=uuid.uuid4(),
            owner_org_id=region_org_id,
            custodian_person_id=None,
            location_id=difference_peer_location.id,
            material_id=material.id,
            condition_code="new",
            availability_bucket="available",
            lot_id=None,
            created_at=now - timedelta(days=1),
            updated_at=now - timedelta(days=1),
        )
        session.add_all(
            (account, concurrency_account, difference_peer_account)
        )
        session.flush()

        # A separate freeze owner for the multi-round recovery proof. The
        # difference peer above deliberately remains submitted/frozen.
        recount_location_id = uuid.uuid4()
        recount_location = StockLocation(
            id=recount_location_id,
            code=f"PG16-RECOUNT-{recount_location_id.hex[:16].upper()}",
            name="PostgreSQL 16 非期初多轮复盘隔离库位",
            location_type="region", owner_org_id=region_org_id,
            parent_id=None, custodian_person_id=manager_person.id,
            status="active", created_at=now - timedelta(days=1),
            updated_at=now - timedelta(days=1),
        )
        session.add(recount_location)
        session.flush()
        session.add(CustodyAssignment(
            id=uuid.uuid4(), location_id=recount_location_id,
            custodian_person_id=manager_person.id,
            valid_from=now - timedelta(days=1), valid_to=None,
            handover_case_id=None, created_at=now - timedelta(days=1),
            updated_at=now - timedelta(days=1),
        ))
        recount_account = StockAccount(
            id=uuid.uuid4(), owner_org_id=region_org_id,
            custodian_person_id=None, location_id=recount_location_id,
            material_id=material.id, condition_code="new",
            availability_bucket="available", lot_id=None,
            created_at=now - timedelta(days=1),
            updated_at=now - timedelta(days=1),
        )
        session.add(recount_account)
        session.flush()

        # Do not reuse the alias-race location: that fixture intentionally
        # starts a separate, not-yet-established opening task later.
        serial_replay_location_id = uuid.uuid4()
        serial_replay_location = StockLocation(
            id=serial_replay_location_id,
            code=f"PG16-SN-REPLAY-{serial_replay_location_id.hex[:16].upper()}",
            name="PostgreSQL 16 非期初串码回放隔离库位",
            location_type="region", owner_org_id=region_org_id,
            parent_id=None, custodian_person_id=manager_person.id,
            status="active", created_at=now - timedelta(days=1),
            updated_at=now - timedelta(days=1),
        )
        serial_replay_serial = InventorySerial(
            id=uuid.uuid4(), material_id=concurrency_material.id,
            serial_no=f"PG16-SN-REPLAY-{serial_replay_location_id.hex.upper()}",
            qr_code=f"PG16-SN-REPLAY-QR-{serial_replay_location_id.hex.upper()}",
            lot_id=None, lifecycle_status="active",
            created_at=now - timedelta(days=1),
            updated_at=now - timedelta(days=1),
        )
        serial_replay_extra_serial = InventorySerial(
            id=uuid.uuid4(), material_id=concurrency_material.id,
            serial_no=f"PG16-SN-REPLAY-EXTRA-{serial_replay_location_id.hex.upper()}",
            qr_code=f"PG16-SN-REPLAY-EXTRA-QR-{serial_replay_location_id.hex.upper()}",
            lot_id=None, lifecycle_status="active",
            created_at=now - timedelta(days=1),
            updated_at=now - timedelta(days=1),
        )
        session.add_all((
            serial_replay_location,
            serial_replay_serial,
            serial_replay_extra_serial,
        ))
        session.flush()
        session.add(CustodyAssignment(
            id=uuid.uuid4(), location_id=serial_replay_location_id,
            custodian_person_id=manager_person.id,
            valid_from=now - timedelta(days=1), valid_to=None,
            handover_case_id=None, created_at=now - timedelta(days=1),
            updated_at=now - timedelta(days=1),
        ))
        serial_replay_account = StockAccount(
            id=uuid.uuid4(), owner_org_id=region_org_id,
            custodian_person_id=None, location_id=serial_replay_location_id,
            material_id=concurrency_material.id, condition_code="new",
            availability_bucket="available", lot_id=None,
            created_at=now - timedelta(days=1),
            updated_at=now - timedelta(days=1),
        )
        session.add(serial_replay_account)
        session.flush()

        # A disposable non-serial peer for the real cutoff-replay
        # multi-scope sample.  It is intentionally independent from the
        # recount account (whose zero balance is asserted by the existing
        # three-round recovery proof) and from the alias-race locations.
        dynamic_peer_location_id = uuid.uuid4()
        dynamic_peer_location = StockLocation(
            id=dynamic_peer_location_id,
            code=f"PG16-DYNAMIC-PEER-{dynamic_peer_location_id.hex[:16].upper()}",
            name="PostgreSQL 16 截止回放多范围隔离库位",
            location_type="region", owner_org_id=region_org_id,
            parent_id=None, custodian_person_id=manager_person.id,
            status="active", created_at=now - timedelta(days=1),
            updated_at=now - timedelta(days=1),
        )
        session.add(dynamic_peer_location)
        session.flush()
        session.add(CustodyAssignment(
            id=uuid.uuid4(), location_id=dynamic_peer_location_id,
            custodian_person_id=manager_person.id,
            valid_from=now - timedelta(days=1), valid_to=None,
            handover_case_id=None, created_at=now - timedelta(days=1),
            updated_at=now - timedelta(days=1),
        ))
        dynamic_peer_account = StockAccount(
            id=uuid.uuid4(), owner_org_id=region_org_id,
            custodian_person_id=None, location_id=dynamic_peer_location_id,
            material_id=material.id, condition_code="new",
            availability_bucket="available", lot_id=None,
            created_at=now - timedelta(days=1),
            updated_at=now - timedelta(days=1),
        )
        session.add(dynamic_peer_account)
        session.flush()

        session.commit()
        fixture: dict[str, object] = {
            "account_id": account.id,
            "assignee_person_id": manager_person.id,
            "concurrency_account_id": concurrency_account.id,
            "concurrency_competing_location_id": (
                concurrency_competing_location.id
            ),
            "concurrency_location_id": concurrency_location.id,
            "concurrency_material_id": concurrency_material.id,
            "concurrency_material_sku_code": concurrency_material.sku_code,
            "concurrency_serial_id": concurrency_serial.id,
            "concurrency_serial_no": concurrency_serial.serial_no,
            "concurrency_serial_qr_code": concurrency_serial.qr_code,
            "deadline": now + timedelta(days=2),
            "difference_peer_account_id": difference_peer_account.id,
            "difference_peer_location_id": difference_peer_location.id,
            "recount_location_id": recount_location_id,
            "recount_account_id": recount_account.id,
            "serial_replay_location_id": serial_replay_location_id,
            "serial_replay_account_id": serial_replay_account.id,
            "serial_replay_serial_id": serial_replay_serial.id,
            "serial_replay_extra_serial_id": serial_replay_extra_serial.id,
            "dynamic_peer_location_id": dynamic_peer_location_id,
            "dynamic_peer_account_id": dynamic_peer_account.id,
            "location_id": location.id,
            "material_external_object_id": material_external_object.id,
            "material_external_version_id": material_external_version.id,
            "material_id": material.id,
            "material_policy_id": policy.id,
            "material_sku_code": material.sku_code,
            "opening_token": location_id.hex,
            "region_org_id": region_org_id,
            "serial_id": serial.id,
            "serial_no": serial.serial_no,
            "serial_qr_code": serial.qr_code,
        }
    fixture.update(_publish_control(owner, edge_engine, world))
    return fixture
