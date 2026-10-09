"""New first-key/wrapped-custody tests, never production key generation."""
import base64
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import runtime_transit as local
import runtime_transit_contract as contract
import runtime_transit_remote as remote

COORDINATE = {'purpose': 'authentication_idempotency', 'environment': 'production',
              'provider_instance_id': 'rsc-public-test', 'application_key_version': 17}
RESPONSE = {'ciphertext': 'vault:v1:' + base64.b64encode(b'x'*60).decode(), 'key_version': 1}
ROOT = 'synthetic-root-no-real-secrets'
REQUEST = {'operation': 'generate', 'attemptId': '0123456789ab', 'instanceId': 'rsc-public-test',
           'rootToken': ROOT, 'apply': True, 'contract': contract.derive({'coordinate': COORDINATE})}


def key(name):
    return {**remote.KEY_CREATE, 'name': name, 'deletion_allowed': False, 'latest_version': 1,
        'min_available_version': 0, 'min_decryption_version': 1, 'min_encryption_version': 0,
        'supports_encryption': True, 'supports_decryption': True, 'supports_signing': False,
        'supports_derivation': True, 'imported_key': False, 'soft_deleted': False,
        'kdf': 'hkdf_sha256', 'keys': {'1': 1790000000}}


class Fixture:
    def __init__(self):
        self.records = {}; self.calls = []; self.unknown = False
        self.keys = {name: key(name) for name in remote.KEYS.values()}
    def record(self, path, value):
        if path.name in self.records: raise FileExistsError()
        self.records[path.name] = copy.deepcopy(value)
    def rpc(self, method, path, token, body=None):
        self.calls.append((method, path, copy.deepcopy(body)))
        if path.endswith('/seal-status'): return {'initialized': True, 'sealed': False, 'version': '2.7.1', 'type': 'shamir'}
        if path.endswith('/lookup-self'): return {'id': ROOT, 'policies': ['root']}
        if path.endswith('/sys/mounts'): return {'transit/': {'type': 'transit', 'local': False}}
        if '/transit/keys/' in path:
            name = path.rsplit('/', 1)[1]
            if method == 'POST': self.keys[name] = key(name)
            return self.keys.get(name)
        if '/datakey/wrapped/' in path:
            assert any(name.endswith('-attempt.json') for name in self.records)
            if self.unknown: raise TimeoutError('synthetic timeout')
            return copy.deepcopy(RESPONSE)
        raise AssertionError('unexpected endpoint')
    def invoke(self, value=REQUEST):
        with patch.object(remote, 'rpc', side_effect=self.rpc), patch.object(remote, 'record', side_effect=self.record), \
             patch.object(remote, 'read_record', side_effect=lambda path: self.records.get(path.name)):
            return remote.handle(value)


class TransitTests(unittest.TestCase):
    def test_real_production_coordinate_and_registry_parser_are_reused(self):
        value = contract.derive({'coordinate': COORDINATE, 'response': RESPONSE})
        self.assertEqual(value['request']['key_version'], 1)
        context = json.loads(base64.b64decode(value['request']['context']))
        aad = json.loads(base64.b64decode(value['request']['associated_data']))
        self.assertEqual(context['application_key_version'], 17)
        self.assertNotEqual(context['schema'], aad['schema'])
        self.assertFalse(value['pinProposal']['independentDatabasePinVerified'])
        self.assertEqual(value['pinProposal']['origin'], 'original_generation_response')
        self.assertEqual(value['registryEntry']['ciphertext'], RESPONSE['ciphertext'])

    def test_nonexplicit_versions_wrong_provider_and_plaintext_response_rejected(self):
        for bad in (None, True, 0, -1, '17'):
            with self.subTest(version=bad), self.assertRaises(Exception):
                contract.derive({'coordinate': {**COORDINATE, 'application_key_version': bad}})
        for response in ({**RESPONSE, 'plaintext': 'forbidden'}, {**RESPONSE, 'key_version': 0},
                         {**RESPONSE, 'key_version': True}, {**RESPONSE, 'ciphertext': 'vault:v1:bad'}):
            with self.subTest(response_shape=list(response)), self.assertRaises(Exception):
                contract.derive({'coordinate': COORDINATE, 'response': response})
        with self.assertRaises(Exception): contract.derive({'coordinate': {**COORDINATE, 'provider': 'aliyun_kms'}})

    def test_one_wrapped_generation_marker_and_ciphertext_fsync_before_delivery(self):
        fixture = Fixture(); value = fixture.invoke()
        self.assertEqual(value['status'], 'wrapped_pending_custody')
        self.assertEqual(len([call for call in fixture.calls if '/datakey/' in call[1]]), 1)
        self.assertEqual(len(fixture.records), 2); self.assertNotIn(ROOT, json.dumps(fixture.records))
        with self.assertRaises(remote.transport.Rejected): fixture.invoke()
        self.assertEqual(len([call for call in fixture.calls if '/datakey/' in call[1]]), 1)

    def test_unknown_does_not_replay_and_recovery_reads_only_saved_original(self):
        fixture = Fixture(); fixture.unknown = True
        with self.assertRaises(TimeoutError): fixture.invoke()
        read = fixture.invoke({**REQUEST, 'operation': 'status', 'apply': False})
        self.assertTrue(read['attemptPresent']); self.assertFalse(read['wrappedResponsePresent'])
        before = list(fixture.calls)
        with self.assertRaises(remote.transport.Rejected): fixture.invoke({**REQUEST, 'operation': 'recover', 'apply': False})
        self.assertEqual(before, fixture.calls)
        with self.assertRaises(remote.transport.Rejected): fixture.invoke()
        self.assertEqual(len([call for call in fixture.calls if '/datakey/' in call[1]]), 1)

    def test_saved_recovery_same_binding_has_no_network_mutation(self):
        fixture = Fixture(); original = fixture.invoke(); calls = list(fixture.calls)
        self.assertEqual(fixture.invoke({**REQUEST, 'operation': 'recover', 'apply': False}), original)
        self.assertEqual(fixture.calls, calls)
        with self.assertRaises(remote.transport.Rejected):
            fixture.invoke({**REQUEST, 'operation': 'recover', 'apply': False, 'attemptId': 'fedcba987654'})

    def test_keys_default_readonly_missing_and_drift_hard_block(self):
        fixture = Fixture(); fixture.keys = {}
        with self.assertRaises(remote.transport.Rejected): fixture.invoke({**REQUEST, 'operation': 'keys', 'apply': False})
        self.assertFalse(any(call[0] == 'POST' for call in fixture.calls))
        for flaw, bad in [('latest_version', 2), ('exportable', True), ('deletion_allowed', True),
                          ('derived', False), ('auto_rotate_period', 3600), ('backup_info', {'version': 1})]:
            with self.subTest(flaw=flaw):
                fixture = Fixture(); fixture.keys[remote.KEYS[COORDINATE['purpose']]][flaw] = bad
                with self.assertRaises(remote.transport.Rejected): fixture.invoke()
                self.assertEqual(fixture.records, {})

    def test_new_key_create_is_bounded_and_verified_before_return(self):
        fixture = Fixture(); fixture.keys = {}
        value = fixture.invoke({**REQUEST, 'operation': 'keys', 'apply': True})
        self.assertEqual(value['status'], 'keys_verified')
        self.assertEqual(len(fixture.records), 2)
        self.assertEqual([call[2] for call in fixture.calls if call[0] == 'POST'], [remote.KEY_CREATE]*2)

    def test_wrong_context_and_aad_cannot_cross_application_version(self):
        changed = copy.deepcopy(REQUEST)
        changed['contract']['coordinate']['application_key_version'] = 18
        with self.assertRaises(remote.transport.Rejected): Fixture().invoke(changed)
        changed = copy.deepcopy(REQUEST); changed['contract']['request']['context'] = changed['contract']['request']['associated_data']
        with self.assertRaises(remote.transport.Rejected): Fixture().invoke(changed)

    def test_allowlist_denies_plaintext_export_backup_rotate_delete_and_other_key(self):
        for method, path in [('POST', '/v1/transit/datakey/plaintext/rsc-authentication-idempotency'),
            ('GET', '/v1/transit/export/encryption-key/rsc-authentication-idempotency'),
            ('GET', '/v1/transit/backup/rsc-authentication-idempotency'),
            ('POST', '/v1/transit/keys/rsc-authentication-idempotency/rotate'),
            ('DELETE', '/v1/transit/keys/rsc-authentication-idempotency'), ('POST', '/v1/transit/keys/other')]:
            self.assertFalse(remote.allowed(method, path))

    def test_actual_py39_custody_bridge_to_existing_py312_has_only_public_input(self):
        script = "import json,sys; from pathlib import Path; import runtime_transit as c; print(json.dumps(c.contract_bridge(Path(sys.argv[1]),json.loads(sys.stdin.read()))))"
        result = subprocess.run(['/tmp/rsc-openbao-verifier-20261008/bin/python', '-B', '-c', script, sys.executable],
            input=json.dumps({'coordinate': COORDINATE}).encode(), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=Path(__file__).parent, env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'}, timeout=20)
        self.assertEqual(result.returncode, 0); self.assertEqual(result.stderr, b'')
        self.assertEqual(json.loads(result.stdout), REQUEST['contract'])

    def test_custody_exclusive_readback_and_existing_bytes_must_match(self):
        with tempfile.TemporaryDirectory() as temporary:
            descriptor = os.open(temporary, os.O_RDONLY | os.O_DIRECTORY)
            try:
                local.store_or_verify(descriptor, 'original-wrapped-response.json', RESPONSE)
                local.store_or_verify(descriptor, 'original-wrapped-response.json', RESPONSE)
                with self.assertRaises(local.Rejected): local.store_or_verify(descriptor, 'original-wrapped-response.json', {})
                info = os.stat(Path(temporary)/'original-wrapped-response.json')
                self.assertEqual(info.st_mode & 0o777, 0o600); self.assertEqual(info.st_nlink, 1)
            finally: os.close(descriptor)

    def test_local_original_then_pin_then_registry_and_public_receipt_has_no_ciphertext(self):
        args = SimpleNamespace(purpose=COORDINATE['purpose'], application_key_version=17, instance_id='rsc-public-test',
            attempt_id='0123456789ab', operation='generate', apply=True, contract_python=Path(sys.executable),
            container_id='a'*64, run_id='111111111111', vault_mount=Path('/unused'), vault_binding=Path('/unused'))
        value = Fixture().invoke(); records = []
        client = SimpleNamespace(docker=['docker'], preflight=lambda: {'stable': True},
            command=lambda argv, body, timeout: json.dumps(value).encode())
        with tempfile.TemporaryDirectory() as temporary:
            descriptor = os.open(temporary, os.O_RDONLY | os.O_DIRECTORY)
            with patch.object(local, 'read_root', return_value=(ROOT, {'stable': True})), \
                 patch.object(local, 'open_custody', return_value=descriptor), \
                 patch.object(local.custody, 'vault_identity', return_value={'stable': True}), \
                 patch.object(local, 'store_or_verify', side_effect=lambda fd, name, body: records.append(name)):
                public = local.execute(args, client)
        self.assertEqual(records, ['generation-intent.json', 'original-wrapped-response.json', 'pin-proposal.json', 'registry-entry.json'])
        self.assertNotIn(ROOT, json.dumps(public)); self.assertNotIn(RESPONSE['ciphertext'], json.dumps(public))
        self.assertFalse(public['independentDatabasePinVerified']); self.assertFalse(public['registryInstalled'])


if __name__ == '__main__':
    unittest.main()
