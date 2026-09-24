"""Loss of a host lease must stop detached remote work, including ignored TERM."""
from backup_test_support import CLOUD, WORKER, OPS
from pathlib import Path
import json,os,signal,subprocess,sys,time
import pytest
from test_backup_deadline import stopped
HERE=Path(__file__).resolve().parent


def command(temp,marker,*,lease='.4',seconds='5'):
    child='import os,signal,time;from pathlib import Path;signal.signal(signal.SIGTERM,signal.SIG_IGN);Path('+repr(str(marker))+').write_text(str(os.getpid()));Path(os.environ["TMPDIR"],"partial").write_bytes(b"partial");time.sleep(60)'
    return [sys.executable,str(WORKER/'deadline_runner.py'),'--seconds',seconds,'--grace','.1','--require-lease','--lease-seconds',lease,'--temp-parent',str(temp),'--',sys.executable,'-c',child]


@pytest.mark.parametrize('heartbeat',[b'',b'x',b'.x'])
def test_initial_lease_required_before_any_child(tmp_path,heartbeat):
    temp=tmp_path/'tmp';temp.mkdir();marker=tmp_path/'started'
    run=subprocess.run(command(temp,marker),input=heartbeat,capture_output=True,timeout=3)
    assert run.returncode==125 and not marker.exists() and not list(temp.iterdir())


def test_missing_initial_lease_has_bounded_wait(tmp_path):
    temp=tmp_path/'tmp';temp.mkdir();marker=tmp_path/'started'
    run=subprocess.Popen(command(temp,marker),stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    try:
        run.wait(timeout=2)
        assert run.returncode==125 and not marker.exists() and not list(temp.iterdir())
    finally:run.stdin.close()


@pytest.mark.parametrize('mode',['eof','stale','invalid','total-timeout'])
def test_live_command_stops_on_lease_loss_or_shared_deadline(tmp_path,mode):
    temp=tmp_path/'tmp';temp.mkdir();marker=tmp_path/'started'
    run=subprocess.Popen(command(temp,marker,seconds='.8' if mode=='total-timeout' else '5'),stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    run.stdin.write(b'.');run.stdin.flush()
    try:
        until=time.monotonic()+2
        while not marker.exists() and run.poll() is None and time.monotonic()<until:time.sleep(.01)
        assert marker.exists()
        if mode=='eof':run.stdin.close();run.stdin=None
        elif mode=='invalid':run.stdin.write(b'x');run.stdin.flush()
        elif mode=='total-timeout':
            while run.poll() is None:
                try:run.stdin.write(b'.');run.stdin.flush()
                except BrokenPipeError:break
                time.sleep(.05)
        run.wait(timeout=3)
        assert run.returncode==(125 if mode in ('eof','invalid') else 124)
        assert stopped(int(marker.read_text())) and not list(temp.iterdir())
        assert not run.stdout.read()
        result=json.loads(run.stderr.read())
        assert result['reason']=={'eof':'lease_lost','invalid':'lease_lost','stale':'lease_expired','total-timeout':'deadline_exceeded'}[mode]
    finally:
        if run.stdin is not None:run.stdin.close()
        if run.poll() is None:run.kill();run.wait(timeout=3)


def test_sender_kill_revokes_detached_remote_lease(tmp_path):
    temp=tmp_path/'tmp';temp.mkdir();marker=tmp_path/'started';bridge=tmp_path/'bridge.py'
    # This remote is in a distinct session; killing the sender cannot kill it directly.
    bridge.write_text('import subprocess,sys\np=subprocess.Popen(sys.argv[1:],start_new_session=True,stdin=sys.stdin)\nraise SystemExit(p.wait())\n')
    sender=subprocess.Popen([sys.executable,str(WORKER/'lease_sender.py'),'--',sys.executable,str(bridge),*command(temp,marker)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True)
    try:
        until=time.monotonic()+3
        while not marker.exists() and sender.poll() is None and time.monotonic()<until:time.sleep(.01)
        assert marker.exists()
        os.kill(sender.pid,signal.SIGKILL);sender.communicate(timeout=4)
        assert sender.returncode==-9 and stopped(int(marker.read_text())) and not list(temp.iterdir())
    finally:
        if sender.poll() is None:os.killpg(sender.pid,signal.SIGKILL);sender.wait(timeout=3)


def test_binary_export_success_through_live_sender(tmp_path):
    temp=tmp_path/'tmp';temp.mkdir()
    remote=[sys.executable,str(WORKER/'deadline_runner.py'),'--seconds','3','--grace','.1','--require-lease','--temp-parent',str(temp),'--',sys.executable,'-c','import os,time;time.sleep(.6);os.write(1,bytes(range(256)))']
    run=subprocess.run([sys.executable,str(WORKER/'lease_sender.py'),'--',*remote],capture_output=True,timeout=5)
    assert run.returncode==0 and run.stdout==bytes(range(256)) and not list(temp.iterdir())
