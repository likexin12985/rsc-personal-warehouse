"""New exact bootstrap-phase tests; no service, root credential or old suites."""
import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import runtime_configure as local
import runtime_configure_remote as remote

RUN = '123456abcdef'


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.mode = patch.object(remote, 'ALLOW_WRITES', True)
        self.mode.start(); self.addCleanup(self.mode.stop)

    def test_readonly_missing_object_never_reserves_or_writes(self):
        with patch.object(remote, 'ALLOW_WRITES', False), patch.object(remote, 'reserve') as reserve:
            write = Mock()
            with self.assertRaisesRegex(remote.transport.Rejected, 'readonly_no_write'):
                remote.ensure('oidc-role-oss', lambda: None, lambda value: True, write, RUN)
        reserve.assert_not_called(); write.assert_not_called()

    def test_absent_write_once_and_precise_readback(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(remote, 'STATE', Path(temporary)):
            read = Mock(side_effect=[None, {'exact': True}]); write = Mock()
            value, created = remote.ensure('policy-transit', read, lambda x: x == {'exact': True}, write, RUN)
            self.assertTrue(created); self.assertEqual(value, {'exact': True}); write.assert_called_once()
            self.assertTrue((Path(temporary) / 'configure-policy-transit.json').is_file())

    def test_equal_current_configuration_does_not_mutate_or_reserve(self):
        with patch.object(remote, 'reserve') as reserve:
            write = Mock(); result = remote.ensure('policy-transit', lambda: {'exact': True},
                lambda x: x == {'exact': True}, write, RUN)
        self.assertEqual(result, ({'exact': True}, False)); write.assert_not_called(); reserve.assert_not_called()

    def test_existing_drift_rejected_without_write_or_marker(self):
        with patch.object(remote, 'reserve') as reserve:
            write = Mock()
            with self.assertRaisesRegex(remote.transport.Rejected, 'existing_configuration_drift'):
                remote.ensure('policy-transit', lambda: {'root': True}, lambda x: False, write, RUN)
        write.assert_not_called(); reserve.assert_not_called()

    def test_unknown_write_marker_blocks_replay_but_exact_readback_can_reconcile(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(remote, 'STATE', Path(temporary)):
            write = Mock(side_effect=TimeoutError)
            with self.assertRaises(TimeoutError):
                remote.ensure('oidc-signing-key', lambda: None, lambda x: True, write, RUN)
            with self.assertRaises(FileExistsError):
                remote.ensure('oidc-signing-key', lambda: None, lambda x: True, write, RUN)
            self.assertEqual(write.call_count, 1)
            result = remote.ensure('oidc-signing-key', lambda: {'exact': True}, lambda x: x == {'exact': True}, write, RUN)
            self.assertFalse(result[1]); self.assertEqual(write.call_count, 1)

    def test_rpc_allowlist_excludes_token_generation_datakey_secretid_and_arbitrary_paths(self):
        for method, path in [('POST', '/v1/auth/rsc-runtime/login'), ('POST', '/v1/auth/token/create'),
                            ('POST', '/v1/auth/rsc-runtime/role/rsc-oss/secret-id'),
                            ('POST', '/v1/transit/datakey/wrapped/rsc-material-request-contact'),
                            ('POST', '/v1/transit/keys/rsc-material-request-contact'),
                            ('DELETE', '/v1/identity/oidc/key/rsc-runtime'), ('GET', '/v1/sys/raw')]:
            with self.subTest(method=method, path=path): self.assertFalse(remote.allowed(method, path))

    def test_role_contract_defaults_periodic_and_extra_effective_policies_blocked(self):
        expected = remote.role_contract('oss'); valid = {**expected, 'local_secret_ids': False}
        self.assertTrue(remote.role_valid(valid, expected))
        for drift in ({'token_policies': ['rsc-oss', 'root']}, {'policies': ['default']},
                      {'token_no_default_policy': False}, {'token_type': 'batch'}, {'period': 300},
                      {'local_secret_ids': True}, {'token_period': True}):
            with self.subTest(drift=drift): self.assertFalse(remote.role_valid({**valid, **drift}, expected))
        self.assertEqual(set(remote.policy('transit')['path']), {
            'auth/token/lookup-self', 'auth/token/renew-self',
            'transit/decrypt/rsc-authentication-idempotency', 'transit/decrypt/rsc-material-request-contact'})

    def test_entity_metadata_groups_policies_and_history_are_bounded(self):
        entity = {'id': '11111111-1111-1111-1111-111111111111', 'name': 'rsc-oss', 'disabled': False,
                  'namespace_id': 'root', 'policies': [], 'aliases': [],
                  'metadata': {'rsc_instance': 'rsc-pilot-runtime-01', 'purpose': 'oss'}}
        self.assertTrue(remote.entity_valid(entity, 'rsc-oss', 'rsc-pilot-runtime-01'))
        for drift in ({'policies': ['root']}, {'group_ids': ['extra']}, {'inherited_group_ids': ['extra']},
                      {'metadata': {}}, {'merged_entity_ids': ['old']}, {'namespace_id': 'other'}):
            with self.subTest(drift=drift):
                self.assertFalse(remote.entity_valid({**entity, **drift}, 'rsc-oss', 'rsc-pilot-runtime-01'))

    def test_oidc_three_exact_shapes_and_distinct_roles(self):
        request = {'rootToken': 'synthetic-token', 'runId': RUN, 'ossAudience': 'oss-test', 'pnvsAudience': 'pnvs-test'}
        calls = []
        def ensure(label, read, valid, write, run, **kwargs):
            result = write(); self.assertTrue(valid(result)); calls.append(label); return result, True
        def rpc(method, path, token, body=None):
            self.assertEqual(method, 'POST'); self.assertEqual(token, 'synthetic-token'); return body
        with patch.object(remote, 'ensure', side_effect=ensure), patch.object(remote, 'rpc', side_effect=rpc):
            result = remote.configure_oidc(request)
        self.assertEqual(calls, ['oidc-issuer', 'oidc-signing-key', 'oidc-role-oss', 'oidc-role-pnvs'])
        self.assertEqual(result['jwtTtlSeconds'], 600); self.assertFalse(result['jwtIssued'])

    def test_three_auth_identities_aliases_and_policies_create_once_then_only_read(self):
        database = {}; entities = {}; writes = []; accessor = 'auth_approle_synthetic'
        request = {'rootToken': 'synthetic-token', 'runId': RUN, 'instanceId': 'rsc-pilot-runtime-01'}
        def rpc(method, path, token, body=None):
            if method == 'POST':
                writes.append(path)
                if path == '/v1/sys/auth/rsc-runtime':
                    database['/v1/sys/auth'] = {'rsc-runtime/': {'type': 'approle', 'local': False, 'accessor': accessor}}
                elif path.startswith('/v1/identity/entity/name/'):
                    name = path.rsplit('/', 1)[-1]; ident = str(len(entities) + 1) * 8 + '-1111-1111-1111-111111111111'
                    database[path] = {**body, 'id': ident, 'namespace_id': 'root', 'aliases': []}; entities[ident] = database[path]
                elif path == '/v1/identity/entity-alias':
                    entities[body['canonical_id']]['aliases'].append({**body, 'local': False, 'metadata': {},
                        'mount_type': 'approle', 'mount_path': 'auth/rsc-runtime/', 'custom_metadata': {}})
                elif path.startswith('/v1/auth/rsc-runtime/role/'):
                    database[path] = {**body, 'local_secret_ids': False}
                else:
                    database[path] = body
                return {}
            if path == '/v1/sys/auth': return database.get(path, {})
            if path.endswith('/role-id'):
                digit = str(remote.PURPOSES.index(path.split('/')[-2].removeprefix('rsc-')) + 1)
                return {'role_id': digit * 8 + '-9999-9999-9999-999999999999'}
            return database.get(path)
        with tempfile.TemporaryDirectory() as temporary, patch.object(remote, 'STATE', Path(temporary)), patch.object(remote, 'rpc', side_effect=rpc):
            first = remote.configure_auth(request); self.assertEqual(len(writes), 13)
            second = remote.configure_auth(request); self.assertEqual(len(writes), 13)
        self.assertEqual(first, second); self.assertEqual(len(set(first['entities'].values())), 3)
        self.assertFalse(first['secretIdsCreated']); self.assertFalse(first['roleIdsEmitted'])

    def test_duplicate_role_identifiers_stop_before_a_second_entity_alias(self):
        def ensure(label, read, valid, write, run, **kwargs):
            if label == 'approle-mount': return {'accessor': 'auth_approle_synthetic'}, False
            if label.startswith('entity-'): return {'id': '11111111-1111-1111-1111-111111111111'}, False
            return {}, False
        with (patch.object(remote, 'ensure', side_effect=ensure) as checked,
                patch.object(remote, 'rpc', return_value={'role_id': '99999999-1111-1111-1111-111111111111'}),
                self.assertRaisesRegex(remote.transport.Rejected, 'role_identifiers_not_distinct')):
            remote.configure_auth({'rootToken': 'synthetic-token', 'runId': RUN, 'instanceId': 'rsc-pilot-runtime-01'})
        self.assertEqual([call.args[0] for call in checked.call_args_list if call.args[0].startswith('alias-')], ['alias-transit'])

    def test_backend_error_and_get_warning_never_treated_as_success(self):
        for value in ({'data': {'error': 'synthetic'}}, {'errors': ['synthetic']},
                      {'data': {'id': 'synthetic'}, 'warnings': ['index repair']}):
            connection = Mock(); response = Mock(status=200); response.read.return_value = json.dumps(value).encode()
            connection.getresponse.return_value = response
            with (patch.object(remote.transport, 'UnixConnection', return_value=connection),
                    self.assertRaises(remote.transport.Rejected)):
                remote.rpc('GET', '/v1/auth/token/lookup-self', 'synthetic-token')
            connection.close.assert_called_once()

    def test_public_details_reject_secret_fields_and_wrong_ids(self):
        value = {'entities': {p: ('1' if p == 'transit' else '2' if p == 'oss' else '3') * 8 + '-1111-1111-1111-111111111111'
                             for p in ('transit', 'oss', 'pnvs')},
                 'policyBusinessAndSelfPathsVerified': True, 'roleIdsEmitted': False, 'secretIdsCreated': False}
        local.public_details('auth', value)
        with self.assertRaises(local.Rejected): local.public_details('auth', {**value, 'rootToken': 'synthetic'})
        wrong = copy.deepcopy(value); wrong['entities']['oss'] = wrong['entities']['pnvs']
        with self.assertRaises(local.Rejected): local.public_details('auth', wrong)

    def test_controller_root_only_in_private_stdin_not_argv_or_public_result(self):
        token = 'synthetic-root-no-credentials'
        details = {'issuer': 'https://rscwz.cn/v1/identity/oidc', 'jwtTtlSeconds': 600,
                   'cloudTrustConfigured': False, 'jwtIssued': False}
        result = {'status': 'phase_verified', 'phase': 'oidc', 'runId': RUN, 'details': details,
                  'automaticReplayAllowed': False, 'productionReady': False, 'writeModeEnabled': False}
        client = Mock(); client.preflight.return_value = {'id': 'a' * 64}; client.docker = ['/usr/bin/docker']
        client.command.return_value = json.dumps(result).encode()
        args = SimpleNamespace(container_id='a' * 64, run_id=RUN, phase='oidc', instance_id='rsc-pilot-runtime-01', apply=False,
            oss_audience='oss-test', pnvs_audience='pnvs-test', vault_mount=Path('/synthetic'), vault_binding=Path('/binding'))
        with (patch.object(local, 'read_root', return_value=(token, {'device': 1})),
                patch.object(local.custody, 'vault_identity', return_value={'device': 1})):
            output = local.execute(args, client)
        argv, body = client.command.call_args.args
        self.assertNotIn(token, ' '.join(argv)); self.assertEqual(json.loads(body)['rootToken'], token)
        self.assertNotIn(token, json.dumps(output))


class OidcIssuerResponseTests(unittest.TestCase):
    # Independent literal from fixed store_oidc.go:480-483, not the consumer constant.
    warning = ('If "issuer" is set explicitly, all tokens must be validated against that address, '
               'including those issued by secondary clusters. Setting issuer to "" will restore '
               "the default behavior of using the cluster's api_addr as the issuer.")
    config = '/v1/identity/oidc/config'
    issuer = {'issuer': 'https://rscwz.cn'}
    request = {'rootToken': 'synthetic-only-token', 'runId': RUN,
               'ossAudience': 'oss-test', 'pnvsAudience': 'pnvs-test'}

    def response_rpc(self, value, *, status=200, method='POST', path=None, body=None):
        connection = Mock(); response = Mock(status=status)
        response.read.return_value = json.dumps(value).encode()
        connection.getresponse.return_value = response
        with patch.object(remote.transport, 'UnixConnection', return_value=connection):
            try:
                return remote.rpc(method, path or self.config, 'synthetic-only-token',
                                  self.issuer if body is None else body)
            finally:
                connection.close.assert_called_once()

    def envelope(self):
        return {'data': None, 'warnings': [self.warning]}

    def test_exact_fixed_post_warning_and_null_data_is_only_acknowledgement(self):
        self.assertEqual(self.response_rpc(self.envelope()), {})

    def test_null_ack_rejects_other_method_path_or_issuer_body(self):
        for changed in ({'method': 'GET'}, {'path': '/v1/identity/oidc/key/rsc-runtime'},
                        {'path': '/v1/identity/oidc/role/rsc-oss'}, {'body': {}},
                        {'body': {'issuer': 'https://unexpected.invalid'}},
                        {'body': {**self.issuer, 'extra': True}}):
            with self.subTest(changed=changed), self.assertRaises(remote.transport.Rejected):
                self.response_rpc(self.envelope(), **changed)

    def test_null_ack_rejects_non200_and_backend_errors(self):
        for status in (201, 204, 400, 500):
            with self.subTest(status=status), self.assertRaises(remote.transport.Rejected):
                self.response_rpc(self.envelope(), status=status)
        for value in ({**self.envelope(), 'errors': ['synthetic backend error']},
                      {**self.envelope(), 'data': {'error': 'synthetic backend error'}}):
            with self.subTest(value=value), self.assertRaises(remote.transport.Rejected):
                self.response_rpc(value)

    def test_null_ack_rejects_missing_changed_extra_or_mistyped_warning(self):
        for warnings in (None, [], [self.warning + ' altered'], [self.warning, 'extra'], self.warning):
            with self.subTest(warnings=warnings), self.assertRaises(remote.transport.Rejected):
                self.response_rpc({'data': None, 'warnings': warnings})

    def test_null_ack_rejects_nonobject_nonnull_data(self):
        for data in ([], 'null', False, 0):
            with self.subTest(data=data), self.assertRaises(remote.transport.Rejected):
                self.response_rpc({**self.envelope(), 'data': data})

    def fake_http(self, database, calls, *, drift=False, timeout=False, read_warning=False):
        connections = []
        def factory(*args, **kwargs):
            connection = Mock(); response = Mock(); connections.append(connection)
            def request(method, path, body=None, headers=None):
                calls.append((method, path))
                if method == 'GET':
                    data = database.get(path, {'issuer': ''} if path == self.config else None)
                    response.status = 404 if data is None else 200
                    value = {'data': data}
                    if read_warning: value['warnings'] = [self.warning]
                else:
                    if timeout and path == self.config:
                        response.read.side_effect = TimeoutError('synthetic response loss')
                        response.status = 200
                        return
                    database[path] = json.loads(body)
                    if drift and path == self.config:
                        database[path] = {'issuer': 'https://unexpected.invalid'}
                    response.status = 200 if path == self.config else 204
                    value = self.envelope() if path == self.config else None
                response.read.return_value = b'' if value is None else json.dumps(value).encode()
            connection.request.side_effect = request; connection.getresponse.return_value = response
            return connection
        return factory, connections

    def test_first_configure_requires_exact_get_after_every_post(self):
        database = {}; calls = []; factory, connections = self.fake_http(database, calls)
        paths = [self.config, '/v1/identity/oidc/key/rsc-runtime',
                 '/v1/identity/oidc/role/rsc-oss', '/v1/identity/oidc/role/rsc-pnvs']
        with (tempfile.TemporaryDirectory() as temporary, patch.object(remote, 'STATE', Path(temporary)),
              patch.object(remote, 'ALLOW_WRITES', True),
              patch.object(remote.transport, 'UnixConnection', side_effect=factory)):
            result = remote.configure_oidc(self.request)
            markers = sorted(Path(temporary).glob('configure-*.json'))
            self.assertEqual(len(markers), 4)
            for marker in markers:
                self.assertEqual(marker.stat().st_mode & 0o777, 0o600)
                fact = json.loads(marker.read_text())
                self.assertEqual(fact['runId'], RUN); self.assertFalse(fact['automaticReplayAllowed'])
        self.assertEqual(calls, [(method, path) for path in paths for method in ('GET', 'POST', 'GET')])
        self.assertFalse(result['jwtIssued']); self.assertFalse(result['cloudTrustConfigured'])
        for connection in connections: connection.close.assert_called_once()

    def test_resume_reads_existing_issuer_and_keeps_original_marker_then_creates_only_three(self):
        database = {self.config: dict(self.issuer)}; calls = []
        factory, connections = self.fake_http(database, calls)
        paths = ['/v1/identity/oidc/key/rsc-runtime', '/v1/identity/oidc/role/rsc-oss',
                 '/v1/identity/oidc/role/rsc-pnvs']
        with (tempfile.TemporaryDirectory() as temporary, patch.object(remote, 'STATE', Path(temporary)),
              patch.object(remote, 'ALLOW_WRITES', True),
              patch.object(remote.transport, 'UnixConnection', side_effect=factory)):
            remote.reserve('oidc-issuer', RUN)
            marker = Path(temporary) / 'configure-oidc-issuer.json'
            original = (marker.read_bytes(), marker.stat().st_ino, marker.stat().st_mtime_ns)
            remote.configure_oidc(self.request)
            self.assertEqual(calls, [('GET', self.config)] +
                             [(method, path) for path in paths for method in ('GET', 'POST', 'GET')])
            calls.clear()
            with patch.object(remote, 'ALLOW_WRITES', False): remote.configure_oidc(self.request)
            self.assertEqual(calls, [('GET', path) for path in [self.config, *paths]])
            self.assertEqual((marker.read_bytes(), marker.stat().st_ino, marker.stat().st_mtime_ns), original)
        for connection in connections: connection.close.assert_called_once()

    def test_post_ack_does_not_bypass_exact_readback_or_remove_marker(self):
        database = {}; calls = []; factory, connections = self.fake_http(database, calls, drift=True)
        with (tempfile.TemporaryDirectory() as temporary, patch.object(remote, 'STATE', Path(temporary)),
              patch.object(remote, 'ALLOW_WRITES', True),
              patch.object(remote.transport, 'UnixConnection', side_effect=factory)):
            with self.assertRaisesRegex(remote.transport.Rejected, '^configuration_readback_failed_or_unknown$'):
                remote.configure_oidc(self.request)
            marker = Path(temporary) / 'configure-oidc-issuer.json'
            original = marker.read_bytes()
            with self.assertRaisesRegex(remote.transport.Rejected, '^existing_configuration_drift$'):
                remote.configure_oidc(self.request)
            self.assertEqual(marker.read_bytes(), original)
            self.assertEqual(len(list(Path(temporary).iterdir())), 1)
        self.assertEqual(calls, [(method, self.config) for method in ('GET', 'POST', 'GET', 'GET')])
        for connection in connections: connection.close.assert_called_once()

    def test_unknown_post_retains_marker_and_blocks_second_post(self):
        database = {}; calls = []; factory, connections = self.fake_http(database, calls, timeout=True)
        with (tempfile.TemporaryDirectory() as temporary, patch.object(remote, 'STATE', Path(temporary)),
              patch.object(remote, 'ALLOW_WRITES', True),
              patch.object(remote.transport, 'UnixConnection', side_effect=factory)):
            with self.assertRaises(TimeoutError): remote.configure_oidc(self.request)
            marker = Path(temporary) / 'configure-oidc-issuer.json'; original = marker.read_bytes()
            with self.assertRaises(FileExistsError): remote.configure_oidc(self.request)
            self.assertEqual(marker.read_bytes(), original)
        self.assertEqual(calls, [(method, self.config) for method in ('GET', 'POST', 'GET')])
        for connection in connections: connection.close.assert_called_once()

    def test_get_warning_still_rejected_before_any_reserve_or_post(self):
        database = {}; calls = []; factory, connections = self.fake_http(database, calls, read_warning=True)
        with (patch.object(remote, 'ALLOW_WRITES', True), patch.object(remote, 'reserve') as reserve,
              patch.object(remote.transport, 'UnixConnection', side_effect=factory),
              self.assertRaisesRegex(remote.transport.Rejected, '^configure_read_warning_requires_review$')):
            remote.configure_oidc(self.request)
        reserve.assert_not_called(); self.assertEqual(calls, [('GET', self.config)])
        for connection in connections: connection.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
