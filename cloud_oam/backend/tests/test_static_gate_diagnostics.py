"""Real subprocess evidence must preserve failures and survive forced interruption."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

HERE=Path(__file__).resolve().parents[2]/'scripts'


def run_command(tmp_path, content):
    test=tmp_path/'test_sample.py';test.write_text(content)
    output=tmp_path/'progress.jsonl'
    env={**os.environ,'PYTHONPATH':str(HERE),'PYTEST_DISABLE_PLUGIN_AUTOLOAD':'1'}
    env.pop('PYTEST_ADDOPTS',None)
    command=[sys.executable,'-m','pytest','-q','-p','static_gate_diagnostics',
        '--static-diagnostics',str(output),str(test)]
    return command,env,output


def test_records_exact_failed_test_without_leaking_exception_payload(tmp_path):
    command,env,output=run_command(tmp_path,
        "def test_ok(): assert True\ndef test_bad(): raise ValueError('SYNTHETIC_PRIVATE_PAYLOAD')\n")
    result=subprocess.run(command,env=env,cwd=tmp_path,capture_output=True,text=True,timeout=30)
    assert result.returncode==1
    assert 'STATIC_GATE_PROGRESS ' in result.stdout and '::test_bad' in result.stdout
    rows=[json.loads(line) for line in output.read_text().splitlines()]
    assert rows[0]['event']=='session_start'
    assert rows[-1]['event']=='session_finish' and rows[-1]['exit_code']==1
    assert any(r['event']=='test_report' and r['nodeid'].endswith('::test_bad')
        and r['outcome']=='failed' for r in rows)
    assert 'SYNTHETIC_PRIVATE_PAYLOAD' not in output.read_text()
    assert all(r['resources']['peak_rss_bytes']>0 for r in rows)
    # Reusing a log refuses the run rather than silently mixing evidence.
    again=subprocess.run(command,env=env,cwd=tmp_path,capture_output=True,text=True,timeout=30)
    assert again.returncode!=0 and len(output.read_text().splitlines())==len(rows)


def test_last_started_test_persists_when_owned_process_is_interrupted(tmp_path):
    command,env,output=run_command(tmp_path,
        'import time\ndef test_interrupted(): time.sleep(60)\n')
    process=subprocess.Popen(command,env=env,cwd=tmp_path,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        deadline=time.monotonic()+15
        while time.monotonic()<deadline:
            if output.exists() and any(json.loads(line)['event']=='test_start'
                    for line in output.read_text().splitlines()):break
            assert process.poll() is None
            time.sleep(.05)
        else:raise AssertionError('diagnostic start not flushed')
        process.terminate();process.wait(timeout=10)
        assert process.returncode!=0
        rows=[json.loads(line) for line in output.read_text().splitlines()]
        starts=[r for r in rows if r['event']=='test_start']
        assert len(starts)==1 and starts[0]['nodeid'].endswith('::test_interrupted')
        assert not any(r['event']=='test_report' and r['phase'] in ('call','teardown') for r in rows)
        assert not any(r['event']=='session_finish' for r in rows)
    finally:
        if process.poll() is None:
            process.kill();process.wait(timeout=10)


def test_shard_runner_wires_diagnostics_without_changing_failure_or_selection(tmp_path, monkeypatch):
    import importlib.util
    spec=importlib.util.spec_from_file_location('diagnostics_runner_test', HERE/'run_static_shard.py')
    runner=importlib.util.module_from_spec(spec);spec.loader.exec_module(runner)
    (tmp_path/'scripts').mkdir()
    (tmp_path/'scripts/static_gate_diagnostics.py').write_text((HERE/'static_gate_diagnostics.py').read_text())
    first=tmp_path/'test_first.py';first.write_text('def test_ok(): assert True\n')
    second=tmp_path/'test_second.py';second.write_text('def test_failed(): assert False\n')
    monkeypatch.setattr(runner,'CLOUD',tmp_path)
    monkeypatch.setattr(runner,'shard_files',lambda index,count:(first,second))
    monkeypatch.setattr(runner.shutil,'which',lambda name:'/synthetic/node')
    monkeypatch.setenv('PYTEST_DISABLE_PLUGIN_AUTOLOAD','1')
    monkeypatch.delenv('PYTEST_ADDOPTS',raising=False)
    assert runner.main(['--index','0','--count','3'])==1
    assert runner.main(['--index','0','--count','3'])==1
    reports=list((tmp_path/'artifacts/static-safety').glob('shard-0-*/progress.jsonl'))
    assert len(reports)==2
    for report in reports:
        rows=[json.loads(line) for line in report.read_text().splitlines()]
        assert rows[-1]['exit_code']==1 and rows[-1]['failed']==1
        assert {r['nodeid'] for r in rows if r['event']=='test_start'}=={'test_first.py::test_ok','test_second.py::test_failed'}
