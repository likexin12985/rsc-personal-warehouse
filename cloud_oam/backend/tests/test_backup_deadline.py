from backup_test_support import CLOUD, WORKER, OPS
from pathlib import Path
import json,os,signal,subprocess,sys,time
import pytest
from deadline_runner import supervise
HERE=Path(__file__).resolve().parent


@pytest.mark.parametrize('seconds',[0,-1,float('inf'),float('nan'),True,86401])
def test_invalid_deadline_never_starts_child(tmp_path,seconds):
    with pytest.raises(ValueError):supervise([sys.executable,'-c','raise RuntimeError("must not run")'],seconds=seconds,temp_parent=tmp_path)
    assert list(tmp_path.iterdir())==[]


@pytest.mark.parametrize('code',[0,7,71])
def test_command_result_and_binary_stdout_preserved(tmp_path,code):
    run=subprocess.run([sys.executable,str(WORKER/'deadline_runner.py'),'--seconds','5','--grace','.05','--temp-parent',str(tmp_path),'--',
        sys.executable,'-c',f'import os;os.write(1,bytes(range(256)));raise SystemExit({code})'],capture_output=True,timeout=10)
    assert run.returncode==code and run.stdout==bytes(range(256)) and list(tmp_path.iterdir())==[]
    assert json.loads(run.stderr)['reason']=='command_finished'


def test_backup_script_sets_only_owned_connection_check_and_discards_injected_options(tmp_path):
    binary=tmp_path/'bin';binary.mkdir();temp=tmp_path/'tmp';temp.mkdir();marker=tmp_path/'options.json'
    for name in ['psql','pg_dump']:
        f=binary/name
        f.write_text('#!'+sys.executable+'\nimport os,sys,json\nfrom pathlib import Path\n'
            'if sys.argv[1:]==["--version"]:print('+repr(name+' (PostgreSQL) 16.15')+')\n'
            'else:Path('+repr(str(marker))+').write_text(json.dumps({"options":os.environ.get("PGOPTIONS"),"user":os.environ.get("PGUSER")}))\n')
        f.chmod(0o700)
    run=subprocess.run(['/bin/sh',str(OPS/'backup_database.sh')],capture_output=True,timeout=10,
        env={'PATH':str(binary)+':/usr/bin:/bin','POSTGRES_DB':'synthetic_backup','RSC_BACKUP_MAXIMUM_BYTES':'1048576',
            'TMPDIR':str(temp),'PGOPTIONS':'-c search_path=synthetic_injected_schema','PGPASSWORD':'synthetic-not-a-real-password'})
    assert run.returncode==0 and run.stdout==b''
    assert json.loads(marker.read_text())=={'options':'-c client_connection_check_interval=1000','user':'star_oam_backup'}
    assert b'synthetic-not-a-real-password' not in run.stderr and list(temp.iterdir())==[]


def stopped(pid):
    run=subprocess.run(['ps','-p',str(pid),'-o','stat='],capture_output=True,text=True)
    return run.returncode!=0 or run.stdout.strip().startswith('Z')


@pytest.mark.parametrize('mode',['timeout','term','int','hup','parent-kill','background-child'])
def test_owned_descendants_stop_and_unrelated_process_survives(tmp_path,mode):
    marker=tmp_path/'pids.json';script=tmp_path/'child.py';temp=tmp_path/'temp';temp.mkdir()
    # Both child and grandchild ignore TERM, forcing group-wide escalation.
    script.write_text('import os,signal,subprocess,sys,time,json\nfrom pathlib import Path\n'
        'signal.signal(signal.SIGTERM,signal.SIG_IGN)\n'
        'grand=subprocess.Popen([sys.executable,"-c","import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(60)"])\n'
        'Path(os.environ["TMPDIR"],"partial").write_bytes(b"unfinished")\n'
        'Path('+repr(str(marker))+').write_text(json.dumps([os.getpid(),grand.pid]))\n'
        +('raise SystemExit(0)\n' if mode=='background-child' else 'time.sleep(60)\n'))
    outsider=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'],start_new_session=True)
    run=subprocess.Popen([sys.executable,str(WORKER/'deadline_runner.py'),'--seconds','1' if mode=='timeout' else '20','--grace','.1',
        '--temp-parent',str(temp),'--',sys.executable,str(script)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True)
    try:
        until=time.monotonic()+5
        while not marker.exists():
            if run.poll() is not None:raise AssertionError('supervisor exited before child marker')
            if time.monotonic()>until:raise AssertionError('child did not start')
            time.sleep(.01)
        if mode in ['term','int','hup','parent-kill']:
            os.kill(run.pid,{'term':signal.SIGTERM,'int':signal.SIGINT,'hup':signal.SIGHUP,'parent-kill':signal.SIGKILL}[mode])
        out,err=run.communicate(timeout=6)
        expected={'timeout':124,'term':143,'int':130,'hup':129,'parent-kill':-9,'background-child':0}[mode]
        assert run.returncode==expected,(mode,run.returncode,err)
        until=time.monotonic()+3
        while not all(stopped(pid) for pid in json.loads(marker.read_text())) or list(temp.iterdir()):
            if time.monotonic()>until:raise AssertionError('owned children or scratch remain')
            time.sleep(.02)
        assert outsider.poll() is None
    finally:
        if run.poll() is None:os.killpg(run.pid,signal.SIGKILL);run.wait(timeout=5)
        outsider.terminate();outsider.wait(timeout=5)
