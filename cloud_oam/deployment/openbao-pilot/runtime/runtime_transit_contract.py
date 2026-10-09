"""Production-coordinate bridge. Inputs never include root tokens or plaintext DEKs."""
import base64
import hashlib
import json
from pathlib import Path
import stat
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'backend'))
from app.openbao_transit_candidate import (PROVIDER, OpenBaoKeyCoordinate, OpenBaoWrappedKey,
    associated_data_b64, context_b64)
from app.openbao_registry_candidate import REGISTRY_SCHEMA, _parse_entries


def derive(value):
    if type(value) is not dict or set(value) not in ({'coordinate'}, {'coordinate', 'response'}):
        raise ValueError('closed_contract_shape')
    data = value['coordinate']
    if type(data) is not dict or set(data) != {'purpose', 'environment', 'provider_instance_id', 'application_key_version'}:
        raise ValueError('closed_coordinate_shape')
    coordinate = OpenBaoKeyCoordinate(**data)
    if coordinate.environment != 'production': raise ValueError('formal_production_coordinate_required')
    context, aad = context_b64(coordinate), associated_data_b64(coordinate)
    public = {'coordinate': data, 'keyPath': coordinate.key_path,
        'generatePath': coordinate.decrypt_path.replace('/decrypt/', '/datakey/wrapped/'),
        'request': {'bits': 256, 'key_version': 1, 'context': context, 'associated_data': aad}}
    if 'response' not in value: return public
    response = value['response']
    if type(response) is not dict or set(response) != {'ciphertext', 'key_version'} or type(response['key_version']) is not int:
        raise ValueError('wrapped_response_exact_fields')
    wrapped = OpenBaoWrappedKey(coordinate, response['ciphertext'], response['key_version'])
    if wrapped.transit_key_version != 1: raise ValueError('initial_transit_version_required')
    entry = {**data, 'key_path': coordinate.key_path, 'transit_key_version': wrapped.transit_key_version,
             'ciphertext': wrapped.ciphertext, 'context_b64': context, 'associated_data_b64': aad}
    _parse_entries(json.dumps({'schema': REGISTRY_SCHEMA, 'provider': PROVIDER, 'entries': [entry]}).encode())
    # This proposal comes directly from the original response BEFORE registry.
    # It is not an independently stored/reviewed/read-back database pin.
    proposal = {**data, 'provider': PROVIDER, 'transit_key_version': wrapped.transit_key_version,
        'ciphertext_sha256': hashlib.sha256(wrapped.ciphertext.encode('ascii')).hexdigest(),
        'context_sha256': hashlib.sha256(base64.b64decode(context)).hexdigest(),
        'associated_data_sha256': hashlib.sha256(base64.b64decode(aad)).hexdigest(),
        'origin': 'original_generation_response', 'independentDatabasePinVerified': False}
    return {**public, 'pinProposal': proposal, 'registryEntry': entry}


def main():
    if not all(stat.S_ISFIFO(__import__('os').fstat(n).st_mode) or stat.S_ISSOCK(__import__('os').fstat(n).st_mode)
               for n in (0, 1)): return 1
    try:
        raw = sys.stdin.buffer.readline(32769)
        if len(raw) > 32768 or not raw.endswith(b'\n'): raise ValueError('contract_bound')
        result = {'status': 'contract_verified', 'result': derive(json.loads(raw))}
    except BaseException:
        result = {'status': 'rejected', 'safeCode': 'production_coordinate_or_wrapped_response_invalid'}
    sys.stdout.buffer.write(json.dumps(result, sort_keys=True).encode() + b'\n'); sys.stdout.buffer.flush()
    return 0 if result['status'] == 'contract_verified' else 1


if __name__ == '__main__':
    raise SystemExit(main())
