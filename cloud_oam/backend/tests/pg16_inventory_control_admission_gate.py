"""Real role, snapshot and lock evidence inside the protected PG16 gate only."""
from source_configuration_file_fixtures import source_evidence
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app import inventory_control_admission as admission
from app import inventory_control_normalization as normalization
from app import inventory_control_authority as authority
from app import inventory_control_preparation as preparation
from app.formal_access import FormalAccessError, load_formal_principal
from app.foundation_models import FileObject, Organization, Person
from app.inventory_control_models import InventoryControlSourceBinding as Binding, InventoryControlPreparation as Preparation
from app.models import User
from app.routers import integrations
from test_inventory_control_capture_ingress import Source, edge, capture
from test_inventory_control_authority import command
from pg16_inventory_control_preparation_gate import _formal_stock


def assert_inventory_control_admission_gate(owner_engine, edge_engine, api_engine, projector_engine, backup_engine,
                                           catalog, settings, transport):
    before_stock = _formal_stock(owner_engine)
    with Session(owner_engine) as db:
        actor = None
        candidates = db.scalars(select(User.id).join(Person, Person.id == User.person_id)
            .join(Organization, Organization.id == Person.organization_id)
            .where(Organization.org_type == 'headquarters', Organization.status == 'active',
                   User.account_status == 'active', User.is_active.is_(True)).order_by(User.id))
        for user_id in candidates:
            try:
                candidate = load_formal_principal(db, user_id)
                authority._operator(db, candidate, authority._now(db))
            except (FormalAccessError, authority.ControlAuthorityError):
                continue
            actor = candidate
            break
        assert actor is not None
        binding = db.scalars(select(Binding).where(
            Binding.binding_jsonb['source_instance'].as_string() == catalog['binding']['source_instance'])).one()
        root = db.scalars(select(Preparation).where(Preparation.binding_id == binding.id)
                          .order_by(Preparation.created_at.desc(), Preparation.id).limit(1)).one()
        file, _ = source_evidence(db, actor.user_id)
        db.add(file); db.flush()
        world = SimpleNamespace(actor=actor, file=file.id, root=root.id, binding=binding.id, catalog=root.catalog_id,
                                source=binding.source_system_id, region=binding.region_org_id)
        db.commit()
        source = authority.record_inventory_control_authority(db, actor=actor, command=command(db, world)); db.commit()
        catalogue = authority.record_inventory_control_authority(db, actor=actor,
                    command=command(db, world, action='catalog_grant', grant=source)); db.commit()
        with pytest.raises(admission.ControlAdmissionError, match='source_authority_missing'):
            admission.inspect_inventory_control_admission(db, preparation_id=world.root)
        db.rollback()
        now = authority._now(db)
    # A newly authorized, explicit-zero full capture is required. The original
    # previously captured root is never retrospectively blessed by the grant.
    with pytest.MonkeyPatch.context() as patch, TemporaryDirectory(prefix='pg16-admission-') as folder:
        folder = Path(folder)
        patch.setattr(integrations, 'settings', settings)
        patch.setattr(edge, 'shared_edge_request', transport)
        state = dict(version=2, sourceInstance=catalog['binding']['source_instance'], scopes={})
        collected = capture.collect(catalog, read_page=Source([]), normalize_records=edge.inventory_records,
                    bind_scope=edge.bind_inventory_scope, page_size=2, clock=lambda: now)
        box = edge.build_outbox(source_instance=catalog['binding']['source_instance'], scope_key='all', warehouse_filter=None,
                company_id=catalog['binding']['company_id'], org_code=catalog['binding']['org_code'], snapshot_at=now.isoformat(),
                snapshots={'inventory': collected['records']}, state=state, force_full=True, batch_size=2)
        bundle = capture.build_bundle(catalog, collected, box, manifest=edge.snapshot_manifest(box),
                    batches=list(edge.snapshot_batches(box)), base=None)
        box['controlEvidence'] = capture.archive(folder/'evidence', bundle)
        box['controlAttestation'] = capture.attestation_payload(bundle, key_id=settings.edge_control_capture_key_id)
        edge.upload_outbox(outbox=box, api_base='https://synthetic.invalid/api', secret=settings.edge_sync_secret,
                           state_file=folder/'state.json', state=state)
        with Session(owner_engine) as db:
            prepared = preparation.record_inventory_control_preparation(db, source_system_id=world.source, region_org_id=world.region,
                    expected_json=capture.canonical(catalog).decode(), evidence_json=capture.canonical(bundle['evidence']).decode(),
                    checked_at=authority._now(db))
            db.commit(); world.root = prepared['preparation_id']
    with Session(owner_engine) as holder:
        result = normalization.inspect_inventory_control_normalization(holder, preparation_id=world.root,
            rules={'revision': 'synthetic-zero-v1', 'conditions': []})
        assert result['capture_authorized'] and not result['start_ready'] and not result['projection_published']
        assert result['normalization_review']['normalization_complete']
        assert result['normalization_review']['candidate_groups'] == []
        assert not result['normalization_rules_authorized'] and not result['master_source_evidence_verified']
        assert result['observation']['current_authority']['catalog_grant_id'] == str(catalogue['decision_id'])
        revoke = command(holder, world, action='revoke', grant=source)
        # Each contender is a separate real connection. The inspected binding
        # and mutable evidence remain stable until the holder rolls back.
        with Session(owner_engine) as contender:
            contender.execute(text("SET LOCAL lock_timeout='200ms'"))
            with pytest.raises(DBAPIError) as error:
                authority.record_inventory_control_authority(contender, actor=actor, command=revoke)
            assert error.value.orig.sqlstate == '55P03'
            contender.rollback()
        for sql, identifier in (
            ('UPDATE public.files SET status=\'quarantined\' WHERE id=:id', world.file),
            ('UPDATE public.source_systems SET enabled=false WHERE id=:id', world.source),
        ):
            with owner_engine.connect() as connection:
                connection.execute(text("SET LOCAL lock_timeout='200ms'"))
                with pytest.raises(DBAPIError) as error: connection.execute(text(sql), {'id': identifier})
                assert error.value.orig.sqlstate == '55P03'
                connection.rollback()
        holder.rollback()
    for engine in (edge_engine, api_engine, projector_engine, backup_engine):
        with Session(engine) as db:
            with pytest.raises(admission.ControlAdmissionError, match='requires_direct_owner'):
                normalization.inspect_inventory_control_normalization(db, preparation_id=world.root,
                    rules={'revision': 'synthetic-zero-v1', 'conditions': []})
    with Session(owner_engine) as db:
        db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ'))
        with pytest.raises(admission.ControlAdmissionError, match='requires_read_committed'):
            admission.inspect_inventory_control_admission(db, preparation_id=world.root)
    with Session(owner_engine) as db:
        authority.record_inventory_control_authority(db, actor=actor, command=revoke); db.commit()
        with pytest.raises(admission.ControlAdmissionError, match='source_authority_missing'):
            admission.inspect_inventory_control_admission(db, preparation_id=world.root)
    assert _formal_stock(owner_engine) == before_stock
    print('PG16 control admission: current and capture-time grants, fresh HMAC zero snapshot, actual binding/file/source locks, revocation and owner boundary PASS', flush=True)
