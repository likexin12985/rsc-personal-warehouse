"""Explicit first Transit setup and encrypted-response custody; never selects DB versions."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

sys.dont_write_bytecode = True
import runtime_init as custody
from runtime_configure import read_root
from runtime_bundle import Rejected, require

PURPOSES = ('authentication_idempotency', 'material_request_contact')


def contract_bridge(interpreter, value):
    require(interpreter.is_absolute() and interpreter.is_file(), 'contract_interpreter_required')
    script = Path(__file__).with_name('runtime_transit_contract.py')
    result = subprocess.run([str(interpreter), '-B', str(script)], input=json.dumps(value).encode() + b'\n',
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=15,
        env={'PATH': '/usr/bin:/bin:/usr/sbin:/sbin', 'PYTHONDONTWRITEBYTECODE': '1'})
    require(result.returncode == 0 and len(result.stdout) <= 32768, 'production_contract_bridge_rejected')
    public = json.loads(result.stdout)
    require(type(public) is dict and set(public) == {'status', 'result'}
            and public['status'] == 'contract_verified' and type(public['result']) is dict,
            'production_contract_bridge_shape')
    return public['result']


def coordinate(args):
    require(args.purpose in PURPOSES and type(args.application_key_version) is int
            and 1 <= args.application_key_version <= 2147483647, 'explicit_application_version_required')
    return {'purpose': args.purpose, 'environment': 'production', 'provider_instance_id': args.instance_id,
            'application_key_version': args.application_key_version}


def directory_name(args):
    return 'transit-' + args.purpose + '-v' + str(args.application_key_version)


def open_custody(args, identity, recover):
    require(custody.vault_identity(args.vault_mount, args.vault_binding) == identity, 'transit_custody_changed')
    parent = os.open(args.vault_mount, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        try:
            os.mkdir(directory_name(args), mode=0o700, dir_fd=parent); os.fsync(parent)
        except FileExistsError:
            require(recover, 'transit_local_attempt_exists_no_replay')
        descriptor = os.open(directory_name(args), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        info = os.fstat(descriptor)
        require(info.st_dev == identity['device'] and stat.S_IMODE(info.st_mode) == 0o700
                and info.st_uid == os.geteuid(), 'transit_custody_directory_identity')
        return descriptor
    finally:
        os.close(parent)


def store_or_verify(descriptor, name, value):
    body = json.dumps(value, sort_keys=True).encode()
    try:
        existing = custody.existing_file(descriptor, name)
    except FileNotFoundError:
        custody.new_file(descriptor, name, body)
    else:
        require(existing == body, 'transit_custody_existing_bytes_differ')


def execute(args, client):
    supplied_coordinate = coordinate(args)
    public_contract = contract_bridge(args.contract_python, {'coordinate': supplied_coordinate})
    require(public_contract.get('coordinate') == supplied_coordinate, 'transit_contract_coordinate_changed')
    before = client.preflight(); token, identity = read_root(args)
    script = Path(__file__).with_name('runtime_transit_remote.py').read_text()
    request = {'operation': args.operation, 'attemptId': args.attempt_id, 'instanceId': args.instance_id,
               'rootToken': token, 'apply': args.apply, 'contract': public_contract}
    descriptor = None
    try:
        if args.operation in ('generate', 'recover'):
            require(args.apply is (args.operation == 'generate'), 'transit_write_mode_mismatch')
            descriptor = open_custody(args, identity, args.operation == 'recover')
            store_or_verify(descriptor, 'generation-intent.json', {'attemptId': args.attempt_id,
                'containerId': args.container_id, 'initRunId': args.run_id, 'contract': public_contract,
                'automaticReplayAllowed': False})
        raw = client.command(client.docker + ['exec', '-i', '--user=23101:23101', args.container_id,
            'python3', '-B', '-c', script], json.dumps(request).encode() + b'\n', timeout=90)
        request = None; token = None
        value = json.loads(raw)
        require(client.preflight() == before and custody.vault_identity(args.vault_mount, args.vault_binding) == identity,
                'transit_identity_changed')
        if args.operation == 'keys':
            require(value == {'status': 'keys_verified', 'wrappedDataKeysGenerated': False,
                'automaticReplayAllowed': False, 'productionReady': False}, 'transit_keys_public_shape')
            return value
        if args.operation == 'status':
            require(type(value) is dict and set(value) == {'status', 'attemptPresent', 'wrappedResponsePresent',
                'automaticReplayAllowed', 'productionReady'} and value['status'] == 'read_only'
                and type(value['attemptPresent']) is bool and type(value['wrappedResponsePresent']) is bool
                and value['automaticReplayAllowed'] is False and value['productionReady'] is False,
                'transit_status_public_shape')
            return value
        binding = {'attemptId': args.attempt_id, 'contract': public_contract, 'automaticReplayAllowed': False}
        require(type(value) is dict and set(value) == {'status', 'binding', 'response'}
                and value['status'] == 'wrapped_pending_custody' and value['binding'] == binding,
                'transit_wrapped_binding')
        derived = contract_bridge(args.contract_python, {'coordinate': supplied_coordinate, 'response': value['response']})
        require({key: derived.get(key) for key in public_contract} == public_contract
                and set(derived) == set(public_contract) | {'pinProposal', 'registryEntry'}, 'transit_derived_shape')
        # Original ciphertext first, independent-review proposal second, entry last.
        store_or_verify(descriptor, 'original-wrapped-response.json', value)
        store_or_verify(descriptor, 'pin-proposal.json', derived['pinProposal'])
        store_or_verify(descriptor, 'registry-entry.json', derived['registryEntry'])
        require(custody.vault_identity(args.vault_mount, args.vault_binding) == identity, 'transit_custody_changed')
        return {'status': 'wrapped_response_custodied', 'purpose': args.purpose,
            'applicationKeyVersion': args.application_key_version, 'transitKeyVersion': 1,
            'originalResponseFsyncReadback': True, 'pinProposalFromOriginalResponse': True,
            'independentDatabasePinVerified': False, 'registryInstalled': False, 'plaintextDataKeyRequested': False,
            'automaticReplayAllowed': False, 'productionReady': False,
            'remoteSourceSha256': hashlib.sha256(script.encode()).hexdigest()}
    finally:
        if descriptor is not None: os.close(descriptor)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('keys', 'generate', 'recover', 'status'))
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--purpose', choices=PURPOSES, required=True)
    parser.add_argument('--application-key-version', type=int, required=True)
    parser.add_argument('--contract-python', type=Path, required=True)
    parser.add_argument('--attempt-id', required=True)
    parser.add_argument('--ssh-config', type=Path, required=True); parser.add_argument('--ssh-host', required=True)
    parser.add_argument('--ssh-sudo', action='store_true'); parser.add_argument('--container-id', required=True)
    parser.add_argument('--instance-id', required=True); parser.add_argument('--run-id', required=True)
    parser.add_argument('--vault-mount', type=Path, required=True); parser.add_argument('--vault-binding', type=Path, required=True)
    args = parser.parse_args(); custody.memory_policy(); os.umask(0o077)
    try:
        require(re.fullmatch(r'[0-9a-f]{12}', args.attempt_id) and re.fullmatch(r'[0-9a-f]{12}', args.run_id),
                'transit_operation_identifiers')
        client = custody.Remote(args.ssh_config, args.ssh_host, args.container_id, args.ssh_sudo, args.instance_id)
        result = execute(args, client)
    except BaseException as error:
        result = {'status': 'failed_or_unknown', 'safeCode': str(error) if isinstance(error, Rejected)
                  else 'transit_custody_or_transport_failed', 'automaticReplayAllowed': False,
                  'nextAction': 'read_exact_status_then_recover_existing_ciphertext_only', 'productionReady': False}
    print(json.dumps(result, sort_keys=True))
    return 0 if result['status'] in ('keys_verified', 'read_only', 'wrapped_response_custodied') else 1


if __name__ == '__main__':
    raise SystemExit(main())
