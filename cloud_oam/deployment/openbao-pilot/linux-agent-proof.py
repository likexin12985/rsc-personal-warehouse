"""Run only a disposable synthetic Linux Docker proof, never production setup.

Requires an existing fixed image ID, the pinned official OpenBao binary and
explicit source root. No pull/build, ports, Docker socket in containers, external
network, business volume, cloud credentials or source edits. Sensitive values
remain in controller memory / anonymous pipes / tmpfs and are never emitted.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import select
import signal
import subprocess
import time
import uuid

BAO_UID, AGENT_UID, API_UID, SHARED_GID = 23101, 23102, 23103, 23110
BINARY_SHA256 = '535cf827b13753046757f5ec8b97ae0ef21f10a40ffe673f0d5e75616170f5ea'
POLICY, AUTH, ROLE = 'rsc-linux-agent-proof', 'rsc-pilot', 'synthetic'
TOKEN_TTL = 30
KEYS = ('rsc-authentication-idempotency', 'rsc-material-request-contact')


class Failure(RuntimeError):
    pass


def require(condition, code):
    if not condition:
        raise Failure(code)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class Proof:
    def __init__(self, args):
        self.args = args
        self.repo = Path(args.source_root).resolve(strict=True)
        self.directory = self.repo / 'cloud_oam/deployment/openbao-pilot'
        self.binary = Path(args.binary).resolve(strict=True)
        require(re.fullmatch(r'[0-9a-f]{12}', args.run_id) is not None, 'run_id_shape')
        self.prefix = 'rsc-linux-agent-' + args.run_id
        self.journal_path = Path(args.receipt_journal)
        require(self.journal_path.is_absolute() and self.journal_path.parent.is_dir(), 'journal_path')
        self.containers = []
        self.container_ids = {}
        self.volumes = []
        self.created_volumes = []
        self.process = None
        self.root_token = None
        self.shares = []
        self.checks = []
        self.evidence = {}
        self.stage = 'preflight'
        names = ('linux-agent-proof.py', 'linux-agent-worker.py', 'linux-agent-server.json',
                 'agent-autoauth.json.example', 'api-decrypt-policy.json.example', 'official_release_2.7.1.json')
        self.sources = {name: self.directory / name for name in names}
        for name in ('openbao_runtime_transport.py', 'openbao_transit_candidate.py'):
            self.sources['app/' + name] = self.repo / 'cloud_oam/backend/app' / name
        self.hashes = {name: digest(path) for name, path in self.sources.items()}
        with os.fdopen(os.open(self.journal_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), 'w') as stream:
            json.dump({'schema': 'rsc.openbao.linux-agent-journal.v1', 'runId': args.run_id,
                       'prefix': self.prefix, 'stage': 'reserved', 'syntheticOnly': True}, stream)
        self.journal()

    def journal(self, cleanup=None):
        document = {'schema': 'rsc.openbao.linux-agent-journal.v1', 'runId': self.args.run_id,
                    'prefix': self.prefix, 'stage': self.stage, 'syntheticOnly': True,
                    'plannedContainerNames': self.containers, 'createdContainerIds': self.container_ids,
                    'plannedVolumeNames': self.volumes, 'createdVolumeNames': self.created_volumes,
                    'sourceSha256': self.hashes}
        if cleanup is not None:
            document['cleanupCompleted'] = cleanup
        temporary = self.journal_path.with_name(self.journal_path.name + '.' + uuid.uuid4().hex + '.tmp')
        with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), 'w') as stream:
            json.dump(document, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self.journal_path)

    def record_container(self, name):
        item = json.loads(self.docker(['inspect', name]).stdout)[0]
        require(item['Config'].get('Labels', {}).get('rsc.openbao.proof.run-id') == self.args.run_id,
                'container_ownership')
        require(name not in self.container_ids or self.container_ids[name] == item['Id'],
                'container_identity_changed')
        self.container_ids[name] = item['Id']
        self.journal()

    def docker(self, args, payload=None, timeout=20, allow_failure=False):
        encoded = None if payload is None else json.dumps(payload)
        result = subprocess.run(['docker', '-H', 'unix:///var/run/docker.sock', *args],
            input=encoded, capture_output=True, text=True, timeout=timeout,
            env={'PATH': os.environ.get('PATH', '/usr/bin:/bin')})
        require(allow_failure or result.returncode == 0, 'docker_operation_failed')
        require(len(result.stdout) < 2 * 1024 * 1024, 'docker_output_bound')
        return result

    def base(self, name, uid, gid, memory, cpus, pids):
        require(not self.docker(['ps', '-a', '--filter=name=^/' + name + '$', '--format={{.ID}}']).stdout.strip(),
                'existing_container_name')
        self.containers.append(name)
        self.journal()
        args = ['run', '--name', name, '--pull=never', '--network=none', '--read-only',
                '--cap-drop=ALL', '--security-opt=no-new-privileges=true', '--log-driver=none',
                '--ulimit=core=0:0', '--pids-limit=' + str(pids), '--memory=' + memory,
                '--memory-swap=' + memory, '--cpus=' + str(cpus), '--user=' + str(uid) + ':' + str(gid),
                '--group-add=' + str(SHARED_GID), '--env=GOMAXPROCS=2', '--env=GOMEMLIMIT=260MiB',
                '--label=rsc.openbao.proof.run-id=' + self.args.run_id,
                '--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=8388608,mode=1777']
        for name_key, path in self.sources.items():
            target = 'agent-autoauth.json' if name_key == 'agent-autoauth.json.example' else name_key
            args.extend(['--mount', 'type=bind,src=' + str(path) + ',dst=/proof/' + target + ',readonly'])
        args.extend(['--mount', 'type=bind,src=' + str(self.binary) + ',dst=/proof/bao,readonly'])
        return args

    def volume(self, suffix):
        name = self.prefix + '-' + suffix
        require(name not in self.docker(['volume', 'ls', '--format={{.Name}}']).stdout.splitlines(),
                'existing_volume_name')
        self.volumes.append(name)
        self.journal()
        self.docker(['volume', 'create', '--driver=local', '--opt=type=tmpfs',
                     '--opt=device=tmpfs', '--opt=o=size=1048576,nodev,nosuid,noexec',
                     '--label=rsc.openbao.proof.run-id=' + self.args.run_id, name])
        created = json.loads(self.docker(['volume', 'inspect', name]).stdout)[0]
        require(created.get('Labels', {}).get('rsc.openbao.proof.run-id') == self.args.run_id, 'created_volume_ownership')
        self.created_volumes.append(name)
        self.journal()
        return name

    @staticmethod
    def mounts(mapping):
        result = []
        for volume, target, readonly in mapping:
            result += ['--mount', 'type=volume,src=' + volume + ',dst=' + target + (',readonly' if readonly else '')]
        return result

    def worker(self, container, operation, payload=None, user=None, timeout=20):
        args = ['exec', '-i']
        if user:
            args += ['--user', user]
        args += [container, 'python', '/proof/linux-agent-worker.py', operation]
        result = json.loads(self.docker(args, payload, timeout).stdout)
        require(not result.get('failed'), 'worker_operation_failed')
        return result

    def rpc(self, method, path, body=None, authenticated=True, accepted=(200, 204)):
        result = self.worker(self.server, 'rpc', {'method': method, 'path': path, 'body': body,
            'token': self.root_token if authenticated else None}, timeout=30)
        require(result['status'] in accepted, 'fixture_rpc_rejected')
        return result['body']

    def api_response(self, timeout=20):
        require(self.process is not None, 'api_not_started')
        ready, _, _ = select.select([self.process.stdout], [], [], timeout)
        require(bool(ready), 'api_protocol_deadline')
        line = self.process.stdout.readline(65536)
        require(bool(line) and len(line) < 65536, 'api_protocol_shape')
        result = json.loads(line)
        require(not result.get('failed'), 'api_' + result.get('safeCode', 'unknown_failure'))
        return result

    def api_command(self, op, wrapped=None, expect=None, timeout=20):
        self.process.stdin.write(json.dumps({'op': op, 'wrapped': wrapped, 'expect': expect}) + '\n')
        self.process.stdin.flush()
        return self.api_response(timeout)

    def unseal(self):
        for index, share in enumerate(self.shares[:2]):
            body = self.rpc('POST', '/v1/sys/unseal', {'key': share}, authenticated=False)
            require(body['sealed'] == (index == 0), 'manual_threshold')
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            body = self.rpc('GET', '/v1/sys/health', authenticated=False, accepted=(200, 429, 503))
            if body.get('sealed') is False and body.get('standby') is False:
                require(body['version'] == '2.7.1', 'server_version')
                return
            time.sleep(0.2)
        raise Failure('unseal_readiness_deadline')

    def run(self):
        require(platform.system() == 'Linux' and platform.machine() == 'x86_64', 'linux_amd64_required')
        require(re.fullmatch(r'sha256:[0-9a-f]{64}', self.args.image) is not None, 'fixed_local_image_required')
        require(digest(self.binary) == BINARY_SHA256, 'official_binary_digest')
        image = json.loads(self.docker(['image', 'inspect', self.args.image]).stdout)[0]
        require(image['Id'] == self.args.image and image['Architecture'] == 'amd64'
                and image['Os'] == 'linux', 'image_identity')
        require(not image['Config'].get('Volumes'), 'image_implicit_volumes')
        require(not any(value.split('=', 1)[0].startswith(('BAO_', 'VAULT_', 'ALIYUN_', 'ALIBABA_', 'ALIBABACLOUD_', 'AWS_', 'OAM_'))
                        for value in image['Config'].get('Env', [])), 'image_credential_environment')
        self.evidence['imageId'] = image['Id']
        self.stage = 'tmpfs_setup'
        socket_volume, token_volume, bootstrap_volume = [self.volume(name) for name in ('socket', 'token', 'bootstrap')]
        keeper = self.prefix + '-keeper'
        args = self.base(keeper, 0, 0, '32m', 0.1, 16)
        args += ['--cap-add=CHOWN', '--cap-add=SETUID', '--cap-add=SETGID']
        args += self.mounts([(socket_volume, '/run/rsc-bao', False), (token_volume, '/run/rsc-bao-token', False),
                             (bootstrap_volume, '/run/rsc-bao-bootstrap', False)])
        args += ['-d', '--entrypoint=python', self.args.image, '/proof/linux-agent-worker.py', 'keeper']
        self.docker(args)
        self.record_container(keeper)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            value = json.loads(self.docker(['exec', keeper, 'python', '/proof/linux-agent-worker.py', 'stat', '/run/rsc-bao']).stdout)
            if value.get('uid') == BAO_UID and value.get('gid') == SHARED_GID and value.get('mode') == '0o750':
                break
            time.sleep(0.1)
        else:
            raise Failure('tmpfs_owner_deadline')

        self.stage = 'server_start'
        self.server = self.prefix + '-bao'
        args = self.base(self.server, BAO_UID, BAO_UID, '384m', 0.5, 128)
        args += self.mounts([(socket_volume, '/run/rsc-bao', False)])
        args += ['--tmpfs=/state:rw,noexec,nosuid,nodev,size=134217728,mode=0700,uid=23101,gid=23101',
                 '-d', '--entrypoint=/proof/bao', self.args.image, 'server', '-config=/proof/linux-agent-server.json']
        self.docker(args)
        self.record_container(self.server)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                self.rpc('GET', '/v1/sys/seal-status', authenticated=False)
                break
            except Failure:
                time.sleep(0.2)
        else:
            raise Failure('server_start_deadline')
        self.evidence['baoBoundary'] = self.worker(self.server, 'bao-boundary')['boundary']
        body = self.rpc('POST', '/v1/sys/init', {'secret_shares': 3, 'secret_threshold': 2}, authenticated=False)
        self.root_token, self.shares = body['root_token'], body['keys_base64']
        self.unseal()

        self.stage = 'synthetic_fixture'
        policy = self.sources['api-decrypt-policy.json.example'].read_text()
        self.rpc('PUT', '/v1/sys/policies/acl/' + POLICY, {'policy': policy})
        self.rpc('POST', '/v1/sys/mounts/transit', {'type': 'transit'})
        wrapped = {}
        for key in KEYS:
            self.rpc('POST', '/v1/transit/keys/' + key, {'type': 'aes256-gcm96', 'derived': True,
                'exportable': False, 'allow_plaintext_backup': False})
            context = base64.b64encode(('linux-agent-synthetic:' + key).encode()).decode()
            aad = base64.b64encode(b'linux-agent-synthetic').decode()
            result = self.rpc('POST', '/v1/transit/datakey/wrapped/' + key,
                {'bits': 256, 'context': context, 'associated_data': aad})
            wrapped[key] = {'ciphertext': result['data']['ciphertext'], 'context': context, 'associated_data': aad}
        self.rpc('POST', '/v1/sys/auth/' + AUTH, {'type': 'approle'})
        role_path = f'/v1/auth/{AUTH}/role/{ROLE}'
        self.rpc('POST', role_path, {'bind_secret_id': True, 'secret_id_num_uses': 20,
            'secret_id_ttl': '3m', 'token_type': 'batch', 'token_ttl': '30s', 'token_max_ttl': '30s',
            'token_num_uses': 0, 'token_no_default_policy': True, 'token_policies': [POLICY]})
        role_config = self.rpc('GET', role_path)['data']
        require(role_config['token_policies'] == [POLICY] and role_config['token_no_default_policy'] is True
                and role_config['token_type'] == 'batch' and role_config['token_ttl'] == TOKEN_TTL
                and role_config['token_max_ttl'] == TOKEN_TTL, 'exact_approle_readback')
        self.checks.append('real_exact_approle_readback')
        role_id = self.rpc('GET', role_path + '/role-id')['data']['role_id']
        secret_id = self.rpc('POST', role_path + '/secret-id', {})['data']['secret_id']
        self.worker(keeper, 'bootstrap', {'role-id': role_id, 'secret-id': secret_id}, user=f'{AGENT_UID}:{SHARED_GID}')
        secret_id = role_id = None

        self.stage = 'agent_start'
        self.agent = self.prefix + '-agent'
        args = self.base(self.agent, AGENT_UID, SHARED_GID, '128m', 0.25, 64)
        args += self.mounts([(socket_volume, '/run/rsc-bao', True), (token_volume, '/run/rsc-bao-token', False),
                             (bootstrap_volume, '/run/rsc-bao-bootstrap', False)])
        args += ['-d', '--entrypoint=/proof/bao', self.args.image, 'agent', '-log-level=error',
                 '-config=/proof/agent-autoauth.json']
        self.docker(args)
        self.record_container(self.agent)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            result = self.docker(['exec', self.agent, 'python', '/proof/linux-agent-worker.py', 'stat',
                                  '/run/rsc-bao-token/api.token'], allow_failure=True)
            if result.returncode == 0:
                break
            time.sleep(0.2)
        else:
            raise Failure('agent_projection_deadline')
        self.evidence['agentBoundary'] = self.worker(self.agent, 'agent-boundary')
        require(self.evidence['agentBoundary']['secretIdRemoved'], 'bootstrap_file_not_removed')

        self.stage = 'api_real_transport'
        api_name = self.prefix + '-api'
        args = self.base(api_name, API_UID, API_UID, '64m', 0.25, 32)
        args += ['--pid=container:' + self.server]
        args += self.mounts([(socket_volume, '/run/rsc-bao', True), (token_volume, '/run/rsc-bao-token', True)])
        args += ['-i', '--entrypoint=python', self.args.image, '/proof/linux-agent-worker.py', 'api']
        self.process = subprocess.Popen(['docker', '-H', 'unix:///var/run/docker.sock', *args],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1,
            env={'PATH': os.environ.get('PATH', '/usr/bin:/bin')})
        self.evidence['apiBoundary'] = self.api_response()
        self.record_container(api_name)
        self.checks.extend(['three_real_nonroot_uids', 'linux_so_peercred_same_pid_namespace',
            'socket_0660_bao_shared_group', 'token_0440_agent_shared_group', 'two_private_directories_0750',
            'api_two_readonly_tmpfs_directory_mounts', 'api_write_attempts_denied',
            'api_no_bootstrap_or_raft_mounts', 'no_capabilities_no_new_privileges_seccomp',
            'api_cannot_open_peer_process_environment',
            'network_none_readonly_root_hard_cgroup_limits', 'bootstrap_consumed'])
        self.evidence['initialDecrypt'] = self.api_command('initial', wrapped, 200)
        self.checks.append('real_transport_two_purpose_decrypt')
        self.stage = 'natural_rotation'
        self.evidence['rotatedDecrypt'] = self.api_command('rotated', wrapped, 200, timeout=50)
        self.checks.append('same_transport_new_token_and_inode_across_readonly_directory')

        self.stage = 'sealed_failure'
        self.rpc('POST', '/v1/sys/seal', {})
        require(self.rpc('GET', '/v1/sys/seal-status', authenticated=False)['sealed'] is True, 'seal_readback')
        self.evidence['sealedDecrypt'] = self.api_command('sealed', wrapped, 503)
        self.checks.append('real_transport_sealed_denies_both_purposes')
        self.unseal()
        self.evidence['unsealedDecrypt'] = self.api_command('unsealed', wrapped, 200)
        self.checks.append('same_transport_after_manual_unseal_positive_control')

        self.stage = 'natural_expiry'
        self.docker(['stop', '--time=5', self.agent])
        require(json.loads(self.docker(['inspect', self.agent]).stdout)[0]['State']['Running'] is False, 'agent_stop_readback')
        time.sleep(TOKEN_TTL + 2)
        self.evidence['expiredDecrypt'] = self.api_command('expired', wrapped, 403)
        self.checks.append('real_transport_natural_expiry_after_projector_stop')
        self.api_command('exit')
        require(self.process.wait(timeout=10) == 0, 'api_exit')
        self.evidence['containers'] = self.inspect_boundaries()
        require(all(digest(path) == self.hashes[name] for name, path in self.sources.items()), 'source_changed')
        require(digest(self.binary) == BINARY_SHA256, 'binary_changed')
        self.checks.append('source_inputs_unchanged')

    def inspect_boundaries(self):
        safe = []
        for name in self.containers:
            item = json.loads(self.docker(['inspect', name]).stdout)[0]
            config = item['HostConfig']
            role = name.rsplit('-', 1)[-1]
            require(config['NetworkMode'] == 'none' and config['ReadonlyRootfs']
                    and 'ALL' in config['CapDrop'] and not item['NetworkSettings']['Ports']
                    and all(mount['Destination'] != '/var/run/docker.sock' for mount in item['Mounts']),
                    'inspect_runtime_boundary')
            if role != 'keeper':
                require(not config['CapAdd'], 'inspect_service_capabilities')
            if role == 'api':
                private = {mount['Destination']: mount for mount in item['Mounts'] if mount['Type'] == 'volume'}
                require(set(private) == {'/run/rsc-bao', '/run/rsc-bao-token'}
                        and all(not value['RW'] for value in private.values()), 'inspect_api_mount_boundary')
            safe.append({'role': name.rsplit('-', 1)[-1], 'user': item['Config']['User'],
                'networkMode': config['NetworkMode'], 'readOnlyRootfs': config['ReadonlyRootfs'],
                'memoryBytes': config['Memory'], 'memorySwapBytes': config['MemorySwap'],
                'nanoCpus': config['NanoCpus'], 'pidsLimit': config['PidsLimit'],
                'capDrop': config['CapDrop'], 'capAdd': config['CapAdd'], 'securityOpt': config['SecurityOpt'],
                'portsPublished': bool(item['NetworkSettings']['Ports']),
                'mounts': [{'destination': mount['Destination'], 'rw': mount['RW'], 'type': mount['Type']}
                           for mount in item['Mounts']],
                'oomKilled': item['State']['OOMKilled'], 'exitCode': item['State']['ExitCode']})
            require(not item['State']['OOMKilled'], 'container_oom')
        return safe

    def cleanup(self):
        success = True
        for name in reversed(self.containers):
            try:
                identifiers = self.docker(['ps', '-a', '--no-trunc', '--filter=name=^/' + name + '$',
                                            '--format={{.ID}}']).stdout.splitlines()
                if identifiers:
                    require(len(identifiers) == 1, 'container_cleanup_ambiguity')
                    self.record_container(name)
                    require(self.container_ids[name] == identifiers[0], 'container_cleanup_identity')
                    self.docker(['rm', '-f', identifiers[0]])
                require(not self.docker(['ps', '-a', '--filter=name=^/' + name + '$', '--format={{.ID}}']).stdout.strip(),
                        'container_cleanup')
            except Exception:
                success = False
        if self.process:
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
                success = False
        for volume in reversed(self.volumes):
            try:
                existing = self.docker(['volume', 'ls', '--format={{.Name}}']).stdout.splitlines()
                if volume in existing:
                    item = json.loads(self.docker(['volume', 'inspect', volume]).stdout)[0]
                    require(item.get('Labels', {}).get('rsc.openbao.proof.run-id') == self.args.run_id,
                            'volume_cleanup_ownership')
                    self.docker(['volume', 'rm', volume])
                require(volume not in self.docker(['volume', 'ls', '--format={{.Name}}']).stdout.splitlines(),
                        'volume_cleanup')
            except Exception:
                success = False
        self.root_token = None
        self.shares.clear()
        try:
            self.journal(cleanup=success)
        except Exception:
            success = False
        return success


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', required=True)
    parser.add_argument('--binary', required=True)
    parser.add_argument('--image', required=True)
    parser.add_argument('--run-id', required=True, help='Fresh explicit 12 lowercase hex characters; never retry an unknown run.')
    parser.add_argument('--receipt-journal', required=True, help='New absolute nonsecret journal path; existing file is rejected.')
    args = parser.parse_args()
    def interrupted(_signum, _frame):
        raise Failure('bounded_run_interrupted')
    for event in (signal.SIGINT, signal.SIGTERM, signal.SIGALRM):
        signal.signal(event, interrupted)
    signal.alarm(160)
    start, proof, result = time.monotonic(), None, {'schema': 'rsc.openbao.linux-agent-proof.v1',
        'syntheticOnly': True, 'productionReady': False, 'productionConfigured': False,
        'businessDataUsed': False, 'containerMemoryLimitTotalBytes': 608 * 1024 * 1024,
        'openbaoVersion': '2.7.1', 'binarySha256': BINARY_SHA256}
    try:
        proof = Proof(args)
        proof.run()
        result.update(status='passed', checks=proof.checks, checkCount=len(proof.checks), evidence=proof.evidence,
                      sourceSha256=proof.hashes, sourceInputsStable=True)
    except Exception as error:
        result.update(status='failed', stage=proof.stage if proof else 'preflight',
                      safeCode=str(error) if isinstance(error, Failure) else 'unexpected_proof_failure')
        if proof:
            result.update(checks=proof.checks, checkCount=len(proof.checks), evidence=proof.evidence,
                          sourceSha256=proof.hashes)
    finally:
        signal.alarm(0)
        result['cleanupCompleted'] = proof.cleanup() if proof else True
    if not result['cleanupCompleted']:
        result['status'] = 'failed'
    if proof:
        result['runId'], result['prefix'] = proof.args.run_id, proof.prefix
        result['createdContainerIds'], result['createdVolumeNames'] = proof.container_ids, proof.created_volumes
        result['plannedVolumeNames'] = proof.volumes
    result['elapsedSeconds'] = round(time.monotonic() - start, 3)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
