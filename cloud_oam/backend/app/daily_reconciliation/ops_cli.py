"""One-shot daily reconciliation operations; no automatic retry or login."""
import argparse
import getpass
import json
import os
from pathlib import Path
import re
import select
import stat
import sys

MAX_COMMAND_BYTES = 65536


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, 'daily_ops_invalid_arguments\n')


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON key')
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError('non-finite JSON number')


def _document(path, operation):
    with Path(path).open('rb') as source:
        raw = source.read(MAX_COMMAND_BYTES + 1)
    if len(raw) > MAX_COMMAND_BYTES:
        raise ValueError('command document too large')
    value = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique,
                       parse_constant=_invalid_constant)
    expected = {'command', 'expected_authorization_version'}
    if operation in ('mapping-apply', 'mapping-status'):
        expected.add('review_sha256')
    if type(value) is not dict or set(value) != expected or \
            type(value['expected_authorization_version']) is not int or \
            value['expected_authorization_version'] < 1:
        raise ValueError('invalid command envelope')
    if 'review_sha256' in expected and not isinstance(value['review_sha256'], str):
        raise ValueError('invalid review digest')
    if 'review_sha256' in expected and not re.fullmatch(r'[a-f0-9]{64}', value['review_sha256']):
        raise ValueError('invalid review digest')
    if operation.startswith('mapping-'):
        from .mapping import MappingCommand as model
    else:
        from .cutoff_service import CaptureCommand as model
    command = model.model_validate(value['command'])
    return command.model_dump(mode='json'), value['expected_authorization_version'], value.get('review_sha256')


def _credential(fd):
    if fd is None:
        if not sys.stdin.isatty():
            raise ValueError('private token descriptor required')
        token = getpass.getpass('RSC 当前网页登录凭据（隐藏输入）：')
    else:
        if fd < 3:
            raise ValueError('private token descriptor required')
        with os.fdopen(os.dup(fd), 'rb', buffering=0) as source:
            if not select.select([source], [], [], 5)[0]:
                raise ValueError('private token descriptor timed out')
            token = os.read(source.fileno(), 8194).decode('ascii').removesuffix('\n')
    if not token or len(token) > 8192 or not re.fullmatch(r'[A-Za-z0-9_.-]+', token):
        raise ValueError('invalid credential')
    return token


def _secret_file(path):
    if not path:
        raise ValueError('database secret file required')
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_CLOEXEC', 0)
    fd = os.open(path, flags)
    try:
        meta = os.fstat(fd)
        if not stat.S_ISREG(meta.st_mode) or meta.st_mode & 0o007 or meta.st_size > 4096:
            raise ValueError('database secret file permissions invalid')
        raw = os.read(fd, 4097)
    finally:
        os.close(fd)
    if not raw or len(raw) > 4096:
        raise ValueError('database secret file invalid')
    return raw.decode('utf-8').strip()


def _configuration():
    target, duration = _target_configuration()
    conninfo = _secret_file(os.environ.get('OAM_DAILY_RECONCILIATION_OWNER_CONNINFO_FILE'))
    return conninfo, target, duration


def _target_configuration():
    target = os.environ.get('OAM_DAILY_RECONCILIATION_DATABASE_NAME', '')
    duration = os.environ.get('OAM_DAILY_RECONCILIATION_MAXIMUM_SECONDS', '10')
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_-]{0,62}', target) or \
            not re.fullmatch(r'[1-9][0-9]?', duration) or not 1 <= int(duration) <= 60:
        raise ValueError('daily operations configuration invalid')
    return target, int(duration)


def _cutoff_configuration():
    from psycopg.conninfo import conninfo_to_dict
    from .process_entry import DatabaseConfig
    target, duration = _target_configuration()
    options = []
    for name, role in (('OWNER', 'star_oam_migrator'),
                       ('SOURCE', 'rsc_control_capture'),
                       ('LEDGER', 'rsc_reconciliation_capture')):
        raw = _secret_file(os.environ.get('OAM_DAILY_RECONCILIATION_'+name+'_CONNINFO_FILE'))
        parsed = conninfo_to_dict(raw)
        if set(parsed) - {'host', 'port', 'dbname', 'user', 'password', 'sslmode', 'sslrootcert'} or \
                any(not parsed.get(key) for key in ('host', 'port', 'dbname', 'user', 'password')) or \
                parsed['user'] != role or parsed['dbname'] != target:
            raise ValueError('daily capture database configuration invalid')
        options.append((raw, parsed))
    if len({(p['host'], str(p['port']), p['dbname']) for _, p in options}) != 1:
        raise ValueError('daily capture database targets mismatch')
    return DatabaseConfig(*(raw for raw, _ in options)), target, duration


def main(argv=None):
    parser = SafeParser(description=__doc__)
    parser.add_argument('operation', choices=('mapping-preview', 'mapping-apply', 'mapping-status',
        'cutoff-preview', 'cutoff-capture', 'cutoff-recover'))
    parser.add_argument('--command-file', required=True)
    parser.add_argument('--access-token-fd', type=int)
    args = parser.parse_args(argv)
    writing = args.operation in ('mapping-apply', 'cutoff-capture')
    worker_started = False
    try:
        command, version, review_sha256 = _document(args.command_file, args.operation)
        token = _credential(args.access_token_fd)
        cutoff = args.operation.startswith('cutoff-')
        database, target, duration = _cutoff_configuration() if cutoff else _configuration()
        worker_started = True
        if cutoff:
            from . import process_entry
            result = process_entry.execute(database=database,command=command,
                access_token=token,expected_authorization_version=version,
                operation=args.operation.removeprefix('cutoff-'),maximum_seconds=duration)
            expected = 'inspected' if args.operation == 'cutoff-preview' else 'committed'
            if result.get('outcome') != expected or type(result.get('receipt')) is not dict:
                raise ValueError('daily capture receipt invalid')
            receipt = result['receipt']
            if receipt.get('stock_written') is not False or receipt.get('daily_reconciliation_approved') is not False:
                raise ValueError('daily capture receipt semantics invalid')
            if args.operation == 'cutoff-preview' and (receipt.get('inspection_only') is not True
                    or receipt.get('capture_performed') is not False):
                raise ValueError('daily capture preview semantics invalid')
            if args.operation == 'cutoff-capture' and receipt.get('recorded') is not True:
                raise ValueError('daily capture commit receipt missing')
        else:
            from . import mapping_entry
            result = mapping_entry.execute(conninfo=database, database_name=target,
                command=command, access_token=token,
                expected_authorization_version=version,
                operation=args.operation.removeprefix('mapping-'),
                review_sha256=review_sha256, maximum_seconds=duration)
            if result.get('operation') != args.operation.removeprefix('mapping-') or \
                    result.get('projection_published') is not False or result.get('start_ready') is not False:
                raise ValueError('daily mapping receipt semantics invalid')
            if args.operation == 'mapping-preview' and not re.fullmatch(r'[a-f0-9]{64}',result.get('review_sha256','')):
                raise ValueError('daily mapping review digest missing')
            if args.operation == 'mapping-apply' and result.get('recorded') is not True:
                raise ValueError('daily mapping decision receipt missing')
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False))
        if args.operation == 'mapping-status' and result['recorded'] is False:
            return 4
        if args.operation == 'cutoff-recover' and result['receipt']['recorded'] is False:
            return 4
        return 0
    except (Exception, KeyboardInterrupt):
        # Worker failures include lost COMMIT acknowledgements. Nothing in an
        # exception is safe to print: it may carry a DSN, token or raw request.
        code = 'daily_ops_outcome_unknown' if writing and worker_started else 'daily_ops_rejected'
        next_action = ('cutoff-recover-with-original-command' if args.operation == 'cutoff-capture'
            else 'mapping-status-with-original-command') if writing and worker_started else 'check_original_command_and_access'
        print(json.dumps(dict(code=code, next_action=next_action)), file=sys.stderr)
        return 3 if writing and worker_started else 2


if __name__ == '__main__':
    raise SystemExit(main())
