"""Prepare one bounded writer, then privately issue one Agent bootstrap credential.

No Agent start, automatic cleanup, secret replay, or credential file on macOS.
The public journal survives interruptions and binds the exact container ID.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys

sys.dont_write_bytecode = True
import runtime_init as custody
from runtime_configure import read_root
from runtime_bundle import IMAGE, RUN, Rejected, require
from runtime_bootstrap_writer import IDENTITIES


def source(name):
    return Path(__file__).with_name(name).read_text()


def record(directory, name, value):
    custody.canonical_directory(directory)
    require(directory.stat().st_mode & 0o777 == 0o700, 'bootstrap_journal_directory_mode')
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        custody.new_file(descriptor, name, json.dumps(value, sort_keys=True).encode())
    finally:
        os.close(descriptor)


def load_record(directory, name):
    custody.canonical_directory(directory)
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        return json.loads(custody.existing_file(descriptor, name))
    finally:
        os.close(descriptor)


def binding(args):
    return {'schema': 'rsc.openbao.bootstrap-writer.v1', 'attemptId': args.attempt_id,
            'purpose': args.purpose, 'instanceId': args.instance_id, 'baoContainerId': args.container_id,
            'writerName': 'rsc-bootstrap-' + args.purpose + '-' + args.attempt_id,
            'writerSourceSha256': hashlib.sha256(source('runtime_bootstrap_writer.py').encode()).hexdigest(),
            'remoteSourceSha256': hashlib.sha256(source('runtime_bootstrap_remote.py').encode()).hexdigest(),
            'automaticReplayAllowed': False}


def create_arguments(args, client):
    uid, gid = IDENTITIES[args.purpose]
    return client.docker + ['create', '--pull=never', '-i', '--name=' + binding(args)['writerName'],
        '--label=rsc.bootstrap.attempt=' + args.attempt_id, '--label=rsc.bootstrap.purpose=' + args.purpose,
        '--user=' + str(uid) + ':' + str(gid), '--network=none', '--read-only', '--cap-drop=ALL',
        '--security-opt=no-new-privileges=true', '--log-driver=none', '--restart=no', '--memory=32m',
        '--memory-swap=32m', '--pids-limit=32', '--cpus=0.25', '--ulimit=core=0:0', '--ipc=private',
        '--mount=type=bind,src=' + RUN + '/bootstrap-' + args.purpose + ',dst=/run/bootstrap,bind-propagation=rprivate',
        '--entrypoint=python3', IMAGE, '-B', '-c', source('runtime_bootstrap_writer.py')]


def writer_inspect(args, client, container_id, expected_status):
    rows = json.loads(client.command(client.docker + ['inspect', '--type=container', container_id]))
    require(type(rows) is list and len(rows) == 1, 'bootstrap_writer_inspect_shape')
    row = rows[0]; host, config, state = row['HostConfig'], row['Config'], row['State']
    uid, gid = IDENTITIES[args.purpose]
    require(row['Id'] == container_id and row['Image'] == IMAGE
            and row['Name'] == '/' + binding(args)['writerName'] and config.get('User') == str(uid) + ':' + str(gid)
            and config.get('Entrypoint') == ['python3']
            and config.get('Cmd') == ['-B', '-c', source('runtime_bootstrap_writer.py')]
            and config.get('OpenStdin') is True and config.get('Tty') is False, 'bootstrap_writer_identity')
    labels = config.get('Labels', {})
    require(labels.get('rsc.bootstrap.attempt') == args.attempt_id
            and labels.get('rsc.bootstrap.purpose') == args.purpose, 'bootstrap_writer_labels')
    require(host.get('NetworkMode') == 'none' and host.get('ReadonlyRootfs') is True
            and host.get('CapDrop') == ['ALL'] and not host.get('CapAdd') and host.get('Privileged') is False
            and host.get('SecurityOpt') in (['no-new-privileges=true'], ['no-new-privileges:true'])
            and host.get('LogConfig', {}).get('Type') == 'none' and host.get('Memory') == host.get('MemorySwap') == 33554432
            and host.get('PidsLimit') == 32 and host.get('NanoCpus') == 250000000
            and not host.get('GroupAdd') and host.get('IpcMode') == 'private'
            and host.get('PidMode', '') in ('', 'private') and not host.get('Devices')
            and not host.get('PortBindings') and not host.get('Tmpfs')
            and host.get('RestartPolicy', {}).get('Name') == 'no', 'bootstrap_writer_isolation')
    require(all(type(value) is str and not value.split('=', 1)[0].startswith(
            ('BAO_', 'VAULT_', 'AWS_', 'ALIYUN_', 'ALIBABA_', 'ALIBABACLOUD_', 'OAM_'))
            for value in config.get('Env', [])), 'bootstrap_writer_credential_environment')
    limits = host.get('Ulimits', [])
    require(any(item == {'Name': 'core', 'Soft': 0, 'Hard': 0} for item in limits), 'bootstrap_writer_core_limit')
    mounts = row.get('Mounts')
    require(type(mounts) is list and len(mounts) == 1
            and mounts[0].get('Type') == 'bind' and mounts[0].get('Destination') == '/run/bootstrap'
            and mounts[0].get('Source') == RUN + '/bootstrap-' + args.purpose
            and mounts[0].get('RW') is True and mounts[0].get('Propagation') == 'rprivate', 'bootstrap_writer_mount')
    require(state.get('Running') is False and state.get('Status') == expected_status
            and state.get('Pid') == 0 and state.get('OOMKilled') is False
            and state.get('ExitCode') == 0, 'bootstrap_writer_process_state')
    return {'containerId': container_id, 'writerState': expected_status, 'running': False, 'exitCode': 0,
            'image': IMAGE, 'user': config['User']}


def empty_directory(args, client):
    # Host metadata only, no credential contents. Root is needed to inspect0700.
    uid, gid = IDENTITIES[args.purpose]
    script = '''import json,os,stat
from pathlib import Path
p=Path(%r)
assert p.resolve(strict=True)==p
for parent in [*p.parents,p]:
 assert not parent.is_symlink()
s=p.lstat()
assert stat.S_ISDIR(s.st_mode) and (s.st_uid,s.st_gid,stat.S_IMODE(s.st_mode))==%r
rows=[r.split() for r in Path('/proc/self/mountinfo').read_text().splitlines()]
rows=[r for r in rows if str(p)==r[4] or str(p).startswith(r[4].rstrip('/')+'/')]
r=max(rows,key=lambda row:len(row[4]))
assert r[r.index('-')+1]=='tmpfs' and {'rw','nosuid','nodev'}<=set(r[5].split(','))
assert os.listdir(p)==[]
print(json.dumps({'empty':True,'tmpfs':True,'contentsRead':False}))
''' % (RUN + '/bootstrap-' + args.purpose, (uid, gid, 0o700))
    result = json.loads(client.command(client.docker[:-1] + ['python3', '-B', '-c', script]))
    require(result == {'empty': True, 'tmpfs': True, 'contentsRead': False}, 'bootstrap_directory_preflight')


def prepare(args, client):
    client.preflight(); empty_directory(args, client)
    expected = binding(args)
    record(args.journal, 'prepare-attempt.json', expected)
    raw = client.command(create_arguments(args, client), timeout=30).decode().strip()
    require(re.fullmatch(r'[0-9a-f]{64}', raw), 'bootstrap_created_id_unknown')
    # Save the actual ID before any follow-up operation, including inspect.
    record(args.journal, 'created-container.json', {**expected, 'containerId': raw})
    checked = writer_inspect(args, client, raw, 'created')
    record(args.journal, 'prepared.json', checked)
    return {'status': 'writer_prepared', **checked, 'automaticReplayAllowed': False, 'secretIdsCreated': False}


def exact_writer(args):
    expected = binding(args)
    require(load_record(args.journal, 'prepare-attempt.json') == expected, 'bootstrap_prepare_source_changed')
    created = load_record(args.journal, 'created-container.json')
    require(type(created) is dict and set(created) == set(expected) | {'containerId'}
            and all(created[key] == value for key, value in expected.items())
            and re.fullmatch(r'[0-9a-f]{64}', created['containerId']), 'bootstrap_created_binding')
    return created['containerId']


def remote_request(args, client, token, operation):
    payload = {'operation': operation, 'purpose': args.purpose, 'attemptId': args.attempt_id,
               'instanceId': args.instance_id, 'rootToken': token}
    raw = client.command(client.docker + ['exec', '-i', '--user=23101:23101', args.container_id,
        'python3', '-B', '-c', source('runtime_bootstrap_remote.py')], json.dumps(payload).encode() + b'\n', timeout=60)
    return json.loads(raw)


def issue(args, client):
    before = client.preflight(); container_id = exact_writer(args)
    require(load_record(args.journal, 'prepared.json') == writer_inspect(args, client, container_id, 'created'),
            'bootstrap_prepared_state_changed')
    empty_directory(args, client); token, identity = read_root(args)
    # Durable marker prevents reusing the same local writer after an unknown.
    record(args.journal, 'issue-attempt.json', {**binding(args), 'writerContainerId': container_id})
    generated = remote_request(args, client, token, 'issue'); token = None
    require(type(generated) is dict and set(generated) == {'status', 'roleId', 'secretId', 'automaticReplayAllowed'}
            and generated['status'] == 'private_credential_issued' and generated['automaticReplayAllowed'] is False
            and all(type(generated[key]) is str and re.fullmatch(r'[0-9a-f-]{36}', generated[key])
                    for key in ('roleId', 'secretId')), 'bootstrap_private_response_unknown')
    require(client.preflight() == before, 'bootstrap_bao_changed')
    payload = {'operation': 'write', 'purpose': args.purpose,
               'roleId': generated['roleId'], 'secretId': generated['secretId']}
    raw = client.command(client.docker + ['start', '-a', '-i', container_id],
                         json.dumps(payload).encode() + b'\n', timeout=30)
    payload = None; generated = None
    result = json.loads(raw)
    require(result == {'status': 'private_bootstrap_written', 'bothFilesFsyncReadbackVerified': True,
            'credentialValuesEmitted': False, 'automaticReplayAllowed': False}, 'bootstrap_write_outcome_unknown')
    checked = writer_inspect(args, client, container_id, 'exited')
    require(client.preflight() == before and custody.vault_identity(args.vault_mount, args.vault_binding) == identity,
            'bootstrap_identity_changed')
    public = {**result, **checked, 'agentStarted': False, 'productionReady': False}
    record(args.journal, 'delivered.json', public)
    return public


def status(args, client):
    before = client.preflight(); token, identity = read_root(args)
    value = remote_request(args, client, token, 'status'); token = None
    require(type(value) is dict and set(value) == {'status', 'attemptPresent', 'accessorState', 'automaticReplayAllowed'}
            and value['status'] == 'read_only' and type(value['attemptPresent']) is bool
            and value['accessorState'] in ('unknown', 'not_issued', 'present', 'absent')
            and value['automaticReplayAllowed'] is False, 'bootstrap_status_shape')
    require(client.preflight() == before and custody.vault_identity(args.vault_mount, args.vault_binding) == identity,
            'bootstrap_identity_changed')
    return {**value, 'absenceDoesNotDistinguishConsumptionFromExpiry': True, 'productionReady': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('prepare', 'issue', 'status'))
    parser.add_argument('--purpose', required=True, choices=tuple(IDENTITIES))
    parser.add_argument('--attempt-id', required=True); parser.add_argument('--journal', type=Path, required=True)
    parser.add_argument('--ssh-config', type=Path, required=True); parser.add_argument('--ssh-host', required=True)
    parser.add_argument('--ssh-sudo', action='store_true'); parser.add_argument('--container-id', required=True)
    parser.add_argument('--instance-id', required=True); parser.add_argument('--run-id', required=True)
    parser.add_argument('--vault-mount', type=Path, required=True); parser.add_argument('--vault-binding', type=Path, required=True)
    args = parser.parse_args(); custody.memory_policy(); os.umask(0o077)
    try:
        require(re.fullmatch(r'[0-9a-f]{12}', args.attempt_id) and re.fullmatch(r'[0-9a-f]{12}', args.run_id),
                'bootstrap_operation_identifiers')
        client = custody.Remote(args.ssh_config, args.ssh_host, args.container_id, args.ssh_sudo, args.instance_id)
        result = {'prepare': prepare, 'issue': issue, 'status': status}[args.operation](args, client)
    except BaseException as error:
        result = {'status': 'failed_or_unknown', 'safeCode': str(error) if isinstance(error, Rejected)
                  else 'bootstrap_custody_or_transport_failed', 'automaticReplayAllowed': False,
                  'nextAction': 'read_exact_journal_container_and_bao_status', 'productionReady': False}
    print(json.dumps(result, sort_keys=True))
    return 0 if result['status'] in ('writer_prepared', 'private_bootstrap_written', 'read_only') else 1


if __name__ == '__main__':
    raise SystemExit(main())
