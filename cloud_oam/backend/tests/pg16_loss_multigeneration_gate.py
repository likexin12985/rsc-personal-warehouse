"""Formal migration, API transactions, recovery and retained-history gate."""
import json
from sqlalchemy import text

from app.database_security import validate_production_database_security
from pg16_stock_loss_sources_gate import run as opening_and_sources
from pg16_loss_correction_gate import stock_snapshot
import pg16_loss_multigeneration_fixture as fixture
import pg16_loss_multigeneration_business as business
import pg16_loss_multigeneration_races as races


from test_postgresql16_release_gate import HEAD_REVISION as HEAD
PREVIOUS = '20261208_0159'
RETENTION_ERROR = '0161 immutable correction request history requires retention'


def assert_current_runtime(owner, api):
    validate_production_database_security(api, expected_runtime_role='star_oam_api',
        expected_migration_role='star_oam_migrator')
    with api.connect() as db:
        assert db.scalar(text('SELECT current_user')) == 'star_oam_api'
    # The runtime deliberately has no direct access to migration metadata.
    with owner.connect() as db:
        assert db.scalars(text('SELECT version_num FROM alembic_version')).all() == [HEAD]


def release(engines, *, tracking, migrate, provision, scenario='generations'):
    if tracking not in ('quantity', 'serial') or scenario not in ('generations', 'seal_retention'):
        raise ValueError('explicit multigeneration tracking and scenario required')
    owner, api = (engines[name] for name in ('star_oam_migrator', 'star_oam_api'))

    def stable():
        with owner.connect() as db:
            return dict(
                stock=stock_snapshot(owner),
                bindings=db.execute(text('SELECT to_jsonb(t) FROM stock_loss_request_key_bindings t ORDER BY fact_id')).scalars().all(),
                seals=db.execute(text('SELECT to_jsonb(t) FROM stock_loss_inverse_request_seals t ORDER BY id')).scalars().all(),
                functions=db.execute(text("SELECT to_jsonb(p) FROM pg_proc p WHERE pronamespace='public'::regnamespace ORDER BY oid")).scalars().all(),
                revision=db.scalar(text('SELECT version_num FROM alembic_version')),
            )

    def startup():
        assert_current_runtime(owner, api)

    migrate('upgrade-multigeneration', 'upgrade', 'head')
    provision()
    startup()
    from pg16_loss_correction_request_gate import snapshot as normalized_snapshot
    empty = normalized_snapshot(owner)
    original_function_ids = {(row["proname"], json.dumps(row["proargtypes"],sort_keys=True)):row["oid"] for row in stable()["functions"]}
    migrate('empty-downgrade', 'downgrade', PREVIOUS)
    migrate('empty-reupgrade', 'upgrade', 'head')
    assert normalized_snapshot(owner) == empty
    recreated_function_ids = sorted(row["proname"] for row in stable()["functions"]
        if original_function_ids.get((row["proname"],json.dumps(row["proargtypes"],sort_keys=True))) != row["oid"])
    startup()

    captured = {}

    def original(context):
        captured['context'], captured['original'] = context, fixture.exercise(context)
        return dict(passed=True, originalApiPostingCommitted=True)

    baseline = opening_and_sources(engines, tracking=tracking, after_preview=original)
    assert baseline['passed'] and baseline['submission']['passed']
    assert baseline['migrationHead'] == HEAD
    completed = []

    def roundtrip_first_history(generation, command, expected):
        if generation != 0:
            return
        before = stable()
        # 0161 retains every bound original request, including generation one.
        migrate('first-history-downgrade-denied', 'downgrade', PREVIOUS, expected=RETENTION_ERROR)
        assert stable() == before
        startup()
        found = business.readonly(api, captured['context'], command)
        assert found['request_state'] == 'found' and found['result'] == expected
        assert found['retry_allowed'] is False
        completed.append(True)

    result = business.run(captured['context'], captured['original'],
        seal_only=scenario == 'seal_retention', after_correction=roundtrip_first_history)
    assert completed == [True]
    if scenario == 'generations':
        result['laterInverseConcurrency'] = races.run(captured['context'], captured['original'])
        assert result['laterInverseConcurrency']['passed']
    else:
        assert result['laterSealWithoutLaterInverse']
    before = stable()
    migrate('later-history-downgrade-denied', 'downgrade', PREVIOUS, expected=RETENTION_ERROR)
    assert stable() == before and before['revision'] == HEAD
    startup()
    result.update(scenario=scenario, actualAlembicRevision=HEAD,
        emptyMigrationRoundtrip=True, emptyRoundtripRecreatedFunctionIdentities=recreated_function_ids,
        firstGenerationDowngradeRejectedWithHistoryUnchanged=True,
        laterGenerationDowngradeRejected=True, failedDowngradeExactReadback=True,
        runtimeStartupSecurity=True, originalOpeningAndReport=baseline,
        formalMigrationApplied=True, canonicalServiceImports=True,
        syntheticBusinessPermissions=True, publicHttpAcceptance=False,
        productionPerformanceAccepted=False, productionAcceptance=False)
    return result
