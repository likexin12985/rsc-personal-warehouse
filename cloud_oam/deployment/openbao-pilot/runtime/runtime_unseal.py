"""Explicit custody-backed unseal only; does not configure roles or start agents."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import warnings

import runtime_init as custody
from runtime_bundle import Rejected, require


def custody_share(args):
    pgpy = custody.pgp_module(); custody.memory_policy()
    identity = custody.vault_identity(args.vault_mount, args.vault_binding)
    parent = os.open(args.vault_mount, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    directory = None
    try:
        directory = os.open('initialization-' + args.run_id, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        info = os.fstat(directory)
        require(info.st_dev == identity['device'] and info.st_mode & 0o777 == 0o700, 'unseal_custody_directory')
        key, _ = pgpy.PGPKey.from_blob(custody.existing_file(directory, 'custody-private.asc'))
        binding = json.loads(custody.existing_file(directory, 'public-binding.json'))
        require(binding == {'runId': args.run_id, 'pgpFingerprint': str(key.fingerprint),
                            'containerId': args.container_id}, 'unseal_custody_binding')
        result = json.loads(custody.existing_file(directory, 'initialization-encrypted.json'))
        require(result.get('runId') == args.run_id, 'unseal_ciphertext_run')
        custody.custody_readback(pgpy, key, result)
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            share = key.decrypt(pgpy.PGPMessage.from_blob(base64.b64decode(result['materials']['keys_base64'][0]))).message
        if isinstance(share, (bytes, bytearray)):
            share = share.decode('ascii')
        require(type(share) is str and re.fullmatch(r'[0-9a-f]{64}', share), 'unseal_share_shape')
        require(custody.vault_identity(args.vault_mount, args.vault_binding) == identity, 'unseal_mount_changed')
        return share, str(key.fingerprint), identity
    finally:
        if directory is not None:
            os.close(directory)
        os.close(parent)


def execute(args, remote):
    before = remote.preflight()
    share, fingerprint, identity = custody_share(args)
    script = Path(__file__).with_name('runtime_unseal_remote.py').read_text()
    # Public reviewed source appears in argv; the share appears only in stdin.
    output = remote.command(remote.docker + ['exec', '-i', '--user=23101:23101', args.container_id,
            'python3', '-c', script], json.dumps({'operation': 'unseal', 'runId': args.run_id,
            'pgpFingerprint': fingerprint, 'share': share}).encode() + b'\n', timeout=55)
    share = None
    result = json.loads(output)
    require(type(result) is dict and set(result) == {'status', 'alreadyUnsealed', 'writeAttempted',
            'initialized', 'sealed', 'automaticReplayAllowed'} and result.get('status') == 'unsealed_verified'
            and result.get('initialized') is True and result.get('sealed') is False
            and result.get('automaticReplayAllowed') is False
            and type(result.get('alreadyUnsealed')) is bool and type(result.get('writeAttempted')) is bool,
            'unseal_public_response_shape')
    require(remote.preflight() == before, 'unseal_peer_changed')
    require(custody.vault_identity(args.vault_mount, args.vault_binding) == identity, 'unseal_custody_mount_changed')
    return {**result, 'pgpFingerprint': fingerprint, 'remoteSourceSha256': hashlib.sha256(script.encode()).hexdigest(),
            'configurationPerformed': False, 'agentStarted': False, 'backupRestoreDrillPassed': False,
            'productionReady': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ssh-config', type=Path, required=True); parser.add_argument('--ssh-host', required=True)
    parser.add_argument('--ssh-sudo', action='store_true'); parser.add_argument('--container-id', required=True)
    parser.add_argument('--instance-id', required=True); parser.add_argument('--run-id', required=True)
    parser.add_argument('--vault-mount', type=Path, required=True); parser.add_argument('--vault-binding', type=Path, required=True)
    args = parser.parse_args(); custody.memory_policy(); os.umask(0o077)
    try:
        require(re.fullmatch(r'[0-9a-f]{12}', args.run_id), 'unseal_run_id')
        remote = custody.Remote(args.ssh_config, args.ssh_host, args.container_id, args.ssh_sudo, args.instance_id)
        result = execute(args, remote)
    except BaseException as error:
        result = {'status': 'failed_or_unknown', 'safeCode': str(error) if isinstance(error, Rejected)
                  else 'unseal_custody_or_transport_failed', 'automaticReplayAllowed': False,
                  'nextAction': 'read_only_runtime_init_status', 'productionReady': False}
    print(json.dumps(result, sort_keys=True)); return 0 if result['status'] == 'unsealed_verified' else 1


if __name__ == '__main__':
    raise SystemExit(main())
