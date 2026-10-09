"""macOS custody controller: initialize once with native OpenBao PGP wrapping.

No mount/password UI, unseal/configuration, plaintext terminal output, silent
retry, or secret write outside the verified encrypted image. Run status after
interruption; recover fetches the existing ciphertext and NEVER repeats init.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import plistlib
import re
import resource
import shlex
import stat
import subprocess
import sys
import warnings

from runtime_bundle import BAO_SHA256, BINARY, CONFIG, IMAGE, RUN, STATE, Rejected, build, require
from runtime_init_remote import encrypted_materials

EXTERNAL = Path('/Volumes/Seagate Backup Plus Drive')
EXTERNAL_UUID = '4FBD5E6D-037F-4DE4-BE21-75A186DB7CDC'
IMAGE_PATH = EXTERNAL / 'RSC-recovery-7ab884150e2e/RSC-recovery.dmg'
IMAGE_VOLUME_UUID = '67A771D2-90A4-3F23-B837-F2EB70BFC7F8'


def native(argv):
    result = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, timeout=20, env={'PATH': '/usr/bin:/bin:/usr/sbin:/sbin'})
    require(result.returncode == 0 and len(result.stdout) <= 2 * 1024 ** 2, 'native_identity_probe_failed')
    return plistlib.loads(result.stdout)


def canonical_directory(path):
    require(path.is_absolute() and path.resolve(strict=True) == path and path.is_dir(), 'directory_not_canonical')
    for part in [*path.parents, path]:
        require(not part.is_symlink(), 'directory_symlink')


def image_stat():
    info = IMAGE_PATH.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_dev == EXTERNAL.stat().st_dev,
            'image_file_identity')
    return {'device': info.st_dev, 'inode': info.st_ino, 'size': info.st_size,
            'birthtime': info.st_birthtime}


def prepare_vault(binding_path):
    require(sys.platform == 'darwin', 'macos_custody_required')
    canonical_directory(EXTERNAL); canonical_directory(IMAGE_PATH.parent)
    require(os.path.ismount(EXTERNAL), 'actual_mount_required')
    outer = native(['/usr/sbin/diskutil', 'info', '-plist', str(EXTERNAL)])
    require(outer.get('VolumeUUID') == EXTERNAL_UUID and outer.get('MountPoint') == str(EXTERNAL)
            and outer.get('BusProtocol') == 'USB' and outer.get('Internal') is False
            and outer.get('WritableVolume') is True, 'external_volume_changed')
    attached = native(['/usr/bin/hdiutil', 'info', '-plist'])
    require(not any(item.get('image-path') == str(IMAGE_PATH) for item in attached.get('images', [])),
            'closed_image_required_for_encryption_probe')
    before = image_stat()
    require(native(['/usr/bin/hdiutil', 'isencrypted', str(IMAGE_PATH), '-plist']).get('encrypted') is True,
            'closed_image_encryption_unverified')
    digest = hashlib.sha256()
    with IMAGE_PATH.open('rb') as source:
        while part := source.read(1024 * 1024):
            digest.update(part)
    require(image_stat() == before, 'closed_image_changed')
    result = {'schema': 'rsc.custody-pre-attachment.v1', 'imagePath': str(IMAGE_PATH),
              'outerVolumeUuid': EXTERNAL_UUID, 'imageVolumeUuid': IMAGE_VOLUME_UUID,
              'closedImageEncrypted': True, 'closedImageSha256': digest.hexdigest(), 'imageStat': before}
    canonical_directory(binding_path.parent)
    fd = os.open(binding_path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        new_file(fd, binding_path.name, json.dumps(result, sort_keys=True).encode())
    finally:
        os.close(fd)
    return {'status': 'closed_image_binding_created', 'credentialsRead': False, 'productionReady': False}


def vault_identity(mount, binding_path):
    require(sys.platform == 'darwin', 'macos_custody_required')
    canonical_directory(EXTERNAL); canonical_directory(IMAGE_PATH.parent); canonical_directory(mount)
    require(os.path.ismount(EXTERNAL) and os.path.ismount(mount), 'actual_mount_required')
    outer = native(['/usr/sbin/diskutil', 'info', '-plist', str(EXTERNAL)])
    require(outer.get('MountPoint') == str(EXTERNAL) and outer.get('VolumeUUID') == EXTERNAL_UUID
            and outer.get('BusProtocol') == 'USB' and outer.get('Internal') is False
            and outer.get('WritableVolume') is True, 'external_volume_changed')
    canonical_directory(binding_path.parent)
    descriptor = os.open(binding_path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        binding = json.loads(existing_file(descriptor, binding_path.name))
    finally:
        os.close(descriptor)
    # isencrypted is not supported reliably while mounted. Bind the earlier
    # successful CLOSED-image native probe to this same inode/device/image.
    require(binding.get('schema') == 'rsc.custody-pre-attachment.v1'
            and binding.get('imagePath') == str(IMAGE_PATH) and binding.get('outerVolumeUuid') == EXTERNAL_UUID
            and binding.get('imageVolumeUuid') == IMAGE_VOLUME_UUID and binding.get('closedImageEncrypted') is True
            and binding.get('imageStat') == image_stat(), 'pre_attachment_binding_changed')
    attached = native(['/usr/bin/hdiutil', 'info', '-plist'])
    images = [item for item in attached.get('images', []) if item.get('image-path') == str(IMAGE_PATH)]
    require(len(images) == 1 and [entity.get('mount-point') for entity in images[0].get('system-entities', [])
            if entity.get('mount-point')] == [str(mount)], 'exact_image_attachment_required')
    volume = native(['/usr/sbin/diskutil', 'info', '-plist', str(mount)])
    require(volume.get('MountPoint') == str(mount) and volume.get('VolumeUUID') == IMAGE_VOLUME_UUID
            and volume.get('Writable') is True and volume.get('WritableVolume') is True,
            'image_mount_identity_or_readonly')
    fs = os.statvfs(mount)
    require(not fs.f_flag & os.ST_RDONLY and fs.f_bavail * fs.f_frsize >= 4 * 1024 ** 2,
            'image_mount_not_writable_or_full')
    value = mount.stat()
    return {'outerDevice': EXTERNAL.stat().st_dev, 'imageInode': binding['imageStat']['inode'],
            'device': value.st_dev, 'inode': value.st_ino, 'volumeUuid': IMAGE_VOLUME_UUID}


def memory_policy():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    # macOS mlockall(3) returned ENOSYS in an independent no-secret probe.
    # Do not claim secure/all-heap locked memory or invent a new launch gate.
    return {'coreDumpsDisabled': True, 'pythonHeapLocked': False}


def new_file(directory_fd, name, body):
    fd = os.open(name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory_fd)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(os.dup(fd), 'wb') as stream:
            stream.write(body); stream.flush(); os.fsync(stream.fileno())
        os.lseek(fd, 0, os.SEEK_SET)
        require(os.read(fd, len(body) + 1) == body, 'custody_file_readback_mismatch')
        os.fsync(directory_fd)
    finally:
        os.close(fd)


def existing_file(directory_fd, name, maximum=65536):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd)
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1
                and 0 < info.st_size <= maximum, 'custody_file_identity')
        return os.read(fd, maximum + 1)
    finally:
        os.close(fd)


def pgp_module():
    require(version('PGPy') == '0.6.0', 'locked_pgpy_runtime_required')
    import pgpy
    return pgpy


def create_custody_key(pgpy):
    from pgpy.constants import (CompressionAlgorithm, HashAlgorithm, KeyFlags,
                                PubKeyAlgorithm, SymmetricKeyAlgorithm)
    key = pgpy.PGPKey.new(PubKeyAlgorithm.RSAEncryptOrSign, 3072)
    uid = pgpy.PGPUID.new('RSC pilot recovery custody')
    key.add_uid(uid, usage={KeyFlags.EncryptCommunications, KeyFlags.EncryptStorage},
                hashes=[HashAlgorithm.SHA256], ciphers=[SymmetricKeyAlgorithm.AES256],
                compression=[CompressionAlgorithm.Uncompressed])
    return key


def custody_readback(pgpy, key, result):
    require(result.get('status') == 'initialized_pending_custody'
            and result.get('pgpFingerprint') == str(key.fingerprint), 'custody_public_binding')
    materials = encrypted_materials(result.get('materials'))
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        share = key.decrypt(pgpy.PGPMessage.from_blob(base64.b64decode(materials['keys_base64'][0]))).message
        root = key.decrypt(pgpy.PGPMessage.from_blob(base64.b64decode(materials['root_token']))).message
    if isinstance(share, (bytes, bytearray)):
        share = share.decode('ascii')
    if isinstance(root, (bytes, bytearray)):
        root = root.decode('ascii')
    require(type(share) is str and re.fullmatch(r'[0-9a-f]{64}', share), 'custody_share_decrypt_failed')
    require(type(root) is str and re.fullmatch(r'[A-Za-z0-9._-]{10,4096}', root), 'custody_root_decrypt_failed')
    # Do not return either plaintext, its hash, or an exception carrying it.
    return True


class Remote:
    def __init__(self, config, host, container, sudo=False, instance=None):
        require(config.is_absolute() and config.is_file() and not config.is_symlink(), 'ssh_config_required')
        require(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}', host), 'ssh_alias_invalid')
        require(re.fullmatch(r'[0-9a-f]{64}', container), 'exact_container_id_required')
        self.prefix = ['/usr/bin/ssh', '-F', str(config), '-T', '-oBatchMode=yes',
                       '-oStrictHostKeyChecking=yes', '-oForwardAgent=no', '-oLogLevel=ERROR', host]
        self.docker = (['sudo', '-n'] if sudo else []) + ['/usr/bin/docker']
        self.container = container
        self.instance = instance

    def command(self, argv, body=None, timeout=20):
        result = subprocess.run(self.prefix + [shlex.join(argv)], input=body,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=timeout,
                env={'PATH': '/usr/bin:/bin:/usr/sbin:/sbin'})
        require(result.returncode == 0 and len(result.stdout) <= 1024 * 1024, 'ssh_operation_failed_or_unknown')
        return result.stdout

    def preflight(self):
        rows = json.loads(self.command(self.docker + ['inspect', '--type=container', self.container]))
        require(type(rows) is list and len(rows) == 1, 'container_inspect_shape')
        row = rows[0]; host = row['HostConfig']; state = row['State']
        require(row['Id'] == self.container and row['Image'] == IMAGE and row['Name'] == '/rsc-bao-server'
                and row['Config']['User'] == '23101:23101' and state['Running'] is True
                and state['Status'] == 'running' and not state['OOMKilled'], 'container_identity_changed')
        require(host['NetworkMode'] == 'none' and host['ReadonlyRootfs'] is True and host['CapDrop'] == ['ALL']
                and not host.get('CapAdd') and host['Privileged'] is False
                and host['SecurityOpt'] in (['no-new-privileges=true'], ['no-new-privileges:true'])
                and host['LogConfig']['Type'] == 'none' and host['MemorySwap'] == host['Memory'] == 384 * 1024 ** 2
                and not host.get('PortBindings') and not host.get('Devices')
                and host.get('PidMode', '') in ('', 'private'), 'container_isolation_changed')
        require(host.get('GroupAdd') == ['23110'] and host.get('IpcMode') == 'private'
                and host.get('PidsLimit') == 128 and host.get('NanoCpus') == 500000000
                and host.get('RestartPolicy', {}).get('Name') == 'no'
                and type(state.get('Pid')) is int and state['Pid'] > 0
                and type(state.get('StartedAt')) is str and state['StartedAt'], 'container_process_boundary')
        require(row['Config'].get('Entrypoint') == ['/runtime/bao']
                and row['Config'].get('Cmd') == ['server', '-config=/runtime/server.json'], 'container_command_changed')
        environment = row['Config'].get('Env', [])
        require(type(environment) is list and all(type(value) is str and not value.split('=', 1)[0].startswith(
                ('BAO_', 'VAULT_', 'AWS_', 'ALIYUN_', 'ALIBABA_', 'ALIBABACLOUD_', 'OAM_')) for value in environment),
                'container_credential_environment')
        expected_mounts = {
            '/runtime/bao': (BINARY, False), '/runtime/server.json': (CONFIG + '/server.json', False),
            '/runtime/runtime_init_remote.py': (CONFIG + '/runtime_init_remote.py', False),
            '/run/rsc-bao': (RUN + '/socket', True), '/state': (STATE, True)}
        mounts = row.get('Mounts', [])
        require(host.get('Tmpfs') == {'/tmp': 'rw,noexec,nosuid,nodev,size=8388608,mode=1777'},
                'container_tmpfs_changed')
        require(type(mounts) is list and len(mounts) in (len(expected_mounts), len(expected_mounts) + 1),
                'container_mount_count')
        temporary = [value for value in mounts if value.get('Type') == 'tmpfs']
        require(not temporary or (len(temporary) == 1 and temporary[0].get('Destination') == '/tmp'
                and temporary[0].get('RW') is True and not temporary[0].get('Source')), 'container_extra_tmpfs')
        mounts = [value for value in mounts if value.get('Type') != 'tmpfs']
        require(len(mounts) == len(expected_mounts)
                and {value.get('Destination') for value in mounts} == set(expected_mounts), 'container_mount_targets')
        for value in mounts:
            source, writable = expected_mounts[value['Destination']]
            require(value.get('Type') == 'bind' and value.get('Source') == source
                    and value.get('RW') is writable and value.get('Propagation') == 'rprivate', 'container_mount_changed')
        expected = hashlib.sha256(Path(__file__).with_name('runtime_init_remote.py').read_bytes()).hexdigest()
        server_hash = hashlib.sha256(build(self.instance)['server.json']).hexdigest()
        output = self.command(self.docker + ['exec', self.container, 'sha256sum',
                              '/runtime/runtime_init_remote.py', '/runtime/bao', '/runtime/server.json'], timeout=30).decode('ascii').splitlines()
        require(output == [expected + '  /runtime/runtime_init_remote.py', BAO_SHA256 + '  /runtime/bao',
                           server_hash + '  /runtime/server.json'],
                'container_source_changed')
        return {'id': row['Id'], 'image': row['Image'], 'startedAt': state['StartedAt'], 'pid': state['Pid']}

    def request(self, operation, run_id, key=None):
        request = {'operation': operation, 'runId': run_id}
        if key is not None:
            request['pgpFingerprint'] = str(key.fingerprint)
        if operation == 'initialize':
            request['pgpPublicKey'] = base64.b64encode(bytes(key.pubkey)).decode('ascii')
        output = self.command(self.docker + ['exec', '-i', '--user=23101:23101', self.container,
                     'python3', '/runtime/runtime_init_remote.py'], json.dumps(request).encode() + b'\n', timeout=55)
        require(len(output) <= 65536, 'init_response_bound')
        result = json.loads(output)
        expected = 'read_only' if operation == 'status' else 'initialized_pending_custody'
        require(type(result) is dict and result.get('status') == expected,
                'init_request_failed_or_unknown')
        if operation == 'status':
            require(set(result) == {'status', 'initialized', 'sealed', 'attemptMarkerPresent', 'automaticReplayAllowed'}
                    and all(type(result[k]) is bool for k in ('initialized', 'sealed', 'attemptMarkerPresent'))
                    and result['automaticReplayAllowed'] is False, 'public_status_shape')
        return result


def initialize_or_recover(args, remote):
    pgpy = pgp_module(); memory_policy()
    identity = vault_identity(args.vault_mount, args.vault_binding)
    parent = os.open(args.vault_mount, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    directory = None
    name = 'initialization-' + args.run_id
    try:
        if args.action == 'initialize':
            current = remote.request('status', args.run_id)
            require(current.get('initialized') is False and current.get('attemptMarkerPresent') is False,
                    'init_already_attempted_use_status_or_recover')
            os.mkdir(name, 0o700, dir_fd=parent); os.fsync(parent)
        directory = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        info = os.fstat(directory)
        require(info.st_dev == identity['device'] and stat.S_IMODE(info.st_mode) == 0o700, 'custody_directory_changed')
        if args.action == 'initialize':
            key = create_custody_key(pgpy)
            new_file(directory, 'custody-private.asc', str(key).encode())
            key, _ = pgpy.PGPKey.from_blob(existing_file(directory, 'custody-private.asc'))
            require(not key.is_public and not key.is_protected, 'custody_private_key_readback')
            new_file(directory, 'public-binding.json', json.dumps({'runId': args.run_id,
                 'pgpFingerprint': str(key.fingerprint), 'containerId': args.container_id}).encode())
        else:
            key, _ = pgpy.PGPKey.from_blob(existing_file(directory, 'custody-private.asc'))
            binding = json.loads(existing_file(directory, 'public-binding.json'))
            require(binding == {'runId': args.run_id, 'pgpFingerprint': str(key.fingerprint),
                                'containerId': args.container_id}, 'recovery_binding_changed')
        require(vault_identity(args.vault_mount, args.vault_binding) == identity, 'custody_mount_changed_before_init')
        result = remote.request(args.action, args.run_id, key)
        require(result.get('runId') == args.run_id, 'init_run_binding')
        raw = json.dumps(result, sort_keys=True).encode()
        # Recovery may encounter a fully sealed existing response; never overwrite it.
        try:
            old = existing_file(directory, 'initialization-encrypted.json')
        except FileNotFoundError:
            new_file(directory, 'initialization-encrypted.json', raw)
        else:
            require(old == raw, 'existing_custody_ciphertext_changed')
        reread = json.loads(existing_file(directory, 'initialization-encrypted.json'))
        custody_readback(pgpy, key, reread)
        require(vault_identity(args.vault_mount, args.vault_binding) == identity, 'custody_mount_changed_after_seal')
        state = remote.request('status', args.run_id)
        require(state.get('initialized') is True and state.get('sealed') is True, 'expected_initialized_sealed_state')
        return {'status': 'pgp_wrapped_initialization_custody_verified', 'runId': args.run_id,
                'pgpFingerprint': str(key.fingerprint), 'privateKeyFsyncReadbackVerified': True,
                'ciphertextFsyncReadbackVerified': True, 'shareAndRootDecryptVerifiedInMemory': True,
                'initialized': True, 'sealed': True, 'unsealPerformed': False,
                'configurationPerformed': False, 'backupRestoreDrillPassed': False,
                'pythonHeapLocked': False, 'coreDumpsDisabled': True,
                'custodianCount': 1, 'mediaCount': 1, 'productionReady': False}
    finally:
        if directory is not None:
            os.close(directory)
        os.close(parent)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare-vault', 'status', 'initialize', 'recover'))
    parser.add_argument('--ssh-config', type=Path); parser.add_argument('--ssh-host')
    parser.add_argument('--ssh-sudo', action='store_true'); parser.add_argument('--container-id')
    parser.add_argument('--instance-id')
    parser.add_argument('--run-id'); parser.add_argument('--vault-mount', type=Path)
    parser.add_argument('--vault-binding', type=Path)
    args = parser.parse_args(); resource.setrlimit(resource.RLIMIT_CORE, (0, 0)); os.umask(0o077)
    try:
        if args.action == 'prepare-vault':
            require(args.vault_binding is not None, 'custody_binding_path_required')
            result = prepare_vault(args.vault_binding)
            print(json.dumps(result)); return 0
        require(args.ssh_config is not None and args.ssh_host and args.container_id and args.run_id,
                'exact_remote_coordinates_required')
        require(re.fullmatch(r'[0-9a-f]{12}', args.run_id), 'run_id_invalid')
        remote = Remote(args.ssh_config, args.ssh_host, args.container_id, args.ssh_sudo, args.instance_id)
        peer = remote.preflight()
        if args.action == 'status':
            result = remote.request('status', args.run_id)
        else:
            require(args.vault_mount is not None and args.vault_binding is not None, 'verified_rw_vault_required')
            result = initialize_or_recover(args, remote)
        require(remote.preflight() == peer, 'peer_changed_during_custody')
    except BaseException as error:
        result = {'status': 'failed_or_unknown', 'safeCode': str(error) if isinstance(error, Rejected)
                  else 'custody_or_transport_failed', 'automaticReplayAllowed': False,
                  'nextAction': 'exact_read_only_status_then_existing_ciphertext_recovery', 'productionReady': False}
    print(json.dumps(result, sort_keys=True))
    return 0 if result['status'] in ('read_only', 'pgp_wrapped_initialization_custody_verified') else 1


if __name__ == '__main__':
    raise SystemExit(main())
