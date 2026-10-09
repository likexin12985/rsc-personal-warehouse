"""Create/inspect one EMPTY encrypted image on the explicitly bound USB disk.

Password prompting is delegated to hdiutil -agentpass; a GUI is not guaranteed.
This program has no
password input, clipboard, environment secret, stdinpass or AppleScript path.
It never generates/copies recovery keys, reformats a disk, ejects a volume,
overwrites an existing vault, retries an interrupted create or deletes files.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import resource
import stat
import subprocess
import sys
import time
import uuid

BASE = Path('/Volumes/Seagate Backup Plus Drive')
VOLUME_UUID = '4FBD5E6D-037F-4DE4-BE21-75A186DB7CDC'


class Rejected(RuntimeError):
    pass


def require(value, code):
    if not value:
        raise Rejected(code)


def command(argv, *, timeout=20):
    # No caller can supply a password, and stdin remains disconnected.
    result = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, timeout=timeout,
                            env={'PATH': '/usr/bin:/bin:/usr/sbin:/sbin'})
    require(result.returncode == 0, 'native_command_failed_or_cancelled')
    require(len(result.stdout) <= 2 * 1024 * 1024, 'metadata_size_bound')
    return result.stdout


def preflight():
    require(sys.platform == 'darwin', 'macos_required')
    require(BASE.is_dir() and not BASE.is_symlink() and BASE.resolve(strict=True) == BASE,
            'external_mount_missing_or_symlink')
    require(os.path.ismount(BASE), 'external_mount_not_mounted')
    value = plistlib.loads(command(['/usr/sbin/diskutil', 'info', '-plist', str(BASE)]))
    require(value.get('MountPoint') == str(BASE) and value.get('VolumeUUID') == VOLUME_UUID
            and value.get('DiskUUID') == VOLUME_UUID and value.get('BusProtocol') == 'USB'
            and value.get('Internal') is False and value.get('Writable') is True
            and value.get('WritableVolume') is True and value.get('FilesystemType') == 'apfs',
            'external_volume_identity_mismatch')
    require(os.access(BASE, os.W_OK | os.X_OK), 'external_volume_not_writable')
    require(value.get('APFSContainerFree', 0) >= 128 * 1024 * 1024, 'external_volume_space')
    return {'mountPoint': str(BASE), 'volumeUuid': VOLUME_UUID,
            'deviceIdentifier': value['DeviceIdentifier'], 'filesystem': 'apfs',
            'externalUsb': True, 'writable': True,
            'wholeVolumeEncrypted': value.get('FileVault') is True or value.get('Encryption') is True,
            'globalPermissionsEnabled': value.get('GlobalPermissionsEnabled') is True,
            'deviceNumber': BASE.stat().st_dev}


def targets(vault_id):
    require(re.fullmatch(r'[0-9a-f]{12}', vault_id) is not None, 'vault_id_shape')
    directory = BASE / ('RSC-recovery-' + vault_id)
    return directory, directory / 'RSC-recovery.dmg', directory / 'receipt.json'


def verify_directory(directory, device):
    require(directory.is_dir() and not directory.is_symlink()
            and directory.resolve(strict=True).parent == BASE and directory.stat().st_dev == device,
            'vault_directory_identity')


def save_receipt(path, value, *, initial=False):
    if initial:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, 'w') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        return
    require(path.is_file() and not path.is_symlink(), 'receipt_identity')
    temporary = path.with_name('receipt-' + uuid.uuid4().hex + '.tmp')
    with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), 'w') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def mounted_image(image):
    info = plistlib.loads(command(['/usr/bin/hdiutil', 'info', '-plist']))
    matches = [item for item in info.get('images', []) if item.get('image-path') == str(image)]
    require(len(matches) <= 1, 'image_attachment_ambiguous')
    if not matches:
        return {'attached': False, 'mountPoints': [], 'deviceEntries': []}
    entities = matches[0].get('system-entities', [])
    return {'attached': True,
            'mountPoints': [item['mount-point'] for item in entities if item.get('mount-point')],
            'deviceEntries': [item['dev-entry'] for item in entities if item.get('dev-entry')]}


def inspect_image(image, device):
    if not image.exists():
        return {'imageExists': False, 'status': 'not_created_or_incomplete'}
    require(not image.is_symlink(), 'image_symlink')
    value = image.stat()
    require(stat.S_ISREG(value.st_mode) and value.st_nlink == 1 and value.st_dev == device
            and 0 < value.st_size < 128 * 1024 * 1024, 'image_file_identity')
    attached = mounted_image(image)
    encrypted = plistlib.loads(command(['/usr/bin/hdiutil', 'isencrypted', str(image), '-plist']))
    require(encrypted.get('encrypted') is True, 'encrypted_header_not_confirmed')
    output = {'imageExists': True, 'encryptedHeaderVerified': True, 'imageSizeBytes': value.st_size,
              'attachment': attached, 'status': 'encrypted_attached' if attached['attached'] else 'encrypted_detached'}
    if not attached['attached']:
        # A digest of the closed image is stable. Do not hash mounted contents.
        digest = hashlib.sha256()
        with image.open('rb') as stream:
            for part in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(part)
        after = image.stat()
        require((value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns) ==
                (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns), 'image_changed_during_hash')
        output['imageSha256'] = digest.hexdigest()
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('preflight', 'status', 'create'))
    parser.add_argument('--vault-id', help='Explicit new 12 lowercase hex ID; interrupted creation is inspected, never replayed.')
    args = parser.parse_args()
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    result = {'schema': 'rsc.recovery-vault.v1', 'productionReady': False,
              'recoveryMaterialsGenerated': False, 'recoveryMaterialsCopied': False,
              'passwordHandledBy': 'macOS_hdiutil_agentpass_only',
              'custodianCount': 1, 'independentReviewerCount': 0, 'plannedMediaCount': 1,
              'secondBackupVerified': False}
    receipt = None
    owned_receipt = False
    try:
        identity = preflight()
        result['externalVolume'] = identity
        if args.action == 'preflight':
            result['status'] = 'preflight_passed_no_changes'
        else:
            directory, image, receipt = targets(args.vault_id or '')
            result.update(vaultId=args.vault_id, directory=str(directory), imagePath=str(image))
            if args.action == 'status':
                if not directory.exists():
                    result['status'] = 'vault_directory_absent_no_changes'
                    receipt = None
                else:
                    verify_directory(directory, identity['deviceNumber'])
                    if receipt.exists():
                        require(not receipt.is_symlink() and receipt.stat().st_size <= 16384, 'receipt_shape')
                        previous = json.loads(receipt.read_text())
                        require(previous.get('vaultId') == args.vault_id
                                and previous.get('imagePath') == str(image), 'receipt_binding')
                        result['previousStage'] = previous.get('status')
                    result.update(inspect_image(image, identity['deviceNumber']))
                    receipt = None  # status never writes a receipt or retries creation
            else:
                require(not directory.exists() and not directory.is_symlink(), 'existing_vault_must_not_be_recreated')
                # Never create /Volumes or the external mount as a fallback.
                descriptor = os.open(BASE, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                try:
                    require(os.fstat(descriptor).st_dev == identity['deviceNumber'], 'external_mount_changed')
                    os.mkdir(directory.name, mode=0o700, dir_fd=descriptor)
                finally:
                    os.close(descriptor)
                verify_directory(directory, identity['deviceNumber'])
                require(not image.exists(), 'existing_image_must_not_be_overwritten')
                result['status'] = 'awaiting_native_password'
                result['encryptionRequested'] = 'AES-256'
                result['emptyImageSizeMiB'] = 64
                result['filesystemInsideImage'] = 'Journaled HFS+'
                save_receipt(receipt, result, initial=True)
                owned_receipt = True
                # Delegate prompting to hdiutil; this does not guarantee a GUI. No
                # -stdinpass, -passphrase, shell, script output, keychain export.
                command(['/usr/bin/hdiutil', 'create', '-size', '64m', '-type', 'UDIF',
                         '-fs', 'HFS+J', '-volname', 'RSC Recovery ' + args.vault_id,
                         '-encryption', 'AES-256', '-agentpass', '-nospotlight', '-plist', str(image)],
                        timeout=600)
                after = preflight()
                require(after['deviceNumber'] == identity['deviceNumber'], 'external_mount_changed')
                verify_directory(directory, identity['deviceNumber'])
                result.update(inspect_image(image, identity['deviceNumber']))
                require(result.get('encryptedHeaderVerified') is True
                        and result['attachment']['attached'] is False and 'imageSha256' in result,
                        'created_image_not_closed_and_verified')
                result['status'] = 'empty_encrypted_container_created_and_verified'
                result['recoveryMaterialsPresent'] = False
                save_receipt(receipt, result)
        result['result'] = 'passed'
    except BaseException as error:
        result.update(result='failed', status='unknown_or_incomplete_do_not_recreate',
                      safeCode=str(error) if isinstance(error, Rejected) else 'native_operation_interrupted_or_failed')
        if owned_receipt and receipt is not None and receipt.is_file() and not receipt.is_symlink():
            try:
                # Leave every image/file intact for exact read-only inspection.
                current = preflight()
                require(current['deviceNumber'] == identity['deviceNumber'], 'external_mount_changed')
                verify_directory(receipt.parent, identity['deviceNumber'])
                save_receipt(receipt, result)
            except Exception:
                result['receiptUpdateFailed'] = True
    result['observedAtUnixSeconds'] = int(time.time())
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result['result'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
