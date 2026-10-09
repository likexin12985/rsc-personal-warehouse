"""Contact 0181 proof for an explicitly owned, disposable PostgreSQL 16 leg.

Adapted from the reviewed local prototype verify_contact_0181_pg16.py, source
SHA256: 00bef014ad3b514fdcd353c056c4aad838487e1a3d43e4ff1d28dea5c62f908f
The prototype was not executed successfully; this module claims no old proof.
The caller owns database/bootstrap/cleanup. No DSN, cloud provider or production
credential discovery, business inventory setup, old-suite invocation or retry.
"""
from contextlib import ExitStack
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from hashlib import sha256
import base64
import json
from pathlib import Path
import runpy
from types import SimpleNamespace
from uuid import UUID, uuid4

from sqlalchemy import text

WORKSPACE = Path(__file__).resolve().parents[2]
OWNER = 'star_oam_migrator'
API = 'star_oam_api'
OLD = '20261229_0180'
HEAD = '20261230_0181'
VERSION = 1810001
INSTANCE = 'isolated-contact-0181'
CONTACT = 'material_request_contact'
KEY_PATH = 'transit/keys/rsc-material-request-contact'
BUSINESS_TABLES = ('material_requests', 'material_request_revisions')
PIN_TABLES = ('kms_data_key_pins', 'openbao_data_key_pins', 'application_key_version_claims')
INVENTORY_TABLES = ('inventory_transactions', 'inventory_movements', 'stock_accounts', 'stock_balances')
SECRET = b'isolated-contact-0181-command-hmac-synthetic-only'


def write_json(name, value):
    # All IDs and rows belong to this leg's synthetic fixture. Retain only
    # descriptive stage names on stdout; no envelope/pin/token is printed.
    print('Contact 0181 stage: ' + name.removesuffix('.json'), flush=True)


def require(value, stage):
    if not value:
        raise RuntimeError(stage)


def digest(value):
    return sha256(value).hexdigest()


def limits(db):
    db.execute(text("SET LOCAL statement_timeout='30s'"))
    db.execute(text("SET LOCAL lock_timeout='8s'"))
    db.execute(text("SET LOCAL idle_in_transaction_session_timeout='60s'"))


def head(db, expected):
    require(db.execute(text('SELECT version_num FROM public.alembic_version')).scalars().all() == [expected],
        'exact_schema_head')


def rows(db, tables):
    return {table: db.execute(text('SELECT to_jsonb(t)::text FROM public.' + table +
        ' AS t ORDER BY to_jsonb(t)::text')).scalars().all() for table in tables}


def summary(snapshot):
    return {name: dict(count=len(values), sha256=digest(json.dumps(values, ensure_ascii=False,
        separators=(',', ':')).encode())) for name, values in snapshot.items()}


def exact_catalog(db, data, side):
    from app.stock_scrap_security_probe import snapshot
    actual = snapshot(db, table_names=tuple(data['catalog']['tables']),
        function_names=tuple(data['observed_function_names']))
    require(actual == dict(tables=data['catalog']['tables'], functions=data['catalog']['functions'][side]),
        'exact_' + side + '_catalog')
    # The scoped probe verifies public objects; reject namespace duplicates too.
    found = [tuple(row) for row in db.execute(text('SELECT n.nspname,p.proname,'
        'pg_catalog.oidvectortypes(p.proargtypes) FROM pg_catalog.pg_proc p '
        'JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace WHERE p.proname=ANY(:names) '
        'ORDER BY p.proname,n.nspname,3'), {'names': data['observed_function_names']})]
    require(found == [('public', name, '') for name in sorted(data['observed_function_names'])],
        'functions_only_in_public')
    return digest(json.dumps(actual, sort_keys=True, separators=(',', ':')).encode())


def online_step(db, configuration, scripts, up):
    from alembic.runtime.environment import EnvironmentContext
    before, target = (OLD, HEAD) if up else (HEAD, OLD)
    head(db, before)
    def revisions(current, context):
        require(tuple(current) == (before,), 'focus_start_head')
        steps = list(scripts._upgrade_revs(target, current) if up else scripts._downgrade_revs(target, current))
        require(len(steps) == 1 and steps[0].revision.revision == HEAD and steps[0].is_upgrade is up
            and steps[0].from_revisions == (before,) and steps[0].to_revisions == (target,), 'one_0181_step')
        return steps
    with EnvironmentContext(configuration, scripts, fn=revisions, as_sql=False, destination_rev=target) as env:
        env.configure(connection=db, version_table_schema='public', transaction_per_migration=False)
        with env.begin_transaction():
            env.run_migrations()
    head(db, target)
    return dict(fromRevision=before, toRevision=target, onlineAlembic=True)


def v2(request_id, person_id, **changes):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    value = dict(schema='rsc.material_request_contact.v2', provider='openbao_transit_v1',
        purpose=CONTACT, environment='test', provider_instance_id=INSTANCE, key_path=KEY_PATH,
        application_key_version=VERSION, transit_key_version=2)
    value.update(changes)
    aad = b'\0'.join([b'cloud_oam.material_request.contact.envelope.v2', *[
        (key + '=' + str(value[key])).encode('ascii') for key in ('provider', 'purpose', 'environment',
            'provider_instance_id', 'key_path', 'application_key_version', 'transit_key_version')],
        ('request_id=' + str(request_id)).encode(), ('requester_person_id=' + str(person_id)).encode()])
    nonce = b'contact0181!'
    # A deterministic synthetic key is test data, never a provider/runtime secret.
    ciphertext = AESGCM(bytes(range(32))).encrypt(nonce, b'{"synthetic":"contact-only"}', aad)
    return dict(value, ciphertext_b64=base64.b64encode(ciphertext).decode(),
        nonce_b64=base64.b64encode(nonce).decode(), aad_sha256=digest(aad),
        mobile_hmac='hmac:1:' + 'b' * 64, contact_hmac='hmac:1:' + 'c' * 64)


def seed_pin(owner):
    with owner.begin() as db:
        limits(db)
        require(db.scalar(text('SELECT count(*) FROM public.application_key_version_claims '
            'WHERE purpose=:purpose AND application_key_version=:version'),
            dict(purpose=CONTACT, version=VERSION)) == 0, 'unused_synthetic_pin_coordinate')
        now = datetime(2026, 10, 9, tzinfo=timezone.utc)
        value = dict(purpose=CONTACT, environment='test', provider_instance_id=INSTANCE, key_path=KEY_PATH,
            application_key_version=VERSION, transit_key_version=2,
            ciphertext_sha256=digest(b'synthetic-contact-0181-wrap'),
            context_sha256=digest(b'synthetic-contact-0181-context'),
            associated_data_sha256=digest(b'synthetic-contact-0181-wrap-aad'), created_at=now)
        db.execute(text('INSERT INTO public.openbao_data_key_pins (' + ','.join(value) +
            ') VALUES (' + ','.join(':' + key for key in value) + ')'), value)
    with owner.connect() as db:
        claim = db.execute(text('SELECT provider,ciphertext_sha256,created_at FROM '
            'public.application_key_version_claims WHERE purpose=:purpose AND application_key_version=:version'),
            dict(purpose=CONTACT, version=VERSION)).one()
        require(tuple(claim) == ('openbao_transit_v1', value['ciphertext_sha256'], now), 'exact_trigger_claim')
    return dict(purpose=CONTACT, applicationKeyVersion=VERSION, transitKeyVersion=2,
        syntheticPinOnly=True, realProviderContacted=False)


def api_permissions(api):
    from sqlalchemy.exc import DBAPIError
    denials = []
    with api.connect() as db:
        transaction = db.begin()
        try:
            limits(db)
            for table in PIN_TABLES:
                require(db.scalar(text('SELECT count(*) FROM public.' + table)) >= 0, 'api_pin_read')
                for sql in ('UPDATE public.' + table + ' SET purpose=purpose',
                    'DELETE FROM public.' + table, 'TRUNCATE public.' + table,
                    'INSERT INTO public.' + table + ' DEFAULT VALUES'):
                    nested = db.begin_nested()
                    try:
                        db.execute(text(sql))
                    except DBAPIError as error:
                        require(error.orig.sqlstate == '42501', 'pin_acl_exact_denial')
                        denials.append(dict(table=table, operation=sql.split()[0], sqlstate='42501'))
                    else:
                        raise RuntimeError('pin_acl_unexpected_success')
                    finally:
                        nested.rollback()
            for function in ('rsc_guard_material_request_identity_0029', 'rsc_guard_material_request_revision_0029'):
                require(db.scalar(text("SELECT has_function_privilege(current_user,:signature,'EXECUTE')"),
                    {'signature': 'public.' + function + '()'}) is False, 'contact_trigger_execute_not_granted')
                nested = db.begin_nested()
                try:
                    db.execute(text('SELECT public.' + function + '()'))
                except DBAPIError as error:
                    require(error.orig.sqlstate == '42501', 'contact_function_acl_exact_denial')
                    denials.append(dict(function=function, sqlstate='42501'))
                else:
                    raise RuntimeError('contact_function_acl_unexpected_success')
                finally:
                    nested.rollback()
        finally:
            transaction.rollback()
    return denials


def foundation_context(owner, run_id):
    """Only synthetic masters; migrated permission/route catalogs are read-only.

    Deliberately avoid make_world(), which recreates seeded authorization and
    attaches an unrelated file. No stock location, opening or ledger is seeded.
    """
    from app.demand_models import ApprovalRouteVersion, ApprovalRouteStepDef
    from app.foundation_models import Role, Permission, RolePermission, AuditChainHead, SourceSystem, ExternalObject
    from app.inventory_models import FormalMaterial
    from test_material_request_draft_service import _organization, _person, _user, _assignment, AUDIT_HEAD_ID
    from sqlalchemy import select
    from sqlalchemy.orm import Session
    catalog_tables = ('roles', 'permissions', 'role_permissions', 'approval_route_versions',
        'approval_route_step_defs', 'audit_chain_heads')
    with Session(owner, expire_on_commit=False) as db:
        require(db.scalar(text('SELECT count(*) FROM public.users')) == 0, 'empty_foundation_users')
        require(all(not values for values in rows(db, BUSINESS_TABLES + INVENTORY_TABLES).values()),
            'empty_business_and_inventory_foundation')
        before = rows(db, catalog_tables)
        roles = {row.code: row for row in db.scalars(select(Role).where(Role.code.in_(
            ('technician', 'provincial_manager', 'admin', 'star_headquarters_approver'))))}
        require(set(roles) == {'technician', 'provincial_manager', 'admin', 'star_headquarters_approver'}
            and all(row.status == 'active' for row in roles.values())
            and {row.code for row in roles.values() if row.is_external} == {'star_headquarters_approver'},
            'exact_existing_formal_roles')
        for code, action, field in (('technician', 'create', ''), ('technician', 'update_draft', ''),
            ('technician', 'submit', ''), ('provincial_manager', 'approve_region', 'approval_decision'),
            ('admin', 'approve_headquarters', 'approval_decision'), ('admin', 'register_external', 'approval_evidence'),
            ('admin', 'verify_external', 'approval_evidence')):
            permission = db.scalars(select(Permission).where(Permission.resource == 'material_request',
                Permission.action == action, Permission.field_code == field)).one()
            grant = db.scalars(select(RolePermission).where(RolePermission.role_id == roles[code].id,
                RolePermission.permission_id == permission.id)).one()
            require(grant.effect == 'allow', 'existing_formal_grant_not_repaired')
        route = db.scalars(select(ApprovalRouteVersion).where(ApprovalRouteVersion.route_code ==
            'material_request_three_stage', ApprovalRouteVersion.status == 'active')).one()
        require(route.version == 1 and route.approval_mode == 'external_registration' and route.effective_to is None,
            'existing_formal_route')
        steps = db.scalars(select(ApprovalRouteStepDef).where(ApprovalRouteStepDef.route_version_id == route.id)).all()
        require({row.step_no: (row.role_code, row.source_mode, row.scope_type) for row in steps} == {
            1: ('provincial_manager', 'internal', 'organization'), 2: ('admin', 'internal', 'national'),
            3: ('star_headquarters_approver', 'external_registration', 'document')}, 'existing_formal_route_steps')
        audit = db.get(AuditChainHead, AUDIT_HEAD_ID)
        require(audit is not None and audit.stream_key == 'material_request' and audit.version == 0
            and audit.last_event_id is None and audit.last_hash is None, 'existing_empty_request_audit_head')
        hq = _organization(db, 'C2-HQ', 'headquarters')
        region = _organization(db, 'C2-REGION', 'region_company', parent=hq)
        dept = _organization(db, 'C2-DEPT', 'department', parent=region)
        requester_person = _person(db, dept, 'Synthetic contact requester')
        requester = _user(db, requester_person)
        _assignment(db, requester, roles['technician'], scope_type='person',
            scope_id=str(requester_person.id), assigned_by=requester.id)
        manager = _user(db, _person(db, dept, 'Synthetic contact manager'))
        _assignment(db, manager, roles['provincial_manager'], scope_type='organization',
            scope_id=str(region.id), assigned_by=requester.id)
        for index in range(2):
            admin = _user(db, _person(db, hq, 'Synthetic contact admin ' + str(index)))
            _assignment(db, admin, roles['admin'], scope_type='national', scope_id='*', assigned_by=requester.id)
        source = SourceSystem(code='C2-CONTACT-' + run_id, name='Synthetic contact master source',
            mode='read_only', enabled=True, configuration_jsonb={})
        db.add(source); db.flush()
        external = ExternalObject(source_system_id=source.id, entity_type='material',
            external_id='C2-MATERIAL-' + run_id, current_version_id=None, deleted_at=None)
        db.add(external); db.flush()
        material = FormalMaterial(external_object_id=external.id, sku_code='C2-SKU-' + run_id,
            name='Synthetic contact material', specification='', base_unit='EA', status='active',
            source_updated_at=datetime.now(timezone.utc))
        db.add(material); db.flush()
        require(rows(db, catalog_tables) == before, 'foundation_did_not_change_authorization_route_or_audit')
        require(all(not values for values in rows(db, BUSINESS_TABLES + INVENTORY_TABLES).values()),
            'foundation_did_not_create_business_or_inventory_rows')
        db.commit()
        context = dict(user=requester.id, person=requester_person.id, material=material.id)
    write_json('contact-0181-foundation.json', dict(context=context, catalog=summary(before),
        organizations=3, people=4, users=4, roleAssignments=4, materials=1, inventoryRows=0,
        existingPermissionsPreserved=True, existingRoutesPreserved=True))
    return context


def draft_value(context, request_id, use_v2):
    from test_material_request_draft_service import _draft
    world = SimpleNamespace(actor_person=SimpleNamespace(id=context['person']),
        materials=(SimpleNamespace(id=context['material']),), attachment=SimpleNamespace(id=None))
    value = replace(_draft(world, request_id), attachment_file_ids=(), purpose='Synthetic contact 0181 proof')
    return replace(value, contact_envelope=v2(request_id, context['person'])) if use_v2 else value


def create_request(api, context, key, use_v2):
    from app.formal_access import load_formal_principal
    from app.formal_services.material_request_draft import create_material_request_draft, derive_material_request_create_id
    from sqlalchemy.orm import Session
    with Session(api) as db:
        actor = load_formal_principal(db, context['user'])
        request_id = derive_material_request_create_id(actor=actor, idempotency_key=key, idempotency_hmac_secret=SECRET)
        write_json('contact-0181-' + ('v2' if use_v2 else 'mixed') + '-planned.json', dict(requestId=str(request_id), key=key))
        result = create_material_request_draft(db, actor=actor, material_request_id=request_id,
            draft=draft_value(context, request_id, use_v2), idempotency_key=key,
            idempotency_hmac_secret=SECRET, trace_request_id=key)
        db.commit()
    return request_id, result


def submit_return(api, context, request_id, prefix):
    from app.formal_access import load_formal_principal
    from app.formal_services.material_request_draft import submit_material_request
    from app.formal_services.material_request_approval import decide_material_request_approval, MaterialRequestApprovalInput
    from app.formal_services.material_request_policy import ApprovalReturnInstruction
    from app.demand_models import MaterialRequest, MaterialRequestLine, ApprovalStep, ApprovalStepCandidate
    from sqlalchemy import select
    from sqlalchemy.orm import Session
    with Session(api) as db:
        request = db.get(MaterialRequest, request_id)
        submitted = submit_material_request(db, actor=load_formal_principal(db, context['user']),
            material_request_id=request_id, expected_version=request.version, idempotency_key=prefix + '-submit',
            idempotency_hmac_secret=SECRET, trace_request_id=prefix + '-submit')
        db.commit()
    with Session(api) as db:
        request = db.get(MaterialRequest, request_id)
        step = db.get(ApprovalStep, submitted.approval_step_ids[0])
        users = db.scalars(select(ApprovalStepCandidate.user_id).where(
            ApprovalStepCandidate.step_id == step.id, ApprovalStepCandidate.candidate_kind == 'assignee')).all()
        require(len(users) == 1, 'one_exact_regional_candidate')
        lines = db.scalars(select(MaterialRequestLine).where(MaterialRequestLine.revision_id == submitted.revision_id)).all()
        returned = decide_material_request_approval(db, actor=load_formal_principal(db, users[0]),
            material_request_id=request_id, approval_step_id=step.id, expected_request_version=request.version,
            expected_step_version=step.version, decision=MaterialRequestApprovalInput(action='return',
                return_lines=tuple(ApprovalReturnInstruction(request_line_id=line.id,
                    required_review_qty=line.requested_qty, reason='Synthetic contact migration review') for line in lines),
                comment='Synthetic contact migration review'), idempotency_key=prefix + '-return',
            idempotency_hmac_secret=SECRET, trace_request_id=prefix + '-return')
        db.commit()
    return submitted.revision_id, returned.request_version


def amend(api, context, request_id, version, key, use_v2):
    from app.formal_access import load_formal_principal
    from app.formal_services.material_request_draft import amend_material_request_draft
    from sqlalchemy.orm import Session
    with Session(api) as db:
        result = amend_material_request_draft(db, actor=load_formal_principal(db, context['user']),
            material_request_id=request_id, expected_version=version, draft=draft_value(context, request_id, use_v2),
            idempotency_key=key, idempotency_hmac_secret=SECRET, trace_request_id=key)
        db.commit()
    return result


def revision_bytes(owner, revision_id):
    with owner.connect() as db:
        return db.scalar(text('SELECT to_jsonb(r)::text FROM public.material_request_revisions r WHERE id=:id'),
            {'id': revision_id})


def tamper_matrix(api, request_id, revision_id, person):
    from sqlalchemy.exc import DBAPIError
    good = v2(request_id, person)
    cases = [('missing-' + key, {name: value for name, value in good.items() if name != key}) for key in good]
    cases += [('extra-field', good | {'unexpected': 'field'}), ('null-envelope', None), ('array-envelope', []),
        ('wrong-aad', good | {'aad_sha256': '0' * 64}), ('bad-base64', good | {'ciphertext_b64': '?' * 24}),
        ('bad-padding', good | {'ciphertext_b64': 'A' * 21 + '==='}),
        ('noncanonical-pad-bits', good | {'ciphertext_b64': 'A' * 22 + 'B='}),
        ('bad-nonce', good | {'nonce_b64': 'A' * 15 + '='}),
        ('boolean-version', good | {'application_key_version': True}),
        ('fraction-version', good | {'application_key_version': 1.5}),
        ('overflow-version', good | {'application_key_version': 2147483648}),
        ('wrong-request-aad', v2(UUID('10000000-0000-4000-8000-000000000001'), person)),
        ('wrong-person-aad', v2(request_id, UUID('20000000-0000-4000-8000-000000000001')))]
    # Recompute AAD for these cases: only a real pin-coordinate check can reject.
    for key, value in (('environment', 'staging'), ('provider_instance_id', 'isolated-other-instance'),
        ('application_key_version', VERSION + 1), ('transit_key_version', 3),
        ('provider', 'aliyun_kms'), ('purpose', 'authentication_idempotency'),
        ('key_path', 'transit/keys/rsc-authentication-idempotency')):
        cases.append(('pin-' + key, v2(request_id, person, **{key: value})))
    results = []
    for table, row_id, function in (
        ('material_requests', request_id, 'rsc_guard_material_request_identity_0029'),
        ('material_request_revisions', revision_id, 'rsc_guard_material_request_revision_0029')):
        with api.connect() as db:
            transaction = db.begin()
            try:
                limits(db)
                original = db.scalar(text('SELECT contact_snapshot_jsonb FROM public.' + table + ' WHERE id=:id'), {'id': row_id})
                require(original == good, 'exact_v2_guard_positive_control')
                query = text('UPDATE public.' + table + ' SET contact_snapshot_jsonb=CAST(:envelope AS jsonb) WHERE id=:id')
                db.execute(query, dict(id=row_id, envelope=json.dumps(good)))
                db.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))
                for label, invalid in cases:
                    nested = db.begin_nested()
                    try:
                        db.execute(query, dict(id=row_id, envelope=json.dumps(invalid)))
                    except DBAPIError as error:
                        require(error.orig.sqlstate == 'P0001'
                            and error.orig.diag.message_primary == 'formal material-request invariant violated'
                            and function + '()' in (error.orig.diag.context or ''), 'exact_contact_guard_rejection_' + label)
                        results.append(dict(table=table, case=label, sqlstate='P0001', function=function))
                    else:
                        raise RuntimeError('tamper_accepted_' + label)
                    finally:
                        nested.rollback()
                    require(db.scalar(text('SELECT contact_snapshot_jsonb FROM public.' + table + ' WHERE id=:id'),
                        {'id': row_id}) == original, 'tamper_savepoint_exact_readback')
            finally:
                transaction.rollback()
    return results


def downgrade_blocked(owner, configuration, scripts, label):
    with owner.connect() as db:
        transaction = db.begin()
        try:
            limits(db)
            db.execute(text('SET LOCAL search_path=pg_catalog,public'))
            try:
                online_step(db, configuration, scripts, False)
            except ValueError as error:
                require(str(error) == 'cannot downgrade 0181 while non-v1 contact history exists',
                    'exact_downgrade_blocker')
            else:
                raise RuntimeError('v2_downgrade_unexpected_success')
            head(db, HEAD)
        finally:
            transaction.rollback()
    return dict(case=label, refusedBeforeMutation=True, outerTransactionRolledBack=True)


def migration_writer_lock_proof(owner, api, configuration, scripts, data, request_id):
    """Observe the API PID blocked by the actual migration transaction PID."""
    from concurrent.futures import ThreadPoolExecutor
    from queue import Queue
    from threading import Event
    from time import monotonic
    from sqlalchemy.exc import DBAPIError

    relevant = BUSINESS_TABLES + PIN_TABLES + INVENTORY_TABLES
    with owner.connect() as db:
        before = rows(db, relevant)
        exact_catalog(db, data, 'after')
    pids = Queue(maxsize=1)
    cadence = Event()

    def writer():
        with api.connect() as db:
            transaction = db.begin()
            try:
                limits(db)
                require(db.execute(text('SELECT current_user, session_user')).one() == (API, API),
                    'direct_api_concurrency_identity')
                pids.put(db.scalar(text('SELECT pg_backend_pid()')))
                # This first real write must wait for the migration locks.
                db.execute(text('UPDATE public.material_requests SET '
                    'contact_snapshot_jsonb=contact_snapshot_jsonb WHERE id=:id'), {'id': request_id})
                db.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))
                nested = db.begin_nested()
                try:
                    db.execute(text("UPDATE public.material_requests SET contact_snapshot_jsonb="
                        "contact_snapshot_jsonb || '{\"unexpected\": true}'::jsonb WHERE id=:id"),
                        {'id': request_id})
                except DBAPIError as error:
                    require(error.orig.sqlstate == 'P0001'
                        and error.orig.diag.message_primary == 'formal material-request invariant violated'
                        and 'rsc_guard_material_request_identity_0029()' in (error.orig.diag.context or ''),
                        'exact_postmigration_writer_guard')
                else:
                    raise RuntimeError('postmigration_writer_guard_not_enforced')
                finally:
                    nested.rollback()
                return dict(validNoopAccepted=True, invalidEnvelopeRejected=True, sqlstate='P0001')
            finally:
                transaction.rollback()

    pool = ThreadPoolExecutor(max_workers=1)
    future = None
    try:
        with owner.connect() as db:
            transaction = db.begin()
            try:
                limits(db)
                leader = db.scalar(text('SELECT pg_backend_pid()'))
                # Both are real one-revision Alembic operations. The final
                # installed guard is 0181 and its locks remain until COMMIT.
                online_step(db, configuration, scripts, False)
                online_step(db, configuration, scripts, True)
                exact_catalog(db, data, 'after')
                future = pool.submit(writer)
                follower = pids.get(timeout=5)
                require(follower != leader, 'independent_migration_writer_connections')
                deadline = monotonic() + 12
                blocked = None
                while monotonic() < deadline:
                    blocked = db.execute(text("SELECT EXISTS(SELECT 1 FROM pg_locks "
                        "WHERE pid=:pid AND NOT granted AND relation='public.material_requests'::regclass), "
                        'pg_blocking_pids(:pid)'), {'pid': follower}).one()
                    if blocked[0] and leader in blocked[1]:
                        break
                    require(not future.done(), 'api_write_completed_before_migration_release')
                    # Poll cadence only: success requires the observed lock
                    # graph; elapsed time never substitutes for that evidence.
                    cadence.wait(0.02)
                else:
                    raise RuntimeError('exact_migration_writer_lock_not_observed')
                require(blocked[0] and leader in blocked[1], 'exact_migration_writer_lock')
                transaction.commit()
            finally:
                if transaction.is_active:
                    transaction.rollback()
        result = future.result(timeout=35)
    finally:
        # The real SQL has a 30s statement timeout, so an error cannot leave
        # an unbounded writer after its leader transaction has rolled back.
        pool.shutdown(wait=True)
    with owner.connect() as db:
        head(db, HEAD)
        exact_catalog(db, data, 'after')
        require(rows(db, relevant) == before, 'migration_writer_fresh_rollback_readback')
    return result | dict(blockingPidObserved=True, actualAlembicLock=True, factsUnchanged=True)


def _resume_v1_predecessor(owner, api):
    """Recover this leg's exact synthetic draft after a failed 0181 upgrade.

    The native caller must first verify its stopped owned-cluster receipt.
    This reads the existing command; it never resubmits the create operation.
    """
    import re
    from app.formal_access import load_formal_principal
    from app.formal_services.material_request_draft import derive_material_request_create_id
    from sqlalchemy.orm import Session

    with owner.connect() as db:
        db.execute(text('SET TRANSACTION READ ONLY'))
        value = db.execute(text('SELECT id,requester_user_id,requester_person_id,status,revision_no,'
            'purpose,contact_snapshot_jsonb FROM public.material_requests')).one()
        request_id, user, person, status, revision, purpose, envelope = value
        require((status, revision, purpose) == ('draft', 1, 'Synthetic contact 0181 proof'),
            'resume_exact_synthetic_draft')
        material, sku = db.execute(text('SELECT m.id,m.sku_code FROM public.materials m '
            'JOIN public.material_request_lines l ON l.material_id=m.id WHERE l.request_id=:id'),
            {'id': request_id}).one()
        require(re.fullmatch('C2-SKU-[a-f0-9]{12}', sku) is not None, 'resume_exact_synthetic_material')
        run_id = sku.removeprefix('C2-SKU-')
        require(db.scalar(text('SELECT count(*) FROM public.source_systems WHERE code=:code'),
            {'code': 'C2-CONTACT-' + run_id}) == 1, 'resume_exact_synthetic_source')
        require(db.execute(text('SELECT request_id,operation,actor_user_id,actor_person_id '
            'FROM public.material_request_commands')).all() == [(request_id, 'create', user, person)],
            'resume_only_completed_create_command')
        require(all(not values for values in rows(db, PIN_TABLES + INVENTORY_TABLES).values()),
            'resume_no_pins_or_inventory')
        context = dict(user=user, person=person, material=material)
        require(envelope == draft_value(context, request_id, False).contact_envelope,
            'resume_exact_synthetic_v1_envelope')
    prefix = 'contact-0181-' + run_id
    with Session(api) as db:
        actor = load_formal_principal(db, user)
        require(request_id == derive_material_request_create_id(actor=actor,
            idempotency_key=prefix + '-mixed-create', idempotency_hmac_secret=SECRET),
            'resume_exact_completed_create_identity')
    return context, request_id, prefix


def run(owner, api, *, upgrade_to_head, validate_runtime, resume_predecessor=False):
    """Run only after the caller proves a fresh owned cluster at 0180.

    Engine coordinates and role bootstrap come from that caller. Hosted CI
    uses the existing exact GitHub/disposable boundary; native validation must
    use its own owned-cluster boundary, never counterfeit GitHub markers.
    """
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from migration_script_cache import cache_migration_compilation
    from unittest.mock import patch
    import os

    report = dict(schema='rsc.contact-0181.pg16.v2', productionReady=False,
        syntheticOnly=True, realProviderContacted=False, oldBusinessSuitesRun=False)
    for engine, role in ((owner, OWNER), (api, API)):
        with engine.connect() as db:
            identity = db.execute(text("SELECT current_user,session_user,current_setting('server_version_num')::int")).one()
            require(identity[:2] == (role, role) and 160000 <= identity[2] < 170000,
                'direct_pg16_' + role)
            require(not any(db.execute(text('SELECT rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls '
                'FROM pg_roles WHERE rolname=current_user')).one()), 'unprivileged_' + role)
    migration = runpy.run_path(str(WORKSPACE / 'backend/alembic/versions/20261230_0181_material_request_contact_v2.py'))
    data = migration['DATA']
    require(type(resume_predecessor) is bool, 'explicit_predecessor_resume')
    with owner.connect() as db:
        limits(db); head(db, OLD); exact_catalog(db, data, 'before')
        if not resume_predecessor:
            require(all(not values for values in rows(db, BUSINESS_TABLES + INVENTORY_TABLES).values()),
                'empty_contact_and_inventory_predecessor')
    if resume_predecessor:
        context, request_id, prefix = _resume_v1_predecessor(owner, api)
        report['resumedExactV1PredecessorWithoutReplayingCreate'] = True
    else:
        run_id = uuid4().hex[:12]
        prefix = 'contact-0181-' + run_id
        context = foundation_context(owner, run_id)
        request_id, prepared = create_request(api, context, prefix + '-mixed-create', False)
    with owner.connect() as db:
        head(db, OLD); exact_catalog(db, data, 'before')
        before = rows(db, BUSINESS_TABLES + PIN_TABLES)
        require({name: len(before[name]) for name in BUSINESS_TABLES} == {
            'material_requests': 1, 'material_request_revisions': 1}, 'one_real_v1_draft_pair')
        require(all(json.loads(row)['contact_snapshot_jsonb']['schema'] == 'rsc.material_request_contact.v1'
            for name in BUSINESS_TABLES for row in before[name]), 'only_v1_predecessor_rows')
    upgrade_to_head()
    with owner.connect() as db:
        head(db, HEAD); exact_catalog(db, data, 'after')
        require(rows(db, BUSINESS_TABLES + PIN_TABLES) == before, 'upgrade_preserves_v1_and_pins')
    report['v1UpgradePreserved'] = True
    with ExitStack() as stack:
        stack.enter_context(patch.dict(os.environ, {'OAM_MIGRATION_CACHE_EXECUTION': '1'}))
        cache = stack.enter_context(cache_migration_compilation(WORKSPACE / 'backend/alembic/versions'))
        require(cache.enabled and cache.execution_enabled, 'bounded_discovery_cache')
        configuration = Config(str(WORKSPACE / 'alembic.ini'))
        configuration.set_main_option('script_location', str(WORKSPACE / 'backend/alembic'))
        scripts = ScriptDirectory.from_config(configuration)
        require(scripts.get_heads() == [HEAD], 'exact_source_head')
        with owner.connect() as db:
            transaction = db.begin()
            try:
                limits(db)
                db.execute(text('SET LOCAL search_path=pg_catalog,public'))
                txid = db.scalar(text('SELECT txid_current()'))
                require(db.scalar(text('SELECT current_schema()')) == 'pg_catalog', 'catalog_first_focus')
                online_step(db, configuration, scripts, False); exact_catalog(db, data, 'before')
                online_step(db, configuration, scripts, True); exact_catalog(db, data, 'after')
                require(db.scalar(text('SELECT txid_current()')) == txid and transaction.is_active, 'one_outer_transaction')
                require(rows(db, BUSINESS_TABLES + PIN_TABLES) == before, 'roundtrip_preserves_rows')
            finally:
                transaction.rollback()
        with owner.connect() as db:
            head(db, HEAD); exact_catalog(db, data, 'after')
            require(rows(db, BUSINESS_TABLES + PIN_TABLES) == before, 'fresh_outer_rollback_readback')
        report['transactionalRoundtrip'] = True
        report['migrationWriterLock'] = migration_writer_lock_proof(owner, api, configuration, scripts, data, request_id)
        validate_runtime()
        report['pin'] = seed_pin(owner)
        report['permissionDenials'] = api_permissions(api)
        with owner.connect() as db:
            expected_pins = rows(db, PIN_TABLES)
        old_revision_id, version = submit_return(api, context, request_id, prefix + '-v1')
        old_sealed = revision_bytes(owner, old_revision_id)
        require(json.loads(old_sealed)['status'] == 'sealed', 'old_v1_revision_sealed')
        mixed = amend(api, context, request_id, version, prefix + '-mixed-amend', True)
        require(revision_bytes(owner, old_revision_id) == old_sealed, 'v1_sealed_revision_unchanged')
        report['guardRejections'] = tamper_matrix(api, request_id, mixed.revision_id, context['person'])
        with owner.connect() as db:
            current_v2 = rows(db, BUSINESS_TABLES + PIN_TABLES)
        report['downgradeCurrentV2'] = downgrade_blocked(owner, configuration, scripts, 'current-v2')
        with owner.connect() as db:
            head(db, HEAD); exact_catalog(db, data, 'after')
            require(rows(db, BUSINESS_TABLES + PIN_TABLES) == current_v2, 'current_v2_refusal_exact_readback')
        v2_revision, version = submit_return(api, context, request_id, prefix + '-v2')
        retained_v2 = revision_bytes(owner, v2_revision)
        amend(api, context, request_id, version, prefix + '-explicit-v1-amend', False)
        require(revision_bytes(owner, v2_revision) == retained_v2, 'sealed_v2_revision_unchanged')
        with owner.connect() as db:
            require(db.scalar(text("SELECT count(*) FROM material_requests WHERE contact_snapshot_jsonb->>'schema' <> 'rsc.material_request_contact.v1'")) == 0,
                'history_blocker_has_no_current_v2')
            require(db.scalar(text("SELECT count(*) FROM material_request_revisions WHERE contact_snapshot_jsonb->>'schema' = 'rsc.material_request_contact.v2'")) == 1,
                'history_blocker_has_one_sealed_v2')
            history_v2 = rows(db, BUSINESS_TABLES + PIN_TABLES)
        report['downgradeOldV2History'] = downgrade_blocked(owner, configuration, scripts, 'only-old-v2-history')
        with owner.connect() as db:
            head(db, HEAD); exact_catalog(db, data, 'after')
            require(rows(db, BUSINESS_TABLES + PIN_TABLES) == history_v2, 'historical_v2_refusal_exact_readback')
        pure_id, pure = create_request(api, context, prefix + '-v2-create', True)
        with owner.connect() as db:
            require(db.scalar(text('SELECT contact_snapshot_jsonb FROM material_requests WHERE id=:id'),
                {'id': pure_id}) == v2(pure_id, context['person']), 'pure_v2_current_insert')
            require(json.loads(revision_bytes(owner, pure.revision_id))['contact_snapshot_jsonb'] == v2(pure_id, context['person']),
                'pure_v2_revision_insert')
        validate_runtime()
        with owner.connect() as db:
            head(db, HEAD); exact_catalog(db, data, 'after')
            require(rows(db, PIN_TABLES) == expected_pins, 'immutable_pins_final_readback')
            require(revision_bytes(owner, old_revision_id) == old_sealed, 'immutable_v1_final_readback')
            require(revision_bytes(owner, v2_revision) == retained_v2, 'immutable_v2_final_readback')
            require(all(not values for values in rows(db, INVENTORY_TABLES).values()), 'no_inventory_business_executed')
            report['finalRows'] = summary(rows(db, BUSINESS_TABLES + PIN_TABLES))
    report.update(result='passed', actualPostgreSQL16=True, finalHead=HEAD, runtimeSecurityAfter=True)
    return report
