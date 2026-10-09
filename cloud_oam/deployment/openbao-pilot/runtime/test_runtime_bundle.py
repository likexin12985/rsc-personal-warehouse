"""New public-only runtime/preflight boundaries; no daemon, cloud or old suite."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import runtime_bundle as bundle
import runtime_preflight as preflight


class BundleTests(unittest.TestCase):
    def setUp(self):
        self.files = bundle.build('rsc-pilot-runtime-01')
        self.compose = json.loads(self.files['compose.json'])

    def test_manifest_binds_every_generated_input(self):
        manifest = json.loads(self.files['manifest.json'])
        self.assertEqual(set(manifest['sha256']), set(self.files) - {'manifest.json'})
        for name, digest in manifest['sha256'].items():
            self.assertEqual(digest, hashlib.sha256(self.files[name]).hexdigest())

    def test_core_has_fixed_image_no_network_or_privilege_and_no_swap(self):
        for name in ('bao', 'transit', 'oss', 'pnvs'):
            item = self.compose['services'][name]
            self.assertEqual(item['image'], bundle.IMAGE)
            self.assertEqual(item['network_mode'], 'none')
            self.assertTrue(item['read_only'])
            self.assertEqual(item['cap_drop'], ['ALL'])
            self.assertEqual(item['cap_add'], [])
            self.assertEqual(item['security_opt'], ['no-new-privileges:true'])
            self.assertEqual(item['logging'], {'driver': 'none'})
            self.assertEqual(item['mem_limit'], item['memswap_limit'])
            self.assertEqual(item['restart'], 'no')
            self.assertNotIn('ports', item)

    def test_no_agent_reads_other_bootstrap_projection_or_state(self):
        for name in bundle.PURPOSES:
            mounts = self.compose['services'][name]['volumes']
            sources = {x['source'] for x in mounts}
            self.assertIn(bundle.RUN + '/bootstrap-' + name, sources)
            self.assertIn(bundle.RUN + '/token-' + name, sources)
            self.assertNotIn(bundle.STATE, sources)
            for other in set(bundle.PURPOSES) - {name}:
                self.assertNotIn(bundle.RUN + '/bootstrap-' + other, sources)
                self.assertNotIn(bundle.RUN + '/token-' + other, sources)
            socket = next(x for x in mounts if x['target'] == '/run/rsc-bao')
            self.assertTrue(socket['read_only'])

    def test_persistent_state_and_config_are_separate(self):
        mounts = self.compose['services']['bao']['volumes']
        state = next(x for x in mounts if x['target'] == '/state')
        self.assertFalse(state['read_only'])
        self.assertEqual(state['source'], bundle.STATE)
        self.assertTrue(next(x for x in mounts if x['target'] == '/runtime/server.json')['read_only'])
        self.assertTrue(all(x['bind']['create_host_path'] is False for x in mounts))

    def test_periodic_acl_is_precise_and_declares_shared_token_limitation(self):
        for purpose in bundle.PURPOSES:
            paths = json.loads(self.files[purpose + '-policy.json'])['path']
            expected = set(bundle.SELF_POLICY)
            if purpose == 'transit':
                expected |= {'transit/decrypt/rsc-authentication-idempotency',
                             'transit/decrypt/rsc-material-request-contact'}
            else:
                expected.add('identity/oidc/token/rsc-' + purpose)
            self.assertEqual(set(paths), expected)
            self.assertNotIn('*', json.dumps(paths))
        plan = json.loads(self.files['public-plan.json'])
        self.assertTrue(plan['transitApiCanRenewOwnToken'])
        self.assertFalse(plan['actualPeriodicRenewalVerified'])
        self.assertFalse(plan['productionReady'])

    def test_role_period_no_default_single_use_short_bootstrap(self):
        roles = json.loads(self.files['approle-contract.json'])
        for purpose, role in roles.items():
            self.assertEqual(role['token_policies'], ['rsc-' + purpose])
            self.assertEqual(role['token_type'], 'service')
            self.assertEqual(role['token_period'], '1200s')
            self.assertEqual(role['secret_id_ttl'], '600s')
            self.assertEqual(role['secret_id_num_uses'], 1)
            self.assertEqual(role['token_explicit_max_ttl'], 0)
            self.assertTrue(role['token_no_default_policy'])

    def test_agent_sinks_and_templates_are_official_and_distinct(self):
        for purpose in bundle.PURPOSES:
            agent = json.loads(self.files[purpose + '-agent.json'])
            method = agent['auto_auth']['method'][0]
            self.assertTrue(method['config']['remove_secret_id_file_after_reading'])
            if purpose == 'transit':
                self.assertNotIn('template', agent)
                self.assertEqual(agent['auto_auth']['sinks'][0]['sink']['config']['mode'], 0o440)
            else:
                self.assertNotIn('sinks', agent['auto_auth'])
                template = agent['template'][0]
                self.assertIn('identity/oidc/token/rsc-' + purpose, template['contents'])
                self.assertEqual(template['perms'], '0440')
                self.assertFalse(template['backup'])
                self.assertFalse(template['create_dest_dirs'])
                self.assertTrue(agent['template_config']['exit_on_retry_failure'])

    def test_server_declarative_audit_keeps_scalar_and_disables_unsafe_api(self):
        server = json.loads(self.files['server.json'])
        self.assertFalse(server['unsafe_allow_api_audit_creation'])
        audit = server['audit'][0]['file']['rsc-runtime']
        self.assertIsInstance(audit['description'], str)
        self.assertEqual(audit['options']['log_raw'], 'false')
        self.assertEqual(server['listener'][1]['tcp']['address'], '127.0.0.1:19200')
        self.assertTrue(server['listener'][1]['tcp']['telemetry']['metrics_only'])

    def test_gateway_private_network_two_exact_gets_and_headers_removed(self):
        service = self.compose['services']['metadata']
        self.assertEqual(service['image'], bundle.CADDY_IMAGE)
        self.assertNotIn('ports', service)
        self.assertTrue(self.compose['networks']['metadata']['internal'])
        text = self.files['gateway.Caddyfile'].decode()
        self.assertEqual(text.count('method GET'), 2)
        self.assertEqual(text.count('header_up -*'), 2)
        self.assertIn('{http.request.uri.query} != ""', text)
        self.assertIn('respond @query 404', text)
        self.assertNotIn('/v1/sys/', text)

    def test_systemd_starts_only_bao_after_preflight(self):
        text = self.files['rsc-openbao.service'].decode()
        self.assertIn('ExecStartPre=', text)
        self.assertIn('--instance-id rsc-pilot-runtime-01', text)
        self.assertIn('--pull never bao', text)
        self.assertNotIn('--profile agents', text)

    def test_unsafe_instance_names_fail_before_writing(self):
        for name in ('rsc-../evil', 'rsc-a;echo', 'rsc-$(id)', 'default', 'rsc-x\n'):
            with self.subTest(name=name), self.assertRaises(bundle.Rejected):
                bundle.build(name)

    def test_bundle_existing_path_never_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary).resolve() / 'bundle'
            bundle.write_new_bundle(target, self.files)
            with self.assertRaises(bundle.Rejected):
                bundle.write_new_bundle(target, self.files)

    def test_mountinfo_longest_mount_controls_tmpfs_admission(self):
        good = '1 0 0:1 / /run rw,nosuid,nodev - tmpfs tmpfs rw\n'
        preflight.tmpfs('/run/rsc-openbao', good)
        for bad in ('1 0 0:1 / /run rw,nosuid - tmpfs tmpfs rw\n',
                    good + '2 1 8:1 / /run/rsc-openbao rw - ext4 /dev/a rw\n',
                    '1 0 0:1 / /tmp rw,nosuid,nodev - tmpfs tmpfs rw\n'):
            with self.subTest(bad=bad), self.assertRaises(bundle.Rejected):
                preflight.tmpfs('/run/rsc-openbao', bad)


if __name__ == '__main__':
    unittest.main()
