"""Keep a remote backup stdin lease alive; stdout remains the raw export."""
import os,select,subprocess,sys,time


def send(command):
    if not command:return 64
    process=subprocess.Popen(command,stdin=subprocess.PIPE)
    os.set_blocking(process.stdin.fileno(),False)
    try:
        while process.poll() is None:
            _,writable,_=select.select([],[process.stdin],[],.25)
            if writable:
                try:os.write(process.stdin.fileno(),b'.')
                except BrokenPipeError:break
            time.sleep(.25)
        # Closed stdin revokes the remote lease even on a transport failure.
        process.stdin.close()
        try:return process.wait(timeout=4)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:process.wait(timeout=2)
            except subprocess.TimeoutExpired:process.kill();process.wait(timeout=2)
            return 125
    finally:
        if not process.stdin.closed:process.stdin.close()
        if process.poll() is None:
            process.terminate()
            try:process.wait(timeout=2)
            except subprocess.TimeoutExpired:process.kill();process.wait(timeout=2)


if __name__=='__main__':
    command=sys.argv[1:]
    if command[:1]==['--']:command=command[1:]
    try:code=send(command)
    except Exception:code=125
    raise SystemExit(code if code>=0 else 128-code)
