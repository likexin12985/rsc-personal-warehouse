"""The named release check cannot pass when either independent gate is absent."""
from itertools import product
from pathlib import Path
import re
import subprocess

ROOT=Path(__file__).resolve().parents[3]
WORKFLOW=ROOT/'.github/workflows/postgresql16-release-gate.yml'


def _jobs():
    text=WORKFLOW.read_text()
    sections=re.split(r'^  ([a-zA-Z0-9_-]+):\s*$',text.split('jobs:\n',1)[1],flags=re.MULTILINE)
    return dict(zip(sections[1::2],sections[2::2]))


def test_runtime_and_static_jobs_are_independent_and_original_named_check_requires_both():
    jobs=_jobs()
    assert set(jobs)=={'pg16_runtime','static_safety','postgresql16-release-gate'}
    runtime,static,aggregate=(jobs[key] for key in ('pg16_runtime','static_safety','postgresql16-release-gate'))
    assert not re.search(r'^    (needs|if|continue-on-error):',runtime+'\n'+static,re.MULTILINE)
    assert 'python -m pytest -q tests/test_postgresql16_release_gate.py' in runtime
    assert 'RSC_PG16_GATE_ACKNOWLEDGE_DISPOSABLE: I_UNDERSTAND_THIS_DATABASE_IS_EPHEMERAL' in runtime
    assert 'postgres:16-alpine@sha256:' in runtime
    assert 'RSC_PG16_GATE_' not in static and 'services:' not in static
    paths=re.findall(r'(?:backend/tests|edge_sync)/[a-zA-Z0-9_]+\.py',static)
    # The pre-split command had 128 files; this regression adds the 129th.
    assert len(paths)>=129 and len(paths)==len(set(paths))
    assert all((ROOT/'cloud_oam'/path).is_file() for path in paths)
    assert 'backend/tests/test_pg16_workflow_topology.py' in paths
    assert '    if: ${{ always() }}\n' in aggregate
    assert '    needs: [pg16_runtime, static_safety]\n' in aggregate
    assert 'RUNTIME_RESULT: ${{ needs.pg16_runtime.result }}' in aggregate
    assert 'STATIC_RESULT: ${{ needs.static_safety.result }}' in aggregate
    assert 'continue-on-error:' not in aggregate


def test_aggregate_shell_rejects_every_incomplete_or_failed_combination():
    source=_jobs()['postgresql16-release-gate'].split('        run: |\n',1)[1]
    script='\n'.join(line[10:] for line in source.splitlines() if line.strip())
    statuses=('success','failure','cancelled','skipped','timed_out','in_progress','')
    for runtime,static in product(statuses,repeat=2):
        result=subprocess.run(['bash','-e','-c',script],env={'RUNTIME_RESULT':runtime,'STATIC_RESULT':static},capture_output=True,check=False)
        assert (result.returncode==0)==(runtime==static=='success'),(runtime,static)
