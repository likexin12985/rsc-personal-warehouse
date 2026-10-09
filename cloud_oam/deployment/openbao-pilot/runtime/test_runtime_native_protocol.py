"""New mount/installation/peer protocol tests; no native command or real init."""
import copy
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import runtime_bundle as bundle
import runtime_init as custody
import runtime_install as install
import runtime_preflight as preflight


class NativeProtocolTests(unittest.TestCase):
    def test_closed_prepare_hashes_with_pinned_python39_without_file_digest(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve(); image = root / 'image'; body = b'public fixture' * 100000
            image.write_bytes(body); binding = root / 'receipt.json'
            responses = [{'VolumeUUID': custody.EXTERNAL_UUID, 'MountPoint': str(root),
                          'BusProtocol': 'USB', 'Internal': False, 'WritableVolume': True},
                         {'images': []}, {'encrypted': True}]
            with (patch.object(custody, 'EXTERNAL', root), patch.object(custody, 'IMAGE_PATH', image),
                    patch.object(custody.sys, 'platform', 'darwin'), patch.object(custody.os.path, 'ismount', return_value=True),
                    patch.object(custody, 'native', side_effect=responses),
                    patch.object(custody, 'image_stat', return_value={'inode': 1})
                    ):
                result = custody.prepare_vault(binding)
            self.assertEqual(result['status'], 'closed_image_binding_created')
            self.assertEqual(json.loads(binding.read_text())['closedImageSha256'], hashlib.sha256(body).hexdigest())
            self.assertEqual(stat.S_IMODE(binding.stat().st_mode), 0o600)

    def vault_case(self, change=None):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve(); outer = root / 'outer'; outer.mkdir()
            image = outer / 'recovery.dmg'; image.write_bytes(b'public synthetic fixture')
            mount = root / 'mount'; mount.mkdir()
            image_identity = {'device': 100, 'inode': 200, 'size': 24, 'birthtime': 1.0}
            binding = root / 'binding.json'
            binding.write_text(json.dumps({'schema': 'rsc.custody-pre-attachment.v1',
                'imagePath': str(image), 'outerVolumeUuid': custody.EXTERNAL_UUID,
                'imageVolumeUuid': custody.IMAGE_VOLUME_UUID, 'closedImageEncrypted': True,
                'imageStat': image_identity, 'closedImageSha256': 'a' * 64}))
            binding.chmod(0o600)
            outer_info = {'MountPoint': str(outer), 'VolumeUUID': custody.EXTERNAL_UUID,
                'BusProtocol': 'USB', 'Internal': False, 'WritableVolume': True}
            attached = {'images': [{'image-path': str(image), 'system-entities': [{'mount-point': str(mount)}]}]}
            volume = {'MountPoint': str(mount), 'VolumeUUID': custody.IMAGE_VOLUME_UUID,
                      'Writable': True, 'WritableVolume': True}
            if change:
                change(outer_info, attached, volume, image_identity)
            def native(argv):
                self.assertNotIn('isencrypted', argv)
                if argv[:2] == ['/usr/bin/hdiutil', 'info']:
                    return attached
                return outer_info if argv[-1] == str(outer) else volume
            with (patch.object(custody, 'EXTERNAL', outer), patch.object(custody, 'IMAGE_PATH', image),
                    patch.object(custody.sys, 'platform', 'darwin'), patch.object(custody.os.path, 'ismount', return_value=True),
                    patch.object(custody, 'image_stat', return_value=image_identity),
                    patch.object(custody, 'native', side_effect=native)):
                return custody.vault_identity(mount, binding)

    def test_mounted_protocol_uses_native_mapping_and_uuid_without_isencrypted(self):
        self.assertEqual(self.vault_case()['volumeUuid'], custody.IMAGE_VOLUME_UUID)

    def test_native_mapping_wrong_uuid_readonly_and_changed_image_refuse(self):
        changes = [lambda o, a, v, i: o.update(Internal=True),
                   lambda o, a, v, i: o.update(VolumeUUID='wrong'),
                   lambda o, a, v, i: v.update(WritableVolume=False),
                   lambda o, a, v, i: v.update(VolumeUUID='wrong'),
                   lambda o, a, v, i: a.update(images=[]),
                   lambda o, a, v, i: i.update(inode=999)]
        for change in changes:
            with self.subTest(change=changes.index(change)), self.assertRaises(custody.Rejected):
                self.vault_case(change)

    def test_closed_prepare_refuses_attached_image_before_any_encryption_probe(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve(); image = root / 'image'; image.write_bytes(b'public')
            responses = [{'VolumeUUID': custody.EXTERNAL_UUID, 'MountPoint': str(root),
                          'BusProtocol': 'USB', 'Internal': False, 'WritableVolume': True},
                         {'images': [{'image-path': str(image)}]}]
            with (patch.object(custody, 'EXTERNAL', root), patch.object(custody, 'IMAGE_PATH', image),
                    patch.object(custody.sys, 'platform', 'darwin'), patch.object(custody.os.path, 'ismount', return_value=True),
                    patch.object(custody, 'native', side_effect=responses) as native,
                    self.assertRaisesRegex(custody.Rejected, 'closed_image_required')):
                custody.prepare_vault(root / 'receipt')
            self.assertEqual(native.call_count, 2)
            self.assertFalse((root / 'receipt').exists())

    def test_public_install_exclusive_mode_readback_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / 'public.json'
            install.copy_new(target, b'{"public":true}', 0o444)
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o444)
            self.assertEqual(target.read_bytes(), b'{"public":true}')
            with self.assertRaises(FileExistsError):
                install.copy_new(target, b'changed', 0o444)
            self.assertEqual(target.read_bytes(), b'{"public":true}')

    def test_preflight_exact_root_gid_mode_and_hash(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / 'public'; target.write_bytes(b'public'); target.chmod(0o444)
            original = os.fstat
            identity = {'uid': 0, 'gid': 0, 'mode': 0o444}
            def metadata(fd):
                info = original(fd)
                return SimpleNamespace(st_mode=stat.S_IFREG | identity['mode'], st_uid=identity['uid'],
                    st_gid=identity['gid'], st_nlink=info.st_nlink, st_size=info.st_size,
                    st_mtime_ns=info.st_mtime_ns, st_ctime_ns=info.st_ctime_ns)
            with patch.object(preflight.os, 'fstat', side_effect=metadata):
                self.assertEqual(preflight.public_file(target), hashlib.sha256(b'public').hexdigest())
                for field, value in [('uid', 1), ('gid', 1), ('mode', 0o600), ('mode', 0o555)]:
                    previous = identity[field]; identity[field] = value
                    with self.subTest(field=field, value=value), self.assertRaises(preflight.Rejected):
                        preflight.public_file(target)
                    identity[field] = previous
                with self.assertRaises(preflight.Rejected):
                    preflight.public_file(target, 'a' * 64)

    def peer_case(self, change=None):
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary).resolve() / 'ssh'; config.write_text('# public synthetic')
            client = custody.Remote(config, 'synthetic', 'a' * 64, instance='rsc-pilot-runtime-01')
            row = {'Id': 'a' * 64, 'Image': bundle.IMAGE, 'Name': '/rsc-bao-server',
                'Config': {'User': '23101:23101', 'Env': ['GOMAXPROCS=2'], 'Entrypoint': ['/runtime/bao'],
                           'Cmd': ['server', '-config=/runtime/server.json']},
                'State': {'Running': True, 'Status': 'running', 'OOMKilled': False, 'Pid': 999, 'StartedAt': 'fixed'},
                'HostConfig': {'NetworkMode': 'none', 'ReadonlyRootfs': True, 'CapDrop': ['ALL'],
                    'CapAdd': [], 'Privileged': False, 'SecurityOpt': ['no-new-privileges:true'],
                    'LogConfig': {'Type': 'none'}, 'Memory': 384 * 1024**2, 'MemorySwap': 384 * 1024**2,
                    'PidMode': '', 'GroupAdd': ['23110'], 'IpcMode': 'private', 'PidsLimit': 128,
                    'NanoCpus': 500000000, 'RestartPolicy': {'Name': 'no'},
                    'Tmpfs': {'/tmp': 'rw,noexec,nosuid,nodev,size=8388608,mode=1777'}}, 'Mounts': []}
            for source, dest, rw in [(bundle.BINARY, '/runtime/bao', False),
                    (bundle.CONFIG+'/server.json', '/runtime/server.json', False),
                    (bundle.CONFIG+'/runtime_init_remote.py', '/runtime/runtime_init_remote.py', False),
                    (bundle.RUN+'/socket', '/run/rsc-bao', True), (bundle.STATE, '/state', True)]:
                row['Mounts'].append({'Type': 'bind', 'Source': source, 'Destination': dest,
                                      'RW': rw, 'Propagation': 'rprivate'})
            if change:
                change(row)
            hashes = (hashlib.sha256(Path(custody.__file__).with_name('runtime_init_remote.py').read_bytes()).hexdigest()
                + '  /runtime/runtime_init_remote.py\n' + bundle.BAO_SHA256 + '  /runtime/bao\n'
                + hashlib.sha256(bundle.build(client.instance)['server.json']).hexdigest() + '  /runtime/server.json\n')
            with patch.object(client, 'command', side_effect=[json.dumps([row]).encode(), hashes.encode()]):
                return client.preflight()

    def test_peer_exact_identity_mounts_resource_and_three_sources(self):
        self.assertEqual(self.peer_case()['pid'], 999)
        def tmpfs_row(row):
            row['Mounts'].append({'Type': 'tmpfs', 'Destination': '/tmp', 'Source': '', 'RW': True})
        self.assertEqual(self.peer_case(tmpfs_row)['id'], 'a' * 64)

    def test_peer_rejects_wrong_source_writable_config_extra_mount_or_command(self):
        changes = [lambda r: r['Mounts'][0].update(Source='/untrusted/bao'),
                   lambda r: r['Mounts'][1].update(RW=True),
                   lambda r: r['Mounts'].append(copy.deepcopy(r['Mounts'][0])),
                   lambda r: r['Config'].update(Cmd=['agent']),
                   lambda r: r['Config'].update(Env=['VAULT_TOKEN=synthetic']),
                   lambda r: r['State'].update(Pid=0),
                   lambda r: r['HostConfig'].update(MemorySwap=0),
                   lambda r: r['HostConfig'].update(GroupAdd=['0']),
                   lambda r: r['HostConfig'].update(Tmpfs={'/tmp': 'rw,size=8388608'}),
                   lambda r: r['HostConfig'].update(SecurityOpt=[])]
        for change in changes:
            with self.subTest(change=changes.index(change)), self.assertRaises(custody.Rejected):
                self.peer_case(change)


if __name__ == '__main__':
    unittest.main()
