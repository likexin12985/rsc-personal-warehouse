"""The named release check cannot pass when either independent gate is absent."""
from itertools import product
from pathlib import Path
import re
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[3]
WORKFLOW=ROOT/'.github/workflows/postgresql16-release-gate.yml'


def _jobs():
    text=WORKFLOW.read_text()
    sections=re.split(r'^  ([a-zA-Z0-9_-]+):\s*$',text.split('jobs:\n',1)[1],flags=re.MULTILINE)
    return dict(zip(sections[1::2],sections[2::2]))


def test_runtime_and_static_jobs_are_independent_and_original_named_check_requires_both():
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
    assert set(jobs)=={'pg16_runtime','static_safety','postgresql16-release-gate'}
    runtime,static,aggregate=(jobs[key] for key in ('pg16_runtime','static_safety','postgresql16-release-gate'))
    assert not re.search(r'^    (needs|if|continue-on-error):',runtime+'\n'+static,re.MULTILINE)
    assert 'python -m pytest -q tests/test_postgresql16_release_gate.py' in runtime
    assert 'RSC_PG16_GATE_ACKNOWLEDGE_DISPOSABLE: I_UNDERSTAND_THIS_DATABASE_IS_EPHEMERAL' in runtime
    assert 'postgres:16-alpine@sha256:' in runtime
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
    assert '    needs: [pg16_runtime, static_safety]\n' in aggregate
    assert 'RUNTIME_RESULT: ${{ needs.pg16_runtime.result }}' in aggregate
    assert 'STATIC_RESULT: ${{ needs.static_safety.result }}' in aggregate
    assert 'continue-on-error:' not in aggregate


def test_static_shards_discover_every_test_module_exactly_once():
    cloud=ROOT/'cloud_oam'
    expected={str(path.relative_to(cloud)) for scope in ('backend/tests','edge_sync')
              for path in (cloud/scope).rglob('test_*.py') if path.is_file()}
    runtime='backend/tests/test_postgresql16_release_gate.py'
    assert runtime in expected
    observed=[]
    for index in range(3):
        result=subprocess.run([sys.executable,'scripts/run_static_shard.py','--index',str(index),
                               '--count','3','--list'],cwd=cloud,capture_output=True,text=True,check=True)
        selected=result.stdout.splitlines()
        assert selected and runtime not in selected
        observed.extend(selected)
    assert len(observed)==len(set(observed))
    assert set(observed)==expected-{runtime}


def test_pg16_and_static_jobs_install_the_image_runtime_hash_lock():
    jobs=_jobs()
    for name in ('pg16_runtime','static_safety'):
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
    for runtime,static in product(statuses,repeat=2):
        result=subprocess.run(['bash','-e','-c',script],env={'RUNTIME_RESULT':runtime,'STATIC_RESULT':static},capture_output=True,check=False)
        assert (result.returncode==0)==(runtime==static=='success'),(runtime,static)
