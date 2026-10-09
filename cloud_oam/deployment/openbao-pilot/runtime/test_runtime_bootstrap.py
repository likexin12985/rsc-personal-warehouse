"""Focused private-delivery boundaries; mocks never claim real Linux bootstrap."""
import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import runtime_bootstrap as local
import runtime_bootstrap_remote as remote

ROLE = '11111111-1111-1111-1111-111111111111'
SECRET = '22222222-2222-2222-2222-222222222222'
ACCESSOR = '33333333-3333-3333-3333-333333333333'
ENTITY = '44444444-4444-4444-4444-444444444444'
ROOT = 'synthetic-root-never-real'
REQUEST = {'operation': 'issue', 'purpose': 'oss', 'attemptId': '0123456789ab',
           'instanceId': 'rsc-public-test', 'rootToken': ROOT}


def role():
    return {'bind_secret_id': True, 'secret_id_num_uses': 1, 'secret_id_ttl': 600, 'token_type': 'service',
        'token_period': 1200, 'token_ttl': 1200, 'token_max_ttl': 0, 'token_explicit_max_ttl': 0,
        'token_num_uses': 0, 'token_no_default_policy': True, 'token_policies': ['rsc-oss'], 'local_secret_ids': False}


class Fixture:
    def __init__(self):
        self.records = {}; self.calls = []; self.fail_after_create = False; self.absent = False
        self.policy = remote.expected_policy('oss'); self.role = role()
        self.entity = {'id': ENTITY, 'metadata': {'rsc_instance': 'rsc-public-test', 'purpose': 'oss'},
            'disabled': False, 'policies': [], 'namespace_id': 'root',
            'aliases': [{'name': ROLE, 'canonical_id': ENTITY, 'mount_accessor': 'auth_approle_fixture',
                'mount_path': 'auth/rsc-runtime/', 'mount_type': 'approle', 'local': False}]}
        self.metadata = {'rsc_instance': 'rsc-public-test', 'purpose': 'oss', 'rsc_bootstrap_attempt': '0123456789ab'}

    def save(self, path, value):
        if path.name in self.records: raise FileExistsError()
        self.records[path.name] = copy.deepcopy(value)

    def rpc(self, method, path, token, purpose, body=None):
        self.calls.append((method, path, copy.deepcopy(body)))
        if path.endswith('/lookup-self'): return {'id': ROOT, 'policies': ['root']}
        if path.endswith('/sys/auth'): return {'rsc-runtime/': {'type': 'approle', 'local': False, 'accessor': 'auth_approle_fixture'}}
        if '/sys/policies/acl/' in path: return {'policy': json.dumps(self.policy)}
        if path.endswith('/role/rsc-oss'): return self.role
        if path.endswith('/role-id'): return {'role_id': ROLE}
        if '/identity/entity/' in path: return self.entity
        if path.endswith('/secret-id'):
            assert 'bootstrap-oss-attempt.json' in self.records
            if self.fail_after_create: raise TimeoutError('synthetic timeout')
            return {'secret_id': SECRET, 'secret_id_accessor': ACCESSOR, 'secret_id_num_uses': 1, 'secret_id_ttl': 600}
        if path.endswith('/secret-id-accessor/lookup'):
            return None if self.absent else {'metadata': self.metadata, 'secret_id_num_uses': 1, 'secret_id_ttl': 600}
        raise AssertionError('unexpected endpoint')

    def invoke(self, request=REQUEST):
        with patch.object(remote, 'rpc', side_effect=self.rpc), patch.object(remote, 'seal_status'), \
             patch.object(remote, 'read_record', side_effect=lambda path: self.records.get(path.name)), \
             patch.object(remote, 'new_record', side_effect=self.save):
            return remote.handle(request)


class BootstrapTests(unittest.TestCase):
    def test_single_create_preceded_by_durable_marker_and_private_only_credentials(self):
        fixture = Fixture(); value = fixture.invoke()
        self.assertEqual(value['secretId'], SECRET)
        self.assertEqual(len([row for row in fixture.calls if row[1].endswith('/secret-id')]), 1)
        self.assertNotIn(ROOT, json.dumps(fixture.records)); self.assertNotIn(SECRET, json.dumps(fixture.records))
        self.assertIn(ACCESSOR, json.dumps(fixture.records))  # private server journal, never public output
        with self.assertRaises(remote.transport.Rejected): fixture.invoke()
        self.assertEqual(len([row for row in fixture.calls if row[1].endswith('/secret-id')]), 1)

    def test_transport_loss_preserves_unknown_and_status_never_reissues(self):
        fixture = Fixture(); fixture.fail_after_create = True
        with self.assertRaises(TimeoutError): fixture.invoke()
        value = fixture.invoke({**REQUEST, 'operation': 'status'})
        self.assertEqual(value['accessorState'], 'unknown')
        with self.assertRaises(remote.transport.Rejected): fixture.invoke()
        self.assertEqual(len([row for row in fixture.calls if row[1].endswith('/secret-id')]), 1)

    def test_status_absence_does_not_claim_agent_authentication(self):
        fixture = Fixture(); fixture.invoke(); fixture.absent = True
        value = fixture.invoke({**REQUEST, 'operation': 'status'})
        self.assertEqual(value, {'status': 'read_only', 'attemptPresent': True, 'accessorState': 'absent',
                                 'automaticReplayAllowed': False})
        for secret in (ROOT, SECRET, ACCESSOR, ROLE): self.assertNotIn(secret, json.dumps(value))

    def test_different_attempt_cannot_bypass_existing_marker(self):
        fixture = Fixture(); fixture.invoke()
        with self.assertRaises(remote.transport.Rejected): fixture.invoke({**REQUEST, 'attemptId': 'abcdef123456'})
        self.assertEqual(len([row for row in fixture.calls if row[1].endswith('/secret-id')]), 1)

    def test_policy_role_entity_privilege_drift_blocks_before_create(self):
        for defect in ('policy', 'period', 'default', 'groups', 'alias'):
            with self.subTest(defect=defect):
                fixture = Fixture()
                if defect == 'policy': fixture.policy['path']['sys/*'] = {'capabilities': ['sudo']}
                if defect == 'period': fixture.role['token_period'] = 1201
                if defect == 'default': fixture.role['token_no_default_policy'] = False
                if defect == 'groups': fixture.entity['inherited_group_ids'] = ['privileged-group']
                if defect == 'alias': fixture.entity['aliases'][0]['mount_accessor'] = 'auth_approle_other'
                with self.assertRaises(remote.transport.Rejected): fixture.invoke()
                self.assertEqual(fixture.records, {})

    def test_whitelist_excludes_login_renew_plaintext_transit_and_other_purpose(self):
        for method, path in [('POST', '/v1/auth/rsc-runtime/login'), ('POST', '/v1/auth/token/renew-self'),
            ('POST', '/v1/transit/datakey/plaintext/key'), ('POST', '/v1/auth/rsc-runtime/role/rsc-pnvs/secret-id'),
            ('DELETE', '/v1/auth/rsc-runtime/role/rsc-oss')]:
            self.assertFalse(remote.allowed(method, path, 'oss'))

    def test_generated_metadata_drift_stops_before_delivery_preserving_accessor(self):
        fixture = Fixture(); fixture.metadata['purpose'] = 'pnvs'
        with self.assertRaises(remote.transport.Rejected): fixture.invoke()
        self.assertIn('bootstrap-oss-accessor.json', fixture.records)

    def test_create_arguments_have_no_root_secret_socket_or_state(self):
        args = SimpleNamespace(purpose='oss', attempt_id='0123456789ab', instance_id='rsc-public-test', container_id='a'*64)
        command = local.create_arguments(args, SimpleNamespace(docker=['docker']))
        mounts = [part for part in command if part.startswith('--mount=')]
        self.assertEqual(len(mounts), 1); self.assertIn('bootstrap-oss', mounts[0])
        self.assertIn('--memory=32m', command); self.assertIn('--cap-drop=ALL', command)
        self.assertIn('--network=none', command); self.assertIn('--log-driver=none', command)
        for secret in (ROOT, SECRET, ACCESSOR): self.assertNotIn(secret, json.dumps(command))

    def test_public_journal_is_exclusive_and_source_changes_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary).resolve(); path.chmod(0o700)
            args = SimpleNamespace(purpose='oss', attempt_id='0123456789ab', instance_id='rsc-public-test',
                                   container_id='a'*64, journal=path)
            value = local.binding(args)
            local.record(path, 'prepare-attempt.json', value)
            local.record(path, 'created-container.json', {**value, 'containerId': 'b'*64})
            self.assertEqual(local.exact_writer(args), 'b'*64)
            with self.assertRaises(FileExistsError): local.record(path, 'prepare-attempt.json', value)
            with patch.object(local, 'source', return_value='different public source'):
                with self.assertRaises(local.Rejected): local.exact_writer(args)

    def test_issue_secret_never_enters_public_receipt(self):
        args = SimpleNamespace(purpose='oss', attempt_id='0123456789ab', instance_id='rsc-public-test',
            container_id='a'*64, journal=Path('/not-used'), vault_mount=Path('/not-used'), vault_binding=Path('/not-used'))
        state = {'containerId': 'b'*64, 'writerState': 'created'}; records = []; commands = []
        result = {'status': 'private_bootstrap_written', 'bothFilesFsyncReadbackVerified': True,
                  'credentialValuesEmitted': False, 'automaticReplayAllowed': False}
        client = SimpleNamespace(docker=['docker'], preflight=lambda: {'stable': True},
            command=lambda argv, body, timeout: commands.append((argv, body)) or json.dumps(result).encode())
        with patch.object(local, 'exact_writer', return_value='b'*64), patch.object(local, 'load_record', return_value=state), \
             patch.object(local, 'writer_inspect', side_effect=[state, {**state, 'writerState': 'exited'}]), \
             patch.object(local, 'empty_directory'), patch.object(local, 'read_root', return_value=(ROOT, {'same': True})), \
             patch.object(local, 'record', side_effect=lambda *values: records.append(values[-1])), \
             patch.object(local, 'remote_request', return_value={'status': 'private_credential_issued', 'roleId': ROLE,
                 'secretId': SECRET, 'automaticReplayAllowed': False}), \
             patch.object(local.custody, 'vault_identity', return_value={'same': True}):
            public = local.issue(args, client)
        self.assertEqual(public['status'], 'private_bootstrap_written')
        self.assertEqual(json.loads(commands[0][1])['secretId'], SECRET)
        for secret in (ROOT, SECRET, ACCESSOR, ROLE): self.assertNotIn(secret, json.dumps(records + [public]))

    def test_container_identity_and_isolation_fail_closed(self):
        args = SimpleNamespace(purpose='oss', attempt_id='0123456789ab', instance_id='rsc-public-test', container_id='a'*64)
        row = {'Id': 'b'*64, 'Image': local.IMAGE, 'Name': '/' + local.binding(args)['writerName'],
            'Config': {'User': '23202:23212', 'Entrypoint': ['python3'],
                'Cmd': ['-B', '-c', local.source('runtime_bootstrap_writer.py')], 'OpenStdin': True, 'Tty': False,
                'Labels': {'rsc.bootstrap.attempt': args.attempt_id, 'rsc.bootstrap.purpose': 'oss'}},
            'State': {'Running': False, 'Status': 'created', 'Pid': 0, 'OOMKilled': False, 'ExitCode': 0},
            'HostConfig': {'NetworkMode': 'none', 'ReadonlyRootfs': True, 'CapDrop': ['ALL'], 'Privileged': False,
                'SecurityOpt': ['no-new-privileges=true'], 'LogConfig': {'Type': 'none'}, 'Memory': 33554432,
                'MemorySwap': 33554432, 'PidsLimit': 32, 'NanoCpus': 250000000, 'IpcMode': 'private',
                'RestartPolicy': {'Name': 'no'}, 'Ulimits': [{'Name': 'core', 'Soft': 0, 'Hard': 0}]},
            'Mounts': [{'Type': 'bind', 'Destination': '/run/bootstrap', 'Source': local.RUN + '/bootstrap-oss',
                        'RW': True, 'Propagation': 'rprivate'}]}
        for defect in (None, 'image', 'user', 'command', 'network', 'cap', 'log', 'swap', 'mount', 'running', 'env'):
            with self.subTest(defect=defect):
                value = copy.deepcopy(row)
                if defect == 'image': value['Image'] = 'sha256:' + '0'*64
                if defect == 'user': value['Config']['User'] = '0:0'
                if defect == 'command': value['Config']['Cmd'][-1] += '\n# unexpected change'
                if defect == 'network': value['HostConfig']['NetworkMode'] = 'bridge'
                if defect == 'cap': value['HostConfig']['CapAdd'] = ['DAC_OVERRIDE']
                if defect == 'log': value['HostConfig']['LogConfig']['Type'] = 'json-file'
                if defect == 'swap': value['HostConfig']['MemorySwap'] = 67108864
                if defect == 'mount': value['Mounts'].append({'Destination': '/state'})
                if defect == 'running': value['State']['Running'] = True
                if defect == 'env': value['Config']['Env'] = ['VAULT_TOKEN=synthetic-forbidden']
                client = SimpleNamespace(docker=['docker'], command=lambda argv: json.dumps([value]).encode())
                if defect is None:
                    self.assertEqual(local.writer_inspect(args, client, 'b'*64, 'created')['writerState'], 'created')
                else:
                    with self.assertRaises(local.Rejected): local.writer_inspect(args, client, 'b'*64, 'created')


if __name__ == '__main__':
    unittest.main()
