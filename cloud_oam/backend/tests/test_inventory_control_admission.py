"""Real HMAC capture + SQL-created authority/audit/receipt facts; no network."""
from source_configuration_file_fixtures import source_evidence
from datetime import datetime, timedelta, timezone
import json
import os
import runpy
from types import SimpleNamespace
from uuid import uuid4

from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
import sqlalchemy as sa

from app import inventory_control_admission as service
from app import inventory_control_configuration as configuration
from app import inventory_control_authority as authority
from app import inventory_control_preparation as preparation
from app.formal_access import load_formal_principal
from app.foundation_models import FileObject, Role, RolePermission, SourceSystem, Organization
from app.inventory_control_models import InventoryControlPreparation as Preparation
from app.inventory_control_attestation_models import InventoryControlCaptureAttestation as Receipt
from app.models import AuthSession, User
from test_inventory_control_attestation import world as capture_world, PATH, SECRET
from test_inventory_control_capture_ingress import Source, expected, collect, edge, capture
from test_inventory_control_authority import db as authority_db, command, decide
from test_inventory_control_configuration import login, cli
from test_formal_access import make_organization, make_user, assign


@pytest.fixture
def db(authority_db):
    with Operations.context(MigrationContext.configure(authority_db.connection())):
        Receipt.__table__.drop(authority_db.connection())
        runpy.run_path(str(PATH))['upgrade']()
    authority_db.commit()
    return authority_db


@pytest.fixture
def world(db, capture_world):
    capture_world['stage'](); seeded = capture_world['record']()
    root = db.get(Preparation, seeded['preparation_id'])
    organization = make_organization(db, name='Synthetic HQ')
    user, _ = make_user(db, organization, name='Synthetic capture reviewer')
    role = db.scalar(sa.select(Role).where(Role.code == 'admin'))
    assignment = assign(db, user, role, scope_type='national', scope_id='*')
    db.commit()
    file, _ = source_evidence(db, user.id)
    db.add(file); db.commit()
    return SimpleNamespace(actor=load_formal_principal(db, user.id), file=file.id, root=root.id, seed=root.id,
                           binding=root.binding_id, catalog=root.catalog_id, source=seeded['source_system_id'],
                           region=seeded['region_org_id'], assignment=assignment.id)


@pytest.fixture
def fresh(db, world, tmp_path):
    state = dict(version=2, sourceInstance='synthetic-edge', scopes={})
    base = None
    def run(*, zero=False, full=False, records=None):
        nonlocal base
        now = datetime.now(timezone.utc)
        collected = collect(Source([] if zero else records), now=now)
        box = edge.build_outbox(source_instance='synthetic-edge', scope_key='all', warehouse_filter=None,
                                company_id='synthetic-company', org_code='synthetic-org', snapshot_at=now.isoformat(),
                                snapshots={'inventory': collected['records']}, state=state, force_full=full, batch_size=2)
        bundle = capture.build_bundle(expected(), collected, box, manifest=edge.snapshot_manifest(box),
                                      batches=list(edge.snapshot_batches(box)), base=None if full else base)
        box['controlAttestation'] = capture.attestation_payload(bundle, key_id='synthetic-key-v1')
        box['controlEvidence'] = capture.archive(tmp_path/'evidence', bundle)
        edge.upload_outbox(outbox=box, api_base='https://synthetic.invalid/api', secret=SECRET,
                           state_file=tmp_path/'state.json', state=state)
        base = capture.load_base(tmp_path/'evidence', state, expected(), force_full=False)
        result = preparation.record_inventory_control_preparation(db, source_system_id=world.source, region_org_id=world.region,
                    expected_json=capture.canonical(expected()).decode(), evidence_json=capture.canonical(base).decode(),
                    checked_at=datetime.now(timezone.utc))
        db.commit(); world.root = result['preparation_id']
        return result
    return run


def grants(db, world, **changes):
    source = decide(db, world, **changes)
    catalogue = decide(db, world, action='catalog_grant', grant=source, **changes)
    return source, catalogue


def inspect(db, world):
    return service.inspect_inventory_control_admission(db, preparation_id=world.root)


def facts(db):
    return {table.name: tuple(tuple(row) for row in db.execute(sa.select(table).order_by(*table.primary_key.columns)))
            for table in sorted(preparation.Preparation.metadata.tables.values(), key=lambda row: row.name)}


@pytest.mark.parametrize('mode', ['full', 'incremental', 'zero'])
def test_actual_authorized_hmac_chain_and_all_database_facts_remain_unchanged(db, world, fresh, mode):
    source, catalogue = grants(db, world)
    fresh(zero=mode == 'zero')
    if mode == 'incremental': fresh(zero=True)
    before = facts(db); result = inspect(db, world)
    assert facts(db) == before
    assert result['capture_authorized'] and result['source_authenticated'] and result['catalog_authorized']
    assert not result['projection_published'] and not result['start_ready'] and not result['trusted_key_lifecycle_verified']
    observation = result['observation']
    assert observation['current_authority']['source_grant_id'] == str(source['decision_id'])
    assert observation['current_authority']['catalog_grant_id'] == str(catalogue['decision_id'])
    assert len(observation['captures']) == (2 if mode == 'incremental' else 1)
    assert result['observation_sha256'] == preparation._sha(observation)
    assert SECRET not in json.dumps(result) and 'storage_key' not in json.dumps(result)


def test_current_grant_does_not_retroactively_authorize_the_seed_capture(db, world):
    grants(db, world)
    assert authority.resolve_inventory_control_authority(db, preparation_id=world.seed)['catalog_authorized']
    with pytest.raises(service.ControlAdmissionError, match='source_authority_missing'): inspect(db, world)


@pytest.mark.parametrize('change', ['source_revoke', 'catalog_revoke', 'replacement_grants', 'file_hash',
                                 'file_quarantine', 'file_empty', 'source_disabled', 'region_disabled', 'expired'])
def test_revocation_mutable_evidence_and_expiry_refuse_new_admission(db, world, fresh, monkeypatch, change):
    expiry = authority._now(db) + timedelta(minutes=1)
    source, catalogue = grants(db, world, valid_to=expiry)
    fresh()
    assert inspect(db, world)['capture_authorized']
    if change in ('source_revoke', 'replacement_grants'):
        decide(db, world, action='revoke', grant=source)
        if change == 'replacement_grants': grants(db, world)
    elif change == 'catalog_revoke': decide(db, world, action='revoke', grant=catalogue)
    elif change == 'file_hash': db.get(FileObject, world.file).sha256 = 'b'*64
    elif change == 'file_quarantine': db.get(FileObject, world.file).status = 'quarantined'
    elif change == 'file_empty': db.get(FileObject, world.file).size_bytes = 0
    elif change == 'source_disabled': db.get(SourceSystem, world.source).enabled = False
    elif change == 'region_disabled': db.get(Organization, world.region).status = 'inactive'
    else: monkeypatch.setattr(authority, '_now', lambda db: expiry)
    db.commit(); before = facts(db)
    with pytest.raises((service.ControlAdmissionError, authority.ControlAuthorityError)): inspect(db, world)
    assert facts(db) == before


def test_natural_renewal_preserves_historical_capture_without_regranting_it(db, world, fresh, monkeypatch):
    cutoff = authority._now(db) + timedelta(minutes=1)
    old_source, _ = grants(db, world, valid_to=cutoff)
    fresh()
    current_source, _ = grants(db, world, valid_from=cutoff)
    monkeypatch.setattr(authority, '_now', lambda db: cutoff + timedelta(seconds=1))
    result = inspect(db, world)['observation']
    assert result['current_authority']['source_grant_id'] == str(current_source['decision_id'])
    assert {span['source_grant_id'] for span in result['captures'][0]['authorization_spans']} == {str(old_source['decision_id'])}


def _interval_rows(start, end, catalog):
    source = SimpleNamespace(id=uuid4(), action='source_grant', catalog_id=None, source_grant_id=None,
                             valid_from=start, valid_to=end, payload_sha256='a'*64)
    child = SimpleNamespace(id=uuid4(), action='catalog_grant', catalog_id=catalog, source_grant_id=source.id,
                            valid_from=start, valid_to=end, payload_sha256='b'*64)
    return source, child


@pytest.mark.parametrize('kind', ['contiguous', 'gap', 'overlap', 'expiry_endpoint', 'revoked_root', 'start_missing'])
def test_whole_capture_interval_checks_renewal_boundaries_and_closed_endpoint(kind):
    start = datetime(2026, 9, 20, tzinfo=timezone.utc); split = start + timedelta(minutes=1)
    end = start + timedelta(minutes=2); catalog = uuid4()
    first = _interval_rows(start, split, catalog)
    second = _interval_rows(split + (timedelta(seconds=1) if kind == 'gap' else
                                    -timedelta(seconds=1) if kind == 'overlap' else timedelta()), None, catalog)
    rows = first + second
    revoked = {first[0].id} if kind == 'revoked_root' else set()
    if kind == 'expiry_endpoint': rows = _interval_rows(start, end, catalog)
    if kind == 'start_missing': rows = second
    if kind == 'contiguous':
        spans, used = service._coverage(rows, catalog, revoked, start, end)
        assert len(used) == 4 and len(spans) == 3
        assert [s['end_inclusive'] for s in spans] == [False, False, True]
        assert spans[-1]['started_at'] == spans[-1]['completed_at'] == end.isoformat()
    else:
        with pytest.raises(service.ControlAdmissionError): service._coverage(rows, catalog, revoked, start, end)


def test_dirty_session_is_refused_without_refreshing_or_flushing_user_edits(db, world, fresh):
    grants(db, world); fresh(); user = db.get(User, world.actor.user_id); user.name = 'pending name'
    with pytest.raises(service.ControlAdmissionError, match='requires_clean_session'): inspect(db, world)
    assert user in db.dirty and user.name == 'pending name'


def test_time_expiring_during_observation_is_not_returned_as_current(db, world, fresh, monkeypatch):
    grants(db, world); fresh()
    now = datetime.now(timezone.utc)
    from itertools import chain, repeat
    clocks = chain([now], repeat(now + timedelta(minutes=46)))
    monkeypatch.setattr(authority, '_now', lambda db: next(clocks))
    with pytest.raises(service.ControlAdmissionError, match='expired_while_waiting'): inspect(db, world)


@pytest.mark.parametrize('roles,isolation', [(('edge_inbox','edge_inbox'),'read committed'),
    (('star_oam_migrator','other_login'),'read committed'),
    (('star_oam_migrator','star_oam_migrator'),'repeatable read'),
    (('star_oam_migrator','star_oam_migrator'),'serializable')])
def test_non_owner_or_historical_transaction_snapshot_cannot_be_used(roles, isolation):
    db = SimpleNamespace(new=(), dirty=(), deleted=(),
        get_bind=lambda: SimpleNamespace(dialect=SimpleNamespace(name='postgresql')),
        execute=lambda query: SimpleNamespace(one=lambda: roles), scalar=lambda query: isolation)
    with pytest.raises(service.ControlAdmissionError): service._owner(db)


def test_removed_capture_receipt_cannot_be_replaced_by_authorization_flags(db, world, fresh):
    grants(db, world); fresh(); assert inspect(db, world)['capture_authorized']
    # Deliberate test-only corruption; normal SQL DELETE is blocked by 0114.
    db.execute(sa.text('DROP TRIGGER trg_control_attestation_delete_0114'))
    db.execute(sa.delete(Receipt)); db.commit()
    with pytest.raises(service.attestation.CaptureAttestationError, match='missing_capture_receipt'): inspect(db, world)


@pytest.mark.parametrize('change', ['expired', 'revoked', 'wrong_version', 'permission_denied'])
def test_inspection_entry_rechecks_current_web_session_and_headquarters_permission(db, world, fresh, login, change):
    grants(db, world); fresh()
    kwargs = {key: login[key] for key in ('access_token', 'expected_authorization_version')}
    kwargs['preparation_id'] = world.root
    assert configuration.inspect_inventory_control_capture(db, **kwargs)['capture_authorized']
    if change == 'expired': db.scalar(sa.select(AuthSession)).expires_at = authority._now(db)
    elif change == 'revoked': db.scalar(sa.select(AuthSession)).revoked_at = authority._now(db)
    elif change == 'wrong_version': kwargs['expected_authorization_version'] += 1
    else: db.scalar(sa.select(RolePermission)).effect = 'deny'
    db.commit(); before = facts(db)
    with pytest.raises(Exception): configuration.inspect_inventory_control_capture(db, **kwargs)
    assert facts(db) == before


def test_cli_inspection_has_no_commit_and_never_exposes_credentials(db, world, fresh, login, cli, tmp_path, monkeypatch, capsys):
    grants(db, world); fresh(); before = facts(db); db.rollback()
    engine = db.get_bind()
    monkeypatch.setattr(sa, 'create_engine', lambda *a, **kw: engine)
    monkeypatch.setattr(engine, 'dispose', lambda: None)
    monkeypatch.setattr(cli, '_connection_config', lambda: ('synthetic-dsn', 'synthetic-db'))
    monkeypatch.setattr(cli, '_database_preflight', lambda db, target: None)
    monkeypatch.setattr(sa.orm.Session, 'commit', lambda db: pytest.fail('inspection must not commit'))
    path = tmp_path/'inspection.json'
    path.write_text(json.dumps(dict(preparation_id=str(world.root), expected_authorization_version=login['expected_authorization_version'])))
    reader, writer = os.pipe(); os.write(writer, login['access_token'].encode()); os.close(writer)
    try: status = cli.main(['--inspection-file', str(path), '--access-token-fd', str(reader)])
    finally: os.close(reader)
    output = capsys.readouterr()
    assert status == 0 and not output.err
    assert json.loads(output.out)['mode'] == 'inspect'
    assert login['access_token'] not in output.out and facts(db) == before


@pytest.mark.parametrize('args', [['--inspection-file','unused','--mode','apply'], ['--command-file','unused','--mode','inspect']])
def test_inspection_cannot_be_used_as_an_apply_command(cli, args):
    with pytest.raises(SystemExit) as error: cli.main(args)
    assert error.value.code == 2
