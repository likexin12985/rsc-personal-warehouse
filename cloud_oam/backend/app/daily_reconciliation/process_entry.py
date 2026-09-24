"""Internal POSIX job boundary, not an HTTP or client-configurable executor.

Credentials stay in a private spawn pipe, never command-line arguments/logs.
The worker owns new non-pooled connections. Parent deadline and child lease
watchdog include setup, connection establishment and Python work. A killed or
unobserved worker always requires exact recovery; there is no retry here.
"""
from dataclasses import dataclass
import multiprocessing as mp
import os
import select
import struct
import threading
import time


class ProcessEntryError(RuntimeError):
    pass


class ProcessOutcomeUnknown(ProcessEntryError):
    pass


@dataclass(frozen=True)
class DatabaseConfig:
    # Trusted service configuration only; never populated from request JSON.
    owner_conninfo: str
    source_conninfo: str
    ledger_conninfo: str


def _watch_lease(lease, expires):
    # A separate thread also bounds blocking native connection attempts.
    # No worker subprocesses are created by the fixed cutoff operation.
    try:
        while True:
            remaining = expires - time.monotonic()
            if remaining <= 0 or not lease.poll(remaining):
                os._exit(124)
            # Parent keeps only the writing end open. No heartbeat writes
            # can block on a stopped worker or a full pipe. EOF means lost owner.
            lease.recv_bytes(16)
            os._exit(125)
    except (EOFError, OSError):
        os._exit(125)


def _bootstrap(result_pipe, lease_pipe, worker, payload, expires):
    threading.Thread(target=_watch_lease, args=(lease_pipe, expires), daemon=True).start()
    try:
        receipt = worker(payload, expires)
        # Bound output and never serialize exception text (DSNs/tokens may be in it).
        import json
        data = json.dumps(dict(outcome='observed', result=receipt), allow_nan=False).encode()
        if len(data) > 64 * 1024:
            raise ProcessEntryError('receipt_too_large')
        result_pipe.send_bytes(data)
    except BaseException:
        # Conservative across connection, SQL, cleanup and COMMIT failures.
        # The child may have committed, so this never asserts rollback.
        try:
            result_pipe.send_bytes(b'{"outcome":"unknown"}')
        except (OSError, EOFError):
            pass
    finally:
        result_pipe.close()
        lease_pipe.close()


def run_owned_job(worker, payload, *, maximum_seconds=10):
    """Server-only executor. `worker` is code, never a request-selected name.

    Runtime deadline excludes the <=1 second SIGKILL/reap cleanup allowance.
    OS scheduling/start/kill syscalls are not a hard-real-time guarantee.
    """
    if os.name != 'posix':
        raise ProcessEntryError('posix_required')
    if type(maximum_seconds) is not int or not 1 <= maximum_seconds <= 60:
        raise ProcessEntryError('deadline_seconds_invalid')
    expires = time.monotonic() + maximum_seconds
    context = mp.get_context('spawn')
    result_rx, result_tx = context.Pipe(duplex=False)
    lease_rx, lease_tx = context.Pipe(duplex=False)
    child = context.Process(target=_bootstrap, args=(result_tx, lease_rx, worker, payload, expires), daemon=True)
    started = False
    observed = None
    failure = None
    try:
        child.start()
        started = True
        result_tx.close()
        lease_rx.close()
        fd = result_rx.fileno()
        os.set_blocking(fd, False)
        buffer = bytearray()
        while True:
            remaining = expires - time.monotonic()
            if remaining <= 0:
                break
            if select.select([fd], [], [], min(remaining, .05))[0]:
                chunk = os.read(fd, 65540 - len(buffer))
                if not chunk:
                    break
                buffer.extend(chunk)
                if len(buffer) >= 4:
                    size = struct.unpack('!i', buffer[:4])[0]
                    if not 0 < size <= 65536 or len(buffer) > size + 4:
                        break
                    if len(buffer) == size + 4:
                        import json
                        observed = json.loads(buffer[4:])
                        break
    except BaseException as error:
        failure = error
    finally:
        lease_tx.close()
        result_rx.close()
        result_tx.close()
        lease_rx.close()
        if started or child.pid is not None:
            if child.is_alive():
                child.kill()
            child.join(timeout=1)
            if child.is_alive():
                # Do not falsely claim termination; retain the process identity.
                raise ProcessEntryError('worker_cleanup_unconfirmed_pid_' + str(child.pid))
            child.close()
    if isinstance(failure, (KeyboardInterrupt, SystemExit)):
        raise failure
    if observed is None or observed.get('outcome') != 'observed':
        raise ProcessOutcomeUnknown('daily_cutoff_process_outcome_unknown_use_exact_recovery') from None
    return observed['result']


def _cutoff_worker(payload, expires):
    # Heavy imports and all DB setup occur inside the supervised boundary.
    from contextlib import ExitStack
    import psycopg
    from psycopg.conninfo import conninfo_to_dict
    from sqlalchemy import create_engine
    from sqlalchemy.pool import NullPool
    from . import cutoff_service
    from . import deadline_entry
    if set(payload) != {'database', 'command', 'access_token', 'expected_authorization_version', 'operation'}:
        raise ProcessEntryError('invalid_internal_job')
    config = payload['database']
    if not isinstance(config, DatabaseConfig) or payload['operation'] not in ('capture', 'recover', 'preview'):
        raise ProcessEntryError('invalid_internal_job')
    command = cutoff_service.CaptureCommand.model_validate(payload['command'])
    def connect(conninfo, *, autocommit=False):
        remaining = int(expires - time.monotonic())
        if remaining < 1:
            raise ProcessEntryError('connection_budget_exhausted')
        # libpq's connection timeout is secondary. The process boundary also
        # covers DNS, multiple addresses and a stalled native connect call.
        options = conninfo_to_dict(conninfo)
        options['connect_timeout'] = max(2, remaining)
        return psycopg.connect(**options, autocommit=autocommit)
    with ExitStack() as owned:
        owner_engine = create_engine('postgresql+psycopg://', creator=lambda: connect(config.owner_conninfo), poolclass=NullPool)
        owned.callback(owner_engine.dispose)
        source_engine = create_engine('postgresql+psycopg://', creator=lambda: connect(config.source_conninfo), poolclass=NullPool)
        owned.callback(source_engine.dispose)
        owner = owned.enter_context(owner_engine.connect())
        source = owned.enter_context(source_engine.connect())
        ledger = owned.enter_context(connect(config.ledger_conninfo, autocommit=True))
        remaining = int(expires - time.monotonic())
        if remaining < 1:
            raise ProcessEntryError('transaction_budget_exhausted')
        return deadline_entry.execute_ready(owner=owner, source=source, ledger=ledger,
            command=command, access_token=payload['access_token'],
            expected_authorization_version=payload['expected_authorization_version'],
            operation=payload['operation'], maximum_seconds=min(remaining, 60))


def execute(*, database, command, access_token, expected_authorization_version, operation='capture', maximum_seconds=10):
    """Fixed internal cutoff operation; no source facts/DSNs accepted from clients."""
    payload = dict(database=database, command=command, access_token=access_token,
        expected_authorization_version=expected_authorization_version, operation=operation)
    return run_owned_job(_cutoff_worker, payload, maximum_seconds=maximum_seconds)
