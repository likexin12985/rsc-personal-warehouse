"""POSIX command deadline with a retained session leader and parent-death pipe.

The private keeper keeps its PID reserved until the owned process group has been
killed. We never reap a process and then signal a potentially reused group id.
Only controlled backup commands that do not detach into new sessions are allowed.
"""
import argparse,json,os,select,shutil,signal,subprocess,sys,tempfile,time
from pathlib import Path


def cleanup(directory):
    # Created exclusively by supervise(), never an arbitrary preexisting path.
    if directory.exists():shutil.rmtree(directory)


def keeper(control,events,directory,grace,command):
    assert os.getpgrp()==os.getpid() and os.getsid(0)==os.getpid()
    # Caught handlers reset on exec, unlike SIG_IGN; descendants receive TERM.
    for sig in [signal.SIGTERM,signal.SIGINT,signal.SIGHUP]:signal.signal(sig,lambda *_:None)
    os.set_blocking(control,False)
    child=None;reported=False;parent_lost=False
    try:
        env=dict(os.environ,TMPDIR=str(directory))
        child=subprocess.Popen(command,env=env,stdin=subprocess.DEVNULL,close_fds=True)
        os.write(events,b'R\n')
        while True:
            ready,_,_=select.select([control],[],[],.05)
            if ready:
                data=os.read(control,32)
                if not data:parent_lost=True;break
                if data==b'STOP':break
                raise RuntimeError('invalid_keeper_control')
            code=child.poll()
            if code is not None and not reported:
                os.write(events,('D'+str(code)+'\n').encode());reported=True
    except BaseException:
        try:os.write(events,b'D125\n')
        except OSError:pass
    finally:
        # The keeper remains alive (handler above) while its entire owned group
        # gets TERM, including descendants left behind by an exited shell.
        os.killpg(os.getpid(),signal.SIGTERM)
        until=time.monotonic()+grace
        while time.monotonic()<until:
            if child is not None:child.poll()
            time.sleep(.02)
        # Removing private scratch before KILL is safe: live open files become
        # unlinked, and controlled children cannot recreate a missing TMPDIR.
        try:
            if not parent_lost:
                # Parent keeps its control writer open until it sends the final
                # KILL. Stay alive so the process group is never only zombies.
                while True:
                    ready,_,_=select.select([control],[],[],.05)
                    if ready and not os.read(control,32):break
            cleanup(directory)
        finally:os.killpg(os.getpid(),signal.SIGKILL)


def supervise(command,*,seconds,grace=2.0,temp_parent=None,lease_fd=None,lease_seconds=2.0):
    if not command or not isinstance(seconds,(int,float)) or isinstance(seconds,bool) or not .1<=seconds<=86400 or not .05<=grace<=30:
        raise ValueError('invalid_deadline_configuration')
    if not isinstance(lease_seconds,(int,float)) or isinstance(lease_seconds,bool) or not .1<=lease_seconds<=30:raise ValueError('invalid_lease_configuration')
    if os.name!='posix':raise ValueError('posix_deadline_required')
    directory=Path(tempfile.mkdtemp(prefix='.rsc-deadline-',dir=temp_parent));os.chmod(directory,0o700)
    control_read,control_write=os.pipe();events_read,events_write=os.pipe()
    process=None;requested=[];old_handlers={};exit_code=125;reason='supervisor_failure'
    for sig in [signal.SIGTERM,signal.SIGINT,signal.SIGHUP]:
        old_handlers[sig]=signal.signal(sig,lambda number,_:requested.append(number))
    started=time.monotonic();buffer=b'';last_lease=started
    try:
        if lease_fd is not None:
            ready,_,_=select.select([lease_fd],[],[],min(seconds,lease_seconds))
            initial=os.read(lease_fd,64) if ready else b''
            if not initial or set(initial)!={46}:raise ValueError('initial_backup_lease_required')
            last_lease=time.monotonic()
        process=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'_keeper',str(control_read),str(events_write),
            str(directory),str(grace),'--',*command],start_new_session=True,pass_fds=(control_read,events_write),stdin=subprocess.DEVNULL,
            env=dict(os.environ,RSC_BACKUP_DEADLINE_MONOTONIC=str(started+seconds)))
        os.close(control_read);control_read=None;os.close(events_write);events_write=None
        os.set_blocking(events_read,False)
        while True:
            if requested:exit_code=128+requested[0];reason='cancelled';break
            remaining=seconds-(time.monotonic()-started)
            if remaining<=0:exit_code=124;reason='deadline_exceeded';break
            if lease_fd is not None and time.monotonic()-last_lease>=lease_seconds:
                exit_code=124;reason='lease_expired';break
            ready,_,_=select.select([events_read]+([lease_fd] if lease_fd is not None else []),[],[],min(.05,remaining))
            if lease_fd is not None and lease_fd in ready:
                lease=os.read(lease_fd,64)
                if not lease or set(lease)!={46}:exit_code=125;reason='lease_lost';break
                last_lease=time.monotonic()
            if events_read not in ready:continue
            data=os.read(events_read,64)
            if not data:break
            buffer+=data
            if len(buffer)>128:break
            if b'D' in buffer:
                line=buffer.split(b'D',1)[1]
                if b'\n' not in line:continue
                code=int(line.split(b'\n',1)[0])
                exit_code=code if code>=0 else 128-code;reason='command_finished';break
    finally:
        if process is not None:
            try:os.write(control_write,b'STOP')
            except OSError:pass
            # Do not poll/wait/reap the session leader before the last signal.
            # Its live or zombie PID reserves this group id throughout cleanup.
            limit=time.monotonic()+grace+.15
            while time.monotonic()<limit:time.sleep(.02)
            try:os.killpg(process.pid,signal.SIGKILL)
            except ProcessLookupError:pass
            process.wait(timeout=5)
        cleanup(directory)
        for fd in [control_read,control_write,events_read,events_write]:
            if fd is not None:os.close(fd)
        for sig,handler in old_handlers.items():signal.signal(sig,handler)
    return dict(exit_code=exit_code,reason=reason,elapsed_seconds=round(time.monotonic()-started,3),
        scratch_cleaned=not directory.exists(),owned_session=process.pid if process is not None else None)


def main(argv=None):
    argv=sys.argv[1:] if argv is None else argv
    if argv and argv[0]=='_keeper':
        control,events,directory,grace=argv[1:5];assert argv[5]=='--'
        keeper(int(control),int(events),Path(directory),float(grace),argv[6:]);return 125
    parser=argparse.ArgumentParser(description='Run one controlled backup command with a POSIX deadline.')
    parser.add_argument('--seconds',required=True,type=float);parser.add_argument('--grace',type=float,default=2.0)
    parser.add_argument('--require-lease',action='store_true');parser.add_argument('--lease-seconds',type=float,default=2.0)
    parser.add_argument('--temp-parent',type=Path);parser.add_argument('command',nargs=argparse.REMAINDER)
    args=parser.parse_args(argv);command=args.command
    if command and command[0]=='--':command=command[1:]
    try:result=supervise(command,seconds=args.seconds,grace=args.grace,temp_parent=args.temp_parent,lease_fd=0 if args.require_lease else None,lease_seconds=args.lease_seconds)
    except Exception:
        print(json.dumps(dict(status='failed',code='deadline_supervisor_failed')),file=sys.stderr);return 125
    # Never print command arguments, provider output or inherited environment.
    print(json.dumps(result,sort_keys=True),file=sys.stderr);return result['exit_code']


if __name__=='__main__':raise SystemExit(main())
