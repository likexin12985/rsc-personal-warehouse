"""New init/custody mocks plus one synthetic in-memory PGP roundtrip; no init."""
import base64
import json
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch, Mock
import warnings

import runtime_init as local
import runtime_init_remote as remote


def fake_ciphertext():
    packet = b'\xc1' + b'x' * 300
    encoded = base64.b64encode(packet).decode()
    return {'keys': [packet.hex()], 'keys_base64': [encoded], 'root_token': encoded}


class InitTests(unittest.TestCase):
    def test_public_status_never_initializes(self):
        with (patch.object(remote, 'rpc', side_effect=[{'initialized': False}, {'sealed': True, 'version': '2.7.1'}]) as call,
                patch.object(remote, 'marker_exists', return_value=False)):
            result = remote.handle({'operation': 'status', 'runId': '123456abcdef'})
        self.assertFalse(result['initialized'])
        self.assertTrue(result['sealed'])
        self.assertTrue(all(args.args[0] == 'GET' for args in call.call_args_list))
        self.assertNotIn('materials', result)

    def test_existing_marker_or_initialized_never_replays(self):
        for initialized, marker in [(True, False), (False, True), (True, True)]:
            with (self.subTest(initialized=initialized, marker=marker),
                    patch.object(remote, 'rpc', return_value={'initialized': initialized}) as call,
                    patch.object(remote, 'marker_exists', return_value=marker),
                    patch.object(remote, 'public_fingerprint', return_value='A' * 40),
                    self.assertRaises(remote.Rejected)):
                remote.handle({'operation': 'initialize', 'runId': '123456abcdef', 'pgpFingerprint': 'A' * 40,
                               'pgpPublicKey': base64.b64encode(b'x' * 300).decode()})
            self.assertEqual(call.call_count, 1)

    def test_pgp_share_and_root_use_same_bound_public_key(self):
        public = base64.b64encode(b'x' * 300).decode()
        calls = []
        def rpc(method, path, body=None):
            calls.append((method, path, body)); return {'initialized': False} if method == 'GET' else fake_ciphertext()
        with (patch.object(remote, 'rpc', side_effect=rpc), patch.object(remote, 'marker_exists', return_value=False),
                patch.object(remote, 'public_fingerprint', return_value='A' * 40),
                patch.object(remote, 'reserve_attempt') as reserve, patch.object(remote, 'save_ciphertext') as save):
            remote.handle({'operation': 'initialize', 'runId': '123456abcdef',
                           'pgpFingerprint': 'A' * 40, 'pgpPublicKey': public})
        self.assertEqual(calls[-1], ('PUT', '/v1/sys/init', {'secret_shares': 1, 'secret_threshold': 1,
                                      'pgp_keys': [public], 'root_token_pgp_key': public}))
        reserve.assert_called_once_with('123456abcdef', 'A' * 40)
        save.assert_called_once()

    def test_init_transport_error_keeps_marker_and_does_not_retry(self):
        with (patch.object(remote, 'rpc', side_effect=[{'initialized': False}, TimeoutError('synthetic')]) as call,
                patch.object(remote, 'marker_exists', return_value=False),
                patch.object(remote, 'public_fingerprint', return_value='A' * 40),
                patch.object(remote, 'reserve_attempt') as reserve, patch.object(remote, 'save_ciphertext') as save,
                self.assertRaises(TimeoutError)):
            remote.handle({'operation': 'initialize', 'runId': '123456abcdef', 'pgpFingerprint': 'A' * 40,
                           'pgpPublicKey': base64.b64encode(b'x' * 300).decode()})
        self.assertEqual(call.call_count, 2); reserve.assert_called_once(); save.assert_not_called()

    def test_plaintext_or_mismatched_encodings_never_saved_as_ciphertext(self):
        values = [dict(fake_ciphertext(), root_token='hvs.ordinary_token'),
                  dict(fake_ciphertext(), keys=['ab' * 32]), {'root_token': 'secret'}]
        for value in values:
            with self.subTest(value_type=tuple(value)), self.assertRaises((remote.Rejected, ValueError)):
                remote.encrypted_materials(value)

    def test_ciphertext_recovery_only_reads_initialized_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / 'ciphertext.json'
            value = {'status': 'initialized_pending_custody', 'runId': '123456abcdef',
                     'pgpFingerprint': 'A' * 40, 'materials': fake_ciphertext()}
            target.write_text(json.dumps(value)); target.chmod(0o600)
            original = os.fstat
            def stat_uid(fd):
                result = original(fd)
                return SimpleNamespace(st_mode=result.st_mode, st_uid=23101, st_nlink=result.st_nlink, st_size=result.st_size)
            with (patch.object(remote, 'CIPHERTEXT', str(target)), patch.object(remote, 'marker_exists', return_value=True),
                    patch.object(remote, 'rpc', return_value={'initialized': True}) as call,
                    patch.object(remote.os, 'fstat', side_effect=stat_uid)):
                found = remote.handle({'operation': 'recover', 'runId': '123456abcdef', 'pgpFingerprint': 'A' * 40})
            self.assertEqual(found, value); self.assertEqual(call.call_count, 1)
            self.assertEqual(call.call_args.args, ('GET', '/v1/sys/init'))

    def test_custody_file_exclusive_fsync_and_exact_readback(self):
        with tempfile.TemporaryDirectory() as temporary:
            fd = os.open(temporary, os.O_RDONLY | os.O_DIRECTORY)
            try:
                local.new_file(fd, 'synthetic', b'not-a-secret')
                self.assertEqual(local.existing_file(fd, 'synthetic'), b'not-a-secret')
                with self.assertRaises(FileExistsError):
                    local.new_file(fd, 'synthetic', b'changed')
                os.symlink('synthetic', Path(temporary) / 'link')
                with self.assertRaises(OSError):
                    local.existing_file(fd, 'link')
            finally:
                os.close(fd)

    def test_memory_policy_disables_core_without_claiming_locked_python_heap(self):
        with patch.object(local.resource, 'setrlimit') as limit:
            result = local.memory_policy()
        limit.assert_called_once_with(local.resource.RLIMIT_CORE, (0, 0))
        self.assertFalse(result['pythonHeapLocked'])

    def test_core_policy_precedes_any_vault_or_key_operation(self):
        with (patch.object(local, 'pgp_module'), patch.object(local, 'memory_policy', side_effect=local.Rejected('core')),
                patch.object(local, 'vault_identity') as vault, patch.object(local, 'create_custody_key') as key,
                self.assertRaises(local.Rejected)):
            local.initialize_or_recover(SimpleNamespace(), Mock())
        vault.assert_not_called(); key.assert_not_called()

    def test_status_shape_rejects_materials_or_unexpected_initialize_result(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / 'ssh'; config.write_text('# synthetic')
            client = local.Remote(config, 'synthetic-host', 'a' * 64)
            for result in [{'status': 'initialized_pending_custody', 'materials': fake_ciphertext()},
                           {'status': 'read_only', 'materials': fake_ciphertext()}]:
                with (patch.object(client, 'command', return_value=json.dumps(result).encode()),
                        self.assertRaises(local.Rejected)):
                    client.request('status', '123456abcdef')

    def test_ssh_arguments_require_exact_container_and_safe_alias(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / 'ssh'; config.write_text('# synthetic')
            for alias, identity in [('-oProxyCommand=bad', 'a' * 64), ('host;id', 'a' * 64), ('host', 'rsc-bao-server')]:
                with self.subTest(alias=alias), self.assertRaises(local.Rejected):
                    local.Remote(config, alias, identity)

    def test_single_synthetic_pgp_roundtrip_and_public_fingerprint(self):
        pgpy = local.pgp_module()
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            key = local.create_custody_key(pgpy)
            public = bytes(key.pubkey)
            self.assertEqual(remote.public_fingerprint(public), str(key.fingerprint))
            share = bytes(key.pubkey.encrypt(pgpy.PGPMessage.new('ab' * 32)))
            root = bytes(key.pubkey.encrypt(pgpy.PGPMessage.new('synthetic-root-no-credentials')))
            result = {'status': 'initialized_pending_custody', 'pgpFingerprint': str(key.fingerprint),
                      'materials': {'keys': [share.hex()], 'keys_base64': [base64.b64encode(share).decode()],
                                    'root_token': base64.b64encode(root).decode()}}
            self.assertTrue(local.custody_readback(pgpy, key, result))
        self.assertNotIn('synthetic-root-no-credentials', json.dumps(result))


if __name__ == '__main__':
    unittest.main()
