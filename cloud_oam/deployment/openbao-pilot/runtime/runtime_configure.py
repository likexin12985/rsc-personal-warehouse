"""Explicit auth/OIDC configure phases after custody and unseal; no Agent start."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import warnings

sys.dont_write_bytecode = True
import runtime_init as custody
from runtime_bundle import Rejected, require


def read_root(args):
    pgpy = custody.pgp_module(); custody.memory_policy()
    identity = custody.vault_identity(args.vault_mount, args.vault_binding)
    parent = os.open(args.vault_mount, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    directory = None
    try:
        directory = os.open('initialization-' + args.run_id, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        info = os.fstat(directory)
        require(info.st_dev == identity['device'] and info.st_mode & 0o777 == 0o700, 'configure_custody_directory')
        key, _ = pgpy.PGPKey.from_blob(custody.existing_file(directory, 'custody-private.asc'))
        binding = json.loads(custody.existing_file(directory, 'public-binding.json'))
        require(binding == {'runId': args.run_id, 'pgpFingerprint': str(key.fingerprint),
                            'containerId': args.container_id}, 'configure_custody_binding')
        result = json.loads(custody.existing_file(directory, 'initialization-encrypted.json'))
        require(result.get('runId') == args.run_id, 'configure_ciphertext_run')
        custody.custody_readback(pgpy, key, result)
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            root = key.decrypt(pgpy.PGPMessage.from_blob(base64.b64decode(result['materials']['root_token']))).message
        if isinstance(root, (bytes, bytearray)):
            root = root.decode('ascii')
        require(type(root) is str and re.fullmatch(r'[A-Za-z0-9._-]{10,4096}', root), 'configure_root_shape')
        require(custody.vault_identity(args.vault_mount, args.vault_binding) == identity, 'configure_mount_changed')
        return root, identity
    finally:
        if directory is not None:
            os.close(directory)
        os.close(parent)


def public_details(phase, value):
    require(type(value) is dict, 'configure_public_details')
    if phase == 'auth':
        require(set(value) == {'entities', 'policyBusinessAndSelfPathsVerified', 'roleIdsEmitted', 'secretIdsCreated'}
                and value['policyBusinessAndSelfPathsVerified'] is True and value['roleIdsEmitted'] is False
                and value['secretIdsCreated'] is False and type(value['entities']) is dict
                and set(value['entities']) == {'transit', 'oss', 'pnvs'}
                and len(set(value['entities'].values())) == 3
                and all(type(item) is str and re.fullmatch(r'[0-9a-f-]{36}', item)
                        for item in value['entities'].values()), 'configure_public_entities')
    else:
        require(value == {'issuer': 'https://rscwz.cn/v1/identity/oidc', 'jwtTtlSeconds': 600,
                          'cloudTrustConfigured': False, 'jwtIssued': False}, 'configure_public_oidc')
    return value


def execute(args, client):
    before = client.preflight(); token, identity = read_root(args)
    script = Path(__file__).with_name('runtime_configure_remote.py').read_text()
    payload = {'phase': args.phase, 'runId': args.run_id, 'instanceId': args.instance_id,
               'ossAudience': args.oss_audience, 'pnvsAudience': args.pnvs_audience, 'rootToken': token, 'apply': args.apply}
    raw = client.command(client.docker + ['exec', '-i', '--user=23101:23101', args.container_id,
            'python3', '-B', '-c', script], json.dumps(payload).encode() + b'\n', timeout=180)
    token = None; payload = None
    result = json.loads(raw)
    require(type(result) is dict and set(result) == {'status', 'phase', 'runId', 'details',
            'automaticReplayAllowed', 'productionReady', 'writeModeEnabled'} and result.get('status') == 'phase_verified'
            and result.get('phase') == args.phase and result.get('runId') == args.run_id
            and result.get('automaticReplayAllowed') is False and result.get('productionReady') is False,
            'configure_public_response')
    require(result['writeModeEnabled'] is args.apply, 'configure_mode_response')
    public_details(args.phase, result['details'])
    require(client.preflight() == before, 'configure_peer_changed')
    require(custody.vault_identity(args.vault_mount, args.vault_binding) == identity, 'configure_mount_changed')
    return {**result, 'remoteSourceSha256': hashlib.sha256(script.encode()).hexdigest(),
            'agentStarted': False, 'transitDataKeysGenerated': False, 'cloudMutationPerformed': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=('auth', 'oidc'))
    parser.add_argument('--apply', action='store_true', help='Explicit creation; default only reads and verifies existing objects.')
    parser.add_argument('--ssh-config', type=Path, required=True); parser.add_argument('--ssh-host', required=True)
    parser.add_argument('--ssh-sudo', action='store_true'); parser.add_argument('--container-id', required=True)
    parser.add_argument('--instance-id', required=True); parser.add_argument('--run-id', required=True)
    parser.add_argument('--vault-mount', type=Path, required=True); parser.add_argument('--vault-binding', type=Path, required=True)
    parser.add_argument('--oss-audience', required=True); parser.add_argument('--pnvs-audience', required=True)
    args = parser.parse_args(); custody.memory_policy(); os.umask(0o077)
    try:
        require(re.fullmatch(r'[0-9a-f]{12}', args.run_id), 'configure_run_id')
        client = custody.Remote(args.ssh_config, args.ssh_host, args.container_id, args.ssh_sudo, args.instance_id)
        result = execute(args, client)
    except BaseException as error:
        result = {'status': 'failed_or_unknown', 'safeCode': str(error) if isinstance(error, Rejected)
                  else 'configuration_custody_or_transport_failed', 'automaticReplayAllowed': False,
                  'nextAction': 'read_only_exact_phase_objects', 'productionReady': False}
    print(json.dumps(result, sort_keys=True)); return 0 if result['status'] == 'phase_verified' else 1


if __name__ == '__main__':
    raise SystemExit(main())
