"""Real Alembic 0168 and default API admission on newly owned PostgreSQL 16."""
from pathlib import Path
import runpy
from sqlalchemy import text

from app import database_security as security
from app import stock_scrap_security as scrap
from pg16_scrap_transition_gate import TRANSITION, catalog, runtime_admission
from pg16_stock_operation_permission_policy import assert_fresh_defaults

READY = runpy.run_path(str(Path(__file__).parents[1] / 'alembic/stock_scrap_0165/readiness.py'))


def forward_admission(engines):
    with engines['star_oam_api'].connect() as db:
        scrap.verify(db)
        # Surface exact catalog-only errors before the public startup wrapper
        # deliberately hides internal security diagnostics.
        for name in ('_loss_correction_catalog', '_return_inbound_quality_catalog',
                '_loss_return_stop_catalog', '_authentication_fence_catalog', '_stock_scrap_readiness'):
            try:
                getattr(security, name).verify(db)
            except ValueError as error:
                raise ValueError(name + ': ' + str(error)) from error
    runtime_admission(engines)


def release(engines, *, tracking, migrate, provision, historical_admission=None):
    migrate('predecessor-0164', 'upgrade', '20261213_0164')
    owner = engines['star_oam_migrator']
    with owner.connect() as db:
        TRANSITION['verify'](db, 'before')
        READY['verify'](db, 'before')
    migrate('formal-upgrade-0170', 'upgrade', '20261230_0181')
    provision()
    with owner.connect() as db:
        assert db.scalar(text('SELECT version_num FROM alembic_version')) == '20261230_0181'
        security._stock_scrap_readiness.verify(db)
        permission_proof = assert_fresh_defaults(db)
    forward_admission(engines)
    print('formal 0168 Alembic and default API admission including old independent verifiers PASS', flush=True)
    before = catalog(owner)
    changed = next(c['after'] for c in scrap.DATA['functions'].values() if c['before'])
    new = next(c['after'] for c in scrap.DATA['functions'].values() if c['before'] is None)
    signature = TRANSITION['function_name'](new)
    private_signature = 'public.rsc_check_scrap_execution_events_with_audit_0165(uuid,uuid[])'
    history_signature = 'public.rsc_scrap_history_from_proof_0165(text,uuid,uuid,jsonb)'
    root_signatures = {
        'internal_root_fact_api_acl': 'public.rsc_scrap_fact_with_root_proof_0165(text,uuid,uuid,uuid[])',
        'internal_root_upstream_api_acl': 'public.rsc_scrap_upstream_with_audit_0165(uuid,uuid[])',
    }
    cases = (
        ('new_function_public_acl', 'GRANT EXECUTE ON FUNCTION ' + signature + ' TO PUBLIC'),
        ('internal_audit_proof_api_acl', 'GRANT EXECUTE ON FUNCTION ' + private_signature + ' TO star_oam_api'),
        ('internal_history_proof_api_acl', 'GRANT EXECUTE ON FUNCTION ' + history_signature + ' TO star_oam_api'),
        *((name, 'GRANT EXECUTE ON FUNCTION ' + signature + ' TO star_oam_api') for name, signature in root_signatures.items()),
        ('registry_insert_acl', 'GRANT INSERT ON stock_scrap_request_key_bindings TO star_oam_api'),
        ('seal_insert_acl', 'GRANT INSERT ON stock_scrap_request_seals TO star_oam_api'),
        ('same_name_trigger_on_one_table', 'ALTER TABLE stock_scrap_recovery_requests DISABLE TRIGGER trg_scrap_seal_fence_0165'),
        ('replaced_old_function_body', changed['definition'].replace(changed['prosrc'], changed['prosrc'] + '\n')),
        ('legacy_alias_constraint', 'ALTER TABLE stock_loss_request_key_bindings DROP CONSTRAINT ck_loss_binding_recovery_0165'),
        ('unexpected_function_overload', 'CREATE FUNCTION public.rsc_scrap_seal_payload_0165(text) RETURNS jsonb LANGUAGE sql AS $$ SELECT NULL::jsonb $$'),
        ('old_readiness_body', READY['DATA']['before']['definition']),
    )
    rejected = []
    for name, statement in cases:
        with owner.begin() as db:
            db.execute(text(statement))
        try:
            forward_admission(engines)
        except (ValueError, security.DatabaseSecurityBoundaryError):
            rejected.append(name)
        else:
            raise AssertionError('forward runtime accepted ' + name)
        # Undo only the exact test mutation, retaining the actual migration.
        with owner.begin() as db:
            if name == 'new_function_public_acl':
                db.execute(text('REVOKE EXECUTE ON FUNCTION ' + signature + ' FROM PUBLIC'))
            elif name == 'internal_audit_proof_api_acl':
                db.execute(text('REVOKE EXECUTE ON FUNCTION ' + private_signature + ' FROM star_oam_api'))
            elif name == 'internal_history_proof_api_acl':
                db.execute(text('REVOKE EXECUTE ON FUNCTION ' + history_signature + ' FROM star_oam_api'))
            elif name in root_signatures:
                db.execute(text('REVOKE EXECUTE ON FUNCTION ' + root_signatures[name] + ' FROM star_oam_api'))
            elif name in ('registry_insert_acl', 'seal_insert_acl'):
                table = 'stock_scrap_request_key_bindings' if name == 'registry_insert_acl' else 'stock_scrap_request_seals'
                db.execute(text('REVOKE INSERT ON ' + table + ' FROM star_oam_api'))
            elif name == 'same_name_trigger_on_one_table':
                db.execute(text('ALTER TABLE stock_scrap_recovery_requests ENABLE ALWAYS TRIGGER trg_scrap_seal_fence_0165'))
            elif name == 'replaced_old_function_body':
                db.execute(text(changed['definition']))
            elif name == 'legacy_alias_constraint':
                prefix = 'ALTER TABLE public.stock_loss_request_key_bindings ADD CONSTRAINT ck_loss_binding_recovery_0165 CHECK ('
                sql = [s for s in TRANSITION['DATA']['statements'] if prefix in s]
                assert len(sql) == 1 and sql[0].count(prefix) == 1
                # This single frozen CHECK has no quoted semicolons. Preserve
                # its literal expression rather than PostgreSQL's deparse.
                db.execute(text(prefix + sql[0].split(prefix)[1].split(';', 1)[0]))
            elif name == 'unexpected_function_overload':
                db.execute(text('DROP FUNCTION public.rsc_scrap_seal_payload_0165(text)'))
            else:
                db.execute(text(security._stock_scrap_readiness.DATA['after']['definition']))
        assert catalog(owner) == before, name + ' did not restore exact catalog'
        forward_admission(engines)
    print(str(len(rejected))+' real API runtime catalog rejections and exact restorations PASS', flush=True)
    migrate('formal-empty-downgrade-0164', 'downgrade', '20261213_0164')
    with owner.connect() as db:
        TRANSITION['verify'](db, 'before')
        READY['verify'](db, 'before')
        assert db.scalar(text('SELECT version_num FROM alembic_version')) == '20261213_0164'
    old_runtime = historical_admission() if historical_admission else None
    if old_runtime:
        print('preserved 0164 default API startup after formal downgrade PASS', flush=True)
    try:
        runtime_admission(engines)
    except security.DatabaseSecurityBoundaryError:
        pass
    else:
        raise AssertionError('new application admitted an old schema')
    migrate('formal-reupgrade-0170', 'upgrade', '20261230_0181')
    assert catalog(owner) == before
    forward_admission(engines)
    print('formal empty downgrade restores 0164 and reupgrade restores default admission PASS', flush=True)
    return dict(passed=True, scope='formal-runtime-catalog', fullApiAdmission=True,
        oldIndependentVerifiersRetained=True, runtimeRejections=rejected,
        newTables=len(scrap.TABLES), newFunctions=sum(c['before'] is None for c in scrap.DATA['functions'].values()),
        replacedFunctions=sum(c['before'] is not None for c in scrap.DATA['functions'].values()),
        formalRevisionActivated=True, alembicEmptyRoundtrip=True, newApplicationRejectsOldSchema=True,
        oldApplicationAfterDowngradeVerified=bool(old_runtime), predecessorRuntime=old_runtime,
        formalPermissionDefaults=permission_proof,
        fullBusinessRerun=False, productionAcceptance=False)
