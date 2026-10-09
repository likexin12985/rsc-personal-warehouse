"""New synthetic unseal boundaries; no real share, disk image or server."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import runtime_unseal as local
import runtime_unseal_remote as remote

STATUS = {'type': 'shamir', 'version': '2.7.1', 'initialized': True,
          'sealed': True, 'n': 1, 't': 1, 'progress': 0}
REQUEST = {'operation': 'unseal', 'runId': '123456abcdef',
           'pgpFingerprint': 'A' * 40, 'share': 'ab' * 32}


class UnsealTests(unittest.TestCase):
    def test_already_unsealed_only_reads_without_marker_or_write(self):
        with (patch.object(remote.transport, 'rpc', return_value={**STATUS, 'sealed': False}),
                patch.object(remote, 'unseal_once') as write):
            result = remote.handle(REQUEST)
        self.assertFalse(result['writeAttempted']); write.assert_not_called()

    def test_wrong_shamir_version_or_threshold_blocks(self):
        for changes in ({'type': 'auto'}, {'version': '2.6.0'}, {'t': 2}, {'n': 3}, {'initialized': False}):
            with (self.subTest(changes=changes), patch.object(remote.transport, 'rpc', return_value={**STATUS, **changes}),
                    patch.object(remote, 'unseal_once') as write, self.assertRaises(remote.transport.Rejected)):
                remote.handle(REQUEST)
            write.assert_not_called()

    def test_partial_unseal_progress_requires_review(self):
        with (patch.object(remote.transport, 'rpc', return_value={**STATUS, 'progress': 1}),
                patch.object(remote, 'unseal_once') as write, self.assertRaisesRegex(remote.transport.Rejected, 'partial_progress')):
            remote.handle(REQUEST)
        write.assert_not_called()

    def test_single_success_marker_contains_only_public_binding(self):
        with tempfile.TemporaryDirectory() as temporary:
            with (patch.object(remote, 'STATE', Path(temporary)),
                    patch.object(remote.transport, 'rpc', side_effect=[STATUS, {**STATUS, 'sealed': False}]),
                    patch.object(remote, 'unseal_once') as write):
                result = remote.handle(REQUEST)
            write.assert_called_once_with(REQUEST['share']); self.assertTrue(result['writeAttempted'])
            text = (Path(temporary) / 'unseal-attempt-123456abcdef.json').read_text()
            self.assertNotIn(REQUEST['share'], text)
            self.assertFalse(json.loads(text)['automaticReplayAllowed'])

    def test_timeout_reserves_attempt_and_repeated_call_never_replays(self):
        with tempfile.TemporaryDirectory() as temporary:
            with (patch.object(remote, 'STATE', Path(temporary)), patch.object(remote.transport, 'rpc', return_value=STATUS),
                    patch.object(remote, 'unseal_once', side_effect=TimeoutError) as write):
                with self.assertRaises(TimeoutError): remote.handle(REQUEST)
                with self.assertRaises(FileExistsError): remote.handle(REQUEST)
            self.assertEqual(write.call_count, 1)

    def test_share_and_request_closed_shape(self):
        for request in ({**REQUEST, 'share': 'not-valid'}, {**REQUEST, 'root_token': 'forbidden'},
                        {**REQUEST, 'runId': '../unsafe'}):
            with self.subTest(keys=tuple(request)), self.assertRaises(remote.transport.Rejected):
                remote.handle(request)

    def test_controller_share_only_in_private_stdin_and_public_response(self):
        response = {'status': 'unsealed_verified', 'alreadyUnsealed': False, 'writeAttempted': True,
                    'initialized': True, 'sealed': False, 'automaticReplayAllowed': False}
        client = Mock(); client.preflight.return_value = {'id': 'a' * 64}; client.docker = ['/usr/bin/docker']
        client.command.return_value = json.dumps(response).encode()
        args = SimpleNamespace(container_id='a' * 64, run_id=REQUEST['runId'], vault_mount=Path('/synthetic'),
                               vault_binding=Path('/public-binding'))
        with (patch.object(local, 'custody_share', return_value=(REQUEST['share'], 'A' * 40, {'device': 1})),
                patch.object(local.custody, 'vault_identity', return_value={'device': 1})):
            result = local.execute(args, client)
        argv, body = client.command.call_args.args
        self.assertNotIn(REQUEST['share'], ' '.join(argv))
        self.assertEqual(json.loads(body)['share'], REQUEST['share'])
        self.assertNotIn(REQUEST['share'], json.dumps(result))
        self.assertFalse(result['productionReady'])


if __name__ == '__main__':
    unittest.main()
