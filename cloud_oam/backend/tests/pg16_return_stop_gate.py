"""Shared formal 0163 acceptance for acknowledged CI and owned local PG16."""
from unittest.mock import patch
from sqlalchemy import text
from pg16_loss_correction_request_gate import snapshot, assert_current_runtime
from pg16_stock_loss_sources_gate import run as opening_and_sources
from pg16_return_stop_fixture import prepare
from test_postgresql16_release_gate import HEAD_REVISION

SCENARIOS = ('seals', 'http', 'negative', 'seal_first', 'execute_first', 'stop_first', 'outbound_first')


def release(engines, *, tracking, scenario, migrate, provision):
    if tracking not in ('quantity', 'serial') or scenario not in SCENARIOS:
        raise ValueError('explicit return stop tracking and scenario required')
    if HEAD_REVISION != '20261213_0164':
        raise ValueError('return stop gate requires review for the new migration head')
    owner, api = (engines[k] for k in ('star_oam_migrator', 'star_oam_api'))
    migrate('return-stop-upgrade', 'upgrade', 'head')
    provision()
    assert_current_runtime(owner, api)
    if scenario == 'seals':
        before = snapshot(owner)
        migrate('empty-return-stop-downgrade', 'downgrade', '20261211_0162')
        migrate('empty-return-stop-reupgrade', 'upgrade', 'head')
        assert snapshot(owner) == before
        assert_current_runtime(owner, api)

    def verify_retention(label):
        before = snapshot(owner)
        with api.connect() as db:
            stops = db.scalar(text('SELECT count(*) FROM stock_loss_return_stops'))
            seals = db.scalar(text('SELECT count(*) FROM stock_loss_inverse_request_seals'))
            assert seals == 1 and stops == (0 if label == 'seal-only' else 1)
        message = ('0163 immutable return inverse seal history requires retention' if label == 'seal-only'
            else '0163 immutable business history requires retention: stock_loss_return_stops')
        migrate('retained-return-stop-' + label, 'downgrade', '20261211_0162', expected=message)
        assert snapshot(owner) == before
        assert_current_runtime(owner, api)

    def exercise(context):
        values = prepare(context)
        command, original, original_result, root_id = values[:4]
        if scenario == 'negative':
            from pg16_return_stop_business import exercise as check
            return check(context, *values)
        if scenario in ('stop_first', 'outbound_first'):
            from pg16_return_stop_outbound_races import exercise as check
            return check(context, command, root_id, winner=scenario.removesuffix('_first'))
        if scenario in ('seal_first', 'execute_first'):
            from pg16_return_stop_seal_races import exercise as check
            return check(context, command, original, original_result, root_id, winner=scenario.removesuffix('_first'))
        if scenario == 'http':
            from pg16_return_stop_http import exercise as check
        else:
            from pg16_return_stop_seals import exercise as check
            context['verify_retention'] = verify_retention
        return check(context, command, original, original_result, root_id)

    if scenario in ('stop_first', 'outbound_first'):
        import pg16_stock_loss_return_outbound_gate as outbound
        with patch.object(outbound, 'exercise', exercise):
            result = outbound.run_sources(engines, tracking=tracking)
    else:
        result = opening_and_sources(engines, tracking=tracking, after_preview=exercise)
    assert result['passed'] and result['submission']['passed']
    assert result['migrationHead'] == HEAD_REVISION
    assert_current_runtime(owner, api)
    result.update(scenario=scenario, formalMigrationApplied=True,
        emptyMigrationRoundtrip=scenario == 'seals',
        retainedHistoryDowngradeChecked=scenario == 'seals',
        runtimeStartupSecurityBeforeAndAfter=True, syntheticLoginIdentity=True,
        publicHttpAcceptance=scenario == 'http', productionAcceptance=False)
    return result
