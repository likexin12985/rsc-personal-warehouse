"""The named release check cannot pass when any independent gate is absent."""
from itertools import product
from pathlib import Path
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
    assert set(jobs)=={'pg16_runtime','pg16_loss','static_safety','postgresql16-release-gate'}
    runtime,static,aggregate=(jobs[key] for key in ('pg16_runtime','static_safety','postgresql16-release-gate'))
    loss=jobs['pg16_loss']
    assert not re.search(r'^    (needs|if|continue-on-error):',runtime+'\n'+static+'\n'+loss,re.MULTILINE)
    assert 'python -m pytest -q tests/test_postgresql16_release_gate.py -s' in runtime
    assert '    timeout-minutes: 360\n' in runtime
    assert 'RSC_PG16_GATE_ACKNOWLEDGE_DISPOSABLE: I_UNDERSTAND_THIS_DATABASE_IS_EPHEMERAL' in runtime
    assert 'postgres:16-alpine@sha256:' in runtime
    assert '    strategy:\n      fail-fast: false\n      matrix:\n        tracking: [quantity, serial]\n' in loss
    assert 'RSC_PG16_LOSS_TRACKING: ${{ matrix.tracking }}' in loss
    assert '        flow: [submission, disposition, return_preview, return_submission, return_outbound, return_shipment]\n' in loss
    assert 'RSC_PG16_LOSS_FLOW: ${{ matrix.flow }}' in loss
    assert 'python -m pytest -q -s tests/test_postgresql16_stock_loss_release_gate.py' in loss
    assert '        working-directory: cloud_oam/backend\n' in loss
    # An independent service per matrix leg must retain the original fresh
    # database, role passwords, loopback host and acknowledgement boundary.
    assert runtime.split('    services:\n',1)[1].split('    steps:\n',1)[0] == (
        loss.split('    services:\n',1)[1].split('    steps:\n',1)[0].replace(
            '      RSC_PG16_LOSS_TRACKING: ${{ matrix.tracking }}\n','').replace(
            '      RSC_PG16_LOSS_FLOW: ${{ matrix.flow }}\n',''))
    assert 'RSC_PG16_GATE_' not in static and 'services:' not in static
    # The three matrix legs must run the same dynamically discovered file set
    # with disjoint assignments. The named aggregate requires every leg.
    assert '    strategy:\n      fail-fast: false\n      matrix:\n        shard: [0, 1, 2]\n' in static
    step=static.split('      - name: Run complete backend and edge static safety gates\n',1)[1]
    assert '        working-directory: cloud_oam\n' in step
    assert step.split('        run: |\n',1)[1].strip() == (
        'python scripts/run_static_shard.py --index ${{ matrix.shard }} --count 3')
    assert '-r cloud_oam/scripts/requirements-public-knowledge.txt' in static
    assert '    timeout-minutes: 360\n' in static
    assert '    if: ${{ always() }}\n' in aggregate
    assert '    needs: [pg16_runtime, pg16_loss, static_safety]\n' in aggregate
    assert 'RUNTIME_RESULT: ${{ needs.pg16_runtime.result }}' in aggregate
    assert 'LOSS_RESULT: ${{ needs.pg16_loss.result }}' in aggregate
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


def test_pg16_and_static_jobs_install_the_image_runtime_hash_lock():
    jobs=_jobs()
    for name in ('pg16_runtime','pg16_loss','static_safety'):
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
    for runtime,loss,static in product(statuses,repeat=3):
        result=subprocess.run(['bash','-e','-c',script],env={
            'RUNTIME_RESULT':runtime,'LOSS_RESULT':loss,'STATIC_RESULT':static},capture_output=True,check=False)
        assert (result.returncode==0)==(runtime==loss==static=='success'),(runtime,loss,static)


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
        with pytest.raises(pytest.fail.Exception,match='explicit submission, disposition, return_preview, return_submission, return_outbound or return_shipment'):
            loss_gate.test_postgresql16_stock_loss_release_gate()


@pytest.mark.parametrize("flow", ("return_preview", "return_submission", "return_outbound", "return_shipment"))
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
