"""The named release check cannot pass when any independent gate is absent."""
from itertools import product
from pathlib import Path
import os
import re
import subprocess
import sys
import pytest

ROOT=Path(__file__).resolve().parents[3]
WORKFLOW=ROOT/'.github/workflows/postgresql16-release-gate.yml'


def _jobs():
    text=WORKFLOW.read_text()
    sections=re.split(r'^  ([a-zA-Z0-9_-]+):\s*$',text.split('jobs:\n',1)[1],flags=re.MULTILINE)
    return dict(zip(sections[1::2],sections[2::2]))


def test_runtime_loss_and_static_jobs_are_independent_and_named_check_requires_all():
    triggers=WORKFLOW.read_text().split('permissions:\n',1)[0]
    for event in ('pull_request','push'):
        trigger=triggers.split(f'  {event}:\n',1)[1]
        trigger=re.split(r'^  [a-z_]+:',trigger,maxsplit=1,flags=re.MULTILINE)[0]
        paths=re.findall(r'^      - "([^"\n]+)"$', trigger, re.MULTILINE)
        assert paths == [
            ".github/workflows/postgresql16-release-gate.yml", "cloud_oam/**"
        ]
        if event == 'push':
            branches = re.findall(
                r'^      - ([^"\s][^\n]*)$', trigger.split('    paths:\n', 1)[0],
                re.MULTILINE,
            )
            assert branches == [
                'main', 'codex/production-readiness-gates',
                'codex/notification-delivery-worker',
            ]
    jobs=_jobs()
    assert set(jobs)=={'pg16_runtime','pg16_loss','pg16_condition','static_safety','postgresql16-release-gate'}
    runtime,static,aggregate=(jobs[key] for key in ('pg16_runtime','static_safety','postgresql16-release-gate'))
    loss=jobs['pg16_loss']
    condition=jobs['pg16_condition']
    assert not re.search(r'^    (needs|if|continue-on-error):',runtime+'\n'+static+'\n'+loss+'\n'+condition,re.MULTILINE)
    assert 'python -m pytest -q tests/test_postgresql16_release_gate.py -s' in runtime
    assert '    timeout-minutes: 360\n' in runtime
    assert '    strategy:\n      fail-fast: false\n      matrix:\n        suite: [migrations, inventory, control, contact_envelope]\n' in runtime
    assert 'RSC_PG16_RUNTIME_SUITE: ${{ matrix.suite }}' in runtime
    assert 'continue-on-error:' not in runtime and 'exclude:' not in runtime
    assert 'RSC_PG16_GATE_ACKNOWLEDGE_DISPOSABLE: I_UNDERSTAND_THIS_DATABASE_IS_EPHEMERAL' in runtime
    assert 'postgres:16-alpine@sha256:' in runtime
    assert '    strategy:\n      fail-fast: false\n      matrix:\n        tracking: [quantity, serial]\n' in loss
    assert 'RSC_PG16_LOSS_TRACKING: ${{ matrix.tracking }}' in loss
    assert '        flow: [submission, submission_http, review_seals, disposition, return_preview, return_submission, return_outbound, return_shipment, return_receipt, sender_http, sender_seals, execution_seals, correction_restore, correction_used, correction_damaged, correction_generations, correction_seal_retention, execution_http_disposition, execution_http_return, correction_request_seals, correction_http_sources, return_quality_whole, return_quality_mixed, return_stop_seals, return_stop_http, return_stop_negative, return_stop_seal_first, return_stop_execute_first, return_stop_stop_first, return_stop_outbound_first, scrap_http]\n' in loss
    assert 'RSC_PG16_LOSS_FLOW: ${{ matrix.flow }}' in loss
    assert 'python -m pytest -q -s tests/test_postgresql16_stock_loss_release_gate.py' in loss
    assert '        working-directory: cloud_oam/backend\n' in loss
    # An independent service per matrix leg must retain the original fresh
    # database, role passwords, loopback host and acknowledgement boundary.
    assert runtime.split('    services:\n',1)[1].split('    steps:\n',1)[0].replace(
        '      RSC_PG16_RUNTIME_SUITE: ${{ matrix.suite }}\n', '') == (
        loss.split('    services:\n',1)[1].split('    steps:\n',1)[0].replace(
            '      RSC_PG16_LOSS_TRACKING: ${{ matrix.tracking }}\n','').replace(
            '      RSC_PG16_LOSS_FLOW: ${{ matrix.flow }}\n',''))
    assert 'RSC_PG16_GATE_' not in static and 'services:' not in static
    assert 'tracking: [quantity, serial]' in condition
    assert 'flow: [condition_http, legacy_history, fulfillment_http]' in condition
    assert 'fetch-depth: 0' in condition and 'persist-credentials: false' in condition
    assert 'prepare_condition_predecessor.py' in condition
    assert 'run_local_pg16_return_condition_checks.py' in condition
    assert '--formal-http --tracking ${{ matrix.tracking }}' in condition
    assert '--legacy-history-only --tracking ${{ matrix.tracking }}' in condition
    assert 'run_local_pg16_scrap_business_checks.py' in condition
    assert 'run_local_pg16_fulfillment_checks.py' in condition
    assert "if: ${{ matrix.flow != 'fulfillment_http' }}" in condition
    business_step = condition.split('      - name: Run selected migration and business gate\n', 1)[1]
    assert '        if:' not in business_step
    assert 'fulfillment_http)' in business_step
    assert 'condition_http) revision=0157' in condition
    assert 'legacy_history) revision=0164' in condition
    assert '--revision 0164 --rollback-compatibility' in condition
    assert '--rollback-source "$GITHUB_WORKSPACE/cloud_oam/artifacts/condition-ci-rollback-0164/source/cloud_oam"' in condition
    assert '--postgres-bin /usr/lib/postgresql/16/bin' in condition
    assert 'postgresql-16' in condition and 'services:' not in condition
    assert 'continue-on-error:' not in condition and 'exclude:' not in condition
    assert 'RSC_PG16_GATE_HOST' not in condition
    # The three matrix legs must run the same dynamically discovered file set
    # with disjoint assignments. The named aggregate requires every leg.
    assert '    strategy:\n      fail-fast: false\n      matrix:\n        shard: [0, 1, 2]\n' in static
    step=static.split('      - name: Run complete backend and edge static safety gates\n',1)[1]
    assert '        working-directory: cloud_oam\n' in step
    assert step.split('        run: |\n',1)[1].strip() == (
        'python scripts/run_static_shard.py --index ${{ matrix.shard }} --count 3')
    assert 'RSC_NATIVE_PG16_BIN: /usr/lib/postgresql/16/bin' in step
    assert 'postgresql-16 postgresql-client-16 zsh' in static
    assert '-r cloud_oam/scripts/requirements-public-knowledge.txt' in static
    assert '    timeout-minutes: 360\n' in static
    assert '    if: ${{ always() }}\n' in aggregate
    assert '    needs: [pg16_runtime, pg16_loss, pg16_condition, static_safety]\n' in aggregate
    assert 'RUNTIME_RESULT: ${{ needs.pg16_runtime.result }}' in aggregate
    assert 'LOSS_RESULT: ${{ needs.pg16_loss.result }}' in aggregate
    assert 'CONDITION_RESULT: ${{ needs.pg16_condition.result }}' in aggregate
    assert 'STATIC_RESULT: ${{ needs.static_safety.result }}' in aggregate
    assert 'continue-on-error:' not in aggregate


def test_static_shards_discover_every_test_module_exactly_once():
    cloud=ROOT/'cloud_oam'
    expected={str(path.relative_to(cloud)) for scope in ('backend/tests','edge_sync')
              for path in (cloud/scope).rglob('test_*.py') if path.is_file()}
    runtimes={'backend/tests/test_postgresql16_release_gate.py',
              'backend/tests/test_postgresql16_stock_loss_release_gate.py'}
    assert runtimes.issubset(expected)
    observed=[]
    for index in range(3):
        result=subprocess.run([sys.executable,'scripts/run_static_shard.py','--index',str(index),
                               '--count','3','--list'],cwd=cloud,capture_output=True,text=True,check=True)
        selected=result.stdout.splitlines()
        assert selected and runtimes.isdisjoint(selected)
        observed.extend(selected)
    assert len(observed)==len(set(observed))
    assert set(observed)==expected-runtimes


def test_runtime_dispatch_rejects_unacknowledged_context_before_any_database(monkeypatch):
    import test_postgresql16_release_gate as gate
    monkeypatch.setattr(gate, '_gate_enabled', lambda: False)
    def forbidden(*args, **kwargs):
        raise AssertionError('unacknowledged runtime reached suite selection or database')
    monkeypatch.setattr(gate, '_selected_runtime_suite', forbidden)
    monkeypatch.setattr(gate, '_assert_fresh_disposable_postgresql16', forbidden)
    with pytest.raises(pytest.fail.Exception, match='acknowledged disposable hosted'):
        gate.test_postgresql16_migration_acl_concurrency_and_kill_gate()


def test_unacknowledged_pytest_entry_fails_instead_of_reporting_a_green_skip():
    # Exercise pytest's real collection/mark handling, not just a direct call.
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith('RSC_PG16_')
                   and key not in ('GITHUB_ACTIONS', 'RUNNER_ENVIRONMENT', 'PYTEST_ADDOPTS')}
    result = subprocess.run([sys.executable, '-m', 'pytest', '-q',
        'tests/test_postgresql16_release_gate.py'], cwd=ROOT/'cloud_oam/backend',
        env=environment, capture_output=True, text=True, timeout=90)
    assert result.returncode == 1, result.stdout + result.stderr
    assert 'acknowledged disposable hosted database' in result.stdout
    assert '1 failed' in result.stdout and '1 skipped' not in result.stdout


@pytest.mark.parametrize('suite', [None, '', 'all', 'inventory,control', 'production'])
def test_runtime_dispatch_rejects_missing_or_unknown_suite_before_database(monkeypatch, suite):
    import test_postgresql16_release_gate as gate
    monkeypatch.setattr(gate, '_gate_enabled', lambda: True)
    if suite is None:
        monkeypatch.delenv('RSC_PG16_RUNTIME_SUITE', raising=False)
    else:
        monkeypatch.setenv('RSC_PG16_RUNTIME_SUITE', suite)
    def forbidden(*args, **kwargs):
        raise AssertionError('invalid runtime selection reached database')
    for name in ('_run_migration_suite', '_run_inventory_suite', '_run_control_suite', '_run_contact_envelope_suite'):
        monkeypatch.setattr(gate, name, forbidden)
    with pytest.raises(ValueError, match='explicit migrations, inventory, control or contact_envelope'):
        gate.test_postgresql16_migration_acl_concurrency_and_kill_gate()


@pytest.mark.parametrize('suite', ['migrations', 'inventory', 'control', 'contact_envelope'])
def test_runtime_dispatch_runs_only_selected_suite_and_propagates_failure(monkeypatch, suite):
    import test_postgresql16_release_gate as gate
    monkeypatch.setattr(gate, '_gate_enabled', lambda: True)
    monkeypatch.setenv('RSC_PG16_RUNTIME_SUITE', suite)
    calls = []
    def selected():
        calls.append(suite)
        raise RuntimeError('selected suite failed')
    def forbidden():
        raise AssertionError('executed an unselected suite')
    for key, name in (('migrations', '_run_migration_suite'),
                      ('inventory', '_run_inventory_suite'), ('control', '_run_control_suite'),
                      ('contact_envelope', '_run_contact_envelope_suite')):
        monkeypatch.setattr(gate, name, selected if key == suite else forbidden)
    with pytest.raises(RuntimeError, match='selected suite failed'):
        gate.test_postgresql16_migration_acl_concurrency_and_kill_gate()
    assert calls == [suite]


@pytest.mark.parametrize('suite', ['migrations', 'inventory', 'control', 'contact_envelope'])
def test_runtime_suite_proves_fresh_database_before_bootstrap(monkeypatch, suite):
    import test_postgresql16_release_gate as gate
    class BoundaryReached(Exception):
        pass
    def boundary():
        raise BoundaryReached()
    def forbidden(*args, **kwargs):
        raise AssertionError('runtime suite skipped fresh database guard')
    monkeypatch.setattr(gate, '_assert_fresh_disposable_postgresql16', boundary)
    monkeypatch.setattr(gate, '_bootstrap_roles', forbidden)
    monkeypatch.setattr(gate, 'create_engine', forbidden)
    entry = {'migrations': gate._run_migration_suite, 'inventory': gate._run_inventory_suite,
             'control': gate._run_control_suite, 'contact_envelope': gate._run_contact_envelope_suite}[suite]
    with pytest.raises(BoundaryReached):
        entry()


def test_pg16_and_static_jobs_install_the_image_runtime_hash_lock():
    jobs=_jobs()
    for name in ('pg16_runtime','pg16_loss','pg16_condition','static_safety'):
        job=jobs[name]
        assert '-r cloud_oam/backend/requirements-build.lock' in job
        assert '-r cloud_oam/backend/requirements-linux-amd64.lock' in job
        assert '-r cloud_oam/backend/requirements-ci-test.lock' in job
        assert job.count('--require-hashes') >= 3
        assert '--only-binary=:all:' in job
        assert 'pytest==9.1.1 pglast==7.18 httpx==0.28.1' not in job
        assert '--no-build-isolation' in job
        assert 'python -m pip check' in job
        assert '-r cloud_oam/backend/requirements.txt' not in job


def test_aggregate_shell_rejects_every_incomplete_or_failed_combination():
    source=_jobs()['postgresql16-release-gate'].split('        run: |\n',1)[1]
    script='\n'.join(line[10:] for line in source.splitlines() if line.strip())
    statuses=('success','failure','cancelled','skipped','timed_out','in_progress','')
    for runtime,loss,condition,static in product(statuses,repeat=4):
        result=subprocess.run(['bash','-e','-c',script],env={
            'RUNTIME_RESULT':runtime,'LOSS_RESULT':loss,'CONDITION_RESULT':condition,'STATIC_RESULT':static},capture_output=True,check=False)
        assert (result.returncode==0)==(runtime==loss==condition==static=='success'),(runtime,loss,condition,static)


def test_loss_gate_refuses_unacknowledged_or_incorrect_hosted_context_before_database(monkeypatch):
    import pytest
    import test_postgresql16_stock_loss_release_gate as loss_gate

    def unexpected_database_access():
        raise AssertionError('unacknowledged loss gate reached database')

    monkeypatch.setattr(loss_gate.gate,'_assert_fresh_disposable_postgresql16',unexpected_database_access)
    settings={
        'RSC_PG16_GATE_ACKNOWLEDGE_DISPOSABLE':loss_gate.gate.ACKNOWLEDGEMENT,
        'GITHUB_ACTIONS':'true','RUNNER_ENVIRONMENT':'github-hosted',
    }
    monkeypatch.setenv('RSC_PG16_LOSS_TRACKING','quantity')
    for missing in settings:
        for key,value in settings.items():monkeypatch.setenv(key,value)
        monkeypatch.delenv(missing,raising=False)
        with pytest.raises(pytest.fail.Exception,match='acknowledged disposable'):
            loss_gate.test_postgresql16_stock_loss_release_gate()
    for key,value in settings.items():monkeypatch.setenv(key,value)
    monkeypatch.setenv('RUNNER_ENVIRONMENT','self-hosted')
    with pytest.raises(pytest.fail.Exception,match='acknowledged disposable'):
        loss_gate.test_postgresql16_stock_loss_release_gate()
    monkeypatch.setenv('RUNNER_ENVIRONMENT','github-hosted')
    for tracking in ('','both','quantity,serial','production'):
        monkeypatch.setenv('RSC_PG16_LOSS_TRACKING',tracking)
        with pytest.raises(pytest.fail.Exception,match='explicit quantity or serial'):
            loss_gate.test_postgresql16_stock_loss_release_gate()


def test_loss_gate_refuses_invalid_flow_before_database(monkeypatch):
    import pytest
    import test_postgresql16_stock_loss_release_gate as loss_gate
    monkeypatch.setattr(loss_gate.gate,'_gate_enabled',lambda:True)
    monkeypatch.setenv('RSC_PG16_LOSS_TRACKING','quantity')
    def unexpected_database_access():
        raise AssertionError('invalid flow reached database')
    monkeypatch.setattr(loss_gate.gate,'_assert_fresh_disposable_postgresql16',unexpected_database_access)
    for flow in ('','both','submission,disposition','production'):
        monkeypatch.setenv('RSC_PG16_LOSS_FLOW',flow)
        with pytest.raises(pytest.fail.Exception,match='explicit submission, submission_http, review_seals, disposition, return_preview, return_submission, return_outbound, return_shipment, return_receipt, sender_http, sender_seals, execution_seals, correction_restore, correction_used, correction_damaged, correction_generations, correction_seal_retention, execution_http_disposition, execution_http_return, correction_request_seals, correction_http_sources, return_quality_whole, return_quality_mixed, return_stop_seals, return_stop_http, return_stop_negative, return_stop_seal_first, return_stop_execute_first, return_stop_stop_first return_stop_outbound_first or scrap_http'):
            loss_gate.test_postgresql16_stock_loss_release_gate()


@pytest.mark.parametrize("flow", ("submission_http", "review_seals", "disposition", "return_preview", "return_submission", "return_outbound", "return_shipment", "return_receipt", "sender_http", "sender_seals", "execution_seals", "correction_restore", "correction_used", "correction_damaged", "correction_generations", "correction_seal_retention", "execution_http_disposition", "execution_http_return", "correction_request_seals", "correction_http_sources", "return_quality_whole", "return_quality_mixed", "return_stop_seals", "return_stop_http", "return_stop_negative", "return_stop_seal_first", "return_stop_execute_first", "return_stop_stop_first", "return_stop_outbound_first", "scrap_http"))
def test_return_leg_still_requires_the_real_disposable_database_boundary(monkeypatch, flow):
    import pytest
    import test_postgresql16_stock_loss_release_gate as loss_gate
    monkeypatch.setattr(loss_gate.gate, '_gate_enabled', lambda: True)
    monkeypatch.setenv('RSC_PG16_LOSS_TRACKING', 'quantity')
    monkeypatch.setenv('RSC_PG16_LOSS_FLOW', flow)
    class BoundaryReached(Exception):
        pass
    def boundary():
        raise BoundaryReached()
    def forbidden(*args, **kwargs):
        raise AssertionError('preview gate skipped its disposable-database boundary')
    monkeypatch.setattr(loss_gate.gate, '_assert_fresh_disposable_postgresql16', boundary)
    monkeypatch.setattr(loss_gate.gate, '_bootstrap_roles', forbidden)
    monkeypatch.setattr(loss_gate, 'create_engine', forbidden)
    with pytest.raises(BoundaryReached):
        loss_gate.test_postgresql16_stock_loss_release_gate()


def test_shared_return_preview_rejects_invalid_tracking_before_migration():
    import pytest
    from pg16_stock_loss_return_preview_gate import release
    def forbidden(*args, **kwargs):
        raise AssertionError('invalid preview tracking reached database setup')
    for tracking in ('', 'both', 'quantity,serial', 'production'):
        with pytest.raises(ValueError, match='tracking must be quantity or serial'):
            release({}, tracking=tracking, migrate=forbidden, provision=forbidden)


@pytest.mark.parametrize('name', ('pg16_loss_sender_http_gate','pg16_loss_sender_seal_gate','pg16_loss_execution_seal_gate'))
def test_sender_gate_invalid_tracking_is_rejected_before_migration(name):
    from importlib import import_module
    release=import_module(name).release
    def forbidden(*args,**kwargs):
        raise AssertionError('invalid sender tracking reached database setup')
    for tracking in ('','both','quantity,serial','production'):
        with pytest.raises(ValueError,match='tracking must be quantity or serial'):
            release({},tracking=tracking,migrate=forbidden,provision=forbidden)


@pytest.mark.parametrize('flow,expected', [('disposition', 'disposition'), ('return_submission', 'return')])
@pytest.mark.parametrize('tracking', ['quantity', 'serial'])
def test_execution_matrix_includes_original_command_recovery(monkeypatch, flow, expected, tracking):
    """Route both existing execution legs through the full recovery gate.

    This is wiring proof only; disposable PG16 runs prove the actual stock,
    permission and recovery checks in that shared driver.
    """
    from types import SimpleNamespace
    import test_postgresql16_stock_loss_release_gate as loss_gate
    import pg16_loss_disposition_recovery_gate as recovery

    monkeypatch.setenv('RSC_PG16_LOSS_FLOW', flow)
    monkeypatch.setenv('RSC_PG16_LOSS_TRACKING', tracking)
    monkeypatch.setattr(loss_gate.gate, '_gate_enabled', lambda: True)
    for name in ('_assert_fresh_disposable_postgresql16', '_bootstrap_roles', '_provision_edge_receiver_role'):
        monkeypatch.setattr(loss_gate.gate, name, lambda: None)
    monkeypatch.setattr(loss_gate.gate, '_role_password', lambda role: 'synthetic-only')
    monkeypatch.setattr(loss_gate.gate, '_sqlalchemy_url', lambda **kwargs: kwargs['role'])
    disposed = []
    monkeypatch.setattr(loss_gate, 'create_engine', lambda role, **kwargs:
        SimpleNamespace(dispose=lambda: disposed.append(role)))
    called = []
    def run(engines, **kwargs):
        called.append((tuple(engines), kwargs['tracking'], kwargs['flow']))
        assert callable(kwargs['migrate']) and callable(kwargs['provision'])
        return {'passed': True}
    monkeypatch.setattr(recovery, 'release', run)
    loss_gate.test_postgresql16_stock_loss_release_gate()
    roles = ('star_oam_migrator', 'star_oam_api', loss_gate.gate.EDGE_RECEIVER_ROLE)
    assert called == [(roles, tracking, expected)]
    assert tuple(disposed) == roles


@pytest.mark.parametrize('tracking,flow', [('', 'return'), ('both', 'disposition'), ('serial', ''), ('quantity', 'submission')])
def test_execution_recovery_rejects_invalid_leg_before_database(tracking, flow):
    from pg16_loss_disposition_recovery_gate import release
    def forbidden(*args, **kwargs):
        raise AssertionError('invalid recovery leg reached database')
    with pytest.raises(ValueError, match='explicit quantity/serial tracking and disposition/return flow required'):
        release({}, tracking=tracking, flow=flow, migrate=forbidden, provision=forbidden)


@pytest.mark.parametrize('tracking', ('quantity','serial'))
def test_execution_seals_leg_invokes_its_gate_and_disposes_all_engines(monkeypatch,tracking):
    from types import SimpleNamespace
    import pg16_loss_execution_seal_gate as execution_seals
    import test_postgresql16_stock_loss_release_gate as entry
    monkeypatch.setenv('RSC_PG16_LOSS_TRACKING',tracking)
    monkeypatch.setenv('RSC_PG16_LOSS_FLOW','execution_seals')
    monkeypatch.setattr(entry.gate,'_gate_enabled',lambda:True)
    for name in ('_assert_fresh_disposable_postgresql16','_bootstrap_roles','_provision_edge_receiver_role'):
        monkeypatch.setattr(entry.gate,name,lambda:None)
    monkeypatch.setattr(entry.gate,'_role_password',lambda role:'synthetic-fixture-only')
    monkeypatch.setattr(entry.gate,'_sqlalchemy_url',lambda **kwargs:kwargs['role'])
    disposed=[];observed=[]
    monkeypatch.setattr(entry,'create_engine',lambda role,**kwargs:SimpleNamespace(dispose=lambda:disposed.append(role)))
    def run(engines,**kwargs):
        assert set(kwargs)=={'tracking','migrate','provision'}
        assert callable(kwargs['migrate']) and callable(kwargs['provision'])
        observed.append((tuple(engines),kwargs['tracking']))
        return {'passed':True}
    monkeypatch.setattr(execution_seals,'release',run)
    entry.test_postgresql16_stock_loss_release_gate()
    expected=('star_oam_migrator','star_oam_api',entry.gate.EDGE_RECEIVER_ROLE)
    assert observed==[(expected,tracking)] and tuple(disposed)==expected
