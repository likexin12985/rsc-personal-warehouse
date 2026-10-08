"""Exact 0168 forward function and readiness expectations."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path

ROOT = Path(__file__).parent
RAW = (ROOT / 'shipment_projection_functions.json').read_bytes()
if sha256(RAW).hexdigest() != '4d4507caf3377f8a3f57b490d24b333a8dbb5dc36d4badc73eee4e25f9d1cc0e':
    raise ValueError('0168 runtime function catalog changed')
FUNCTIONS = json.loads(RAW)['functions']
RAW = (ROOT / 'shipment_projection_readiness.json').read_bytes()
if sha256(RAW).hexdigest() != '829f6c80f8eacb1e299f8bb6e135af9bc7b155d97420cd7e084181a72187f772':
    raise ValueError('0168 runtime readiness catalog changed')
DATA = json.loads(RAW)


def overlay(previous):
    expected = deepcopy(previous.DATA)
    if expected['after'] != DATA['before'] or expected['afterSha256'] != DATA['beforeSha256']:
        raise ValueError('0168 exact readiness predecessor required')
    expected['after'] = deepcopy(DATA['after'])
    expected['afterSha256'] = DATA['afterSha256']
    expected['revision'] = DATA['revision']
    previous.DATA = expected


def register(namespace):
    hashes = namespace['MATERIAL_REQUEST_APPROVAL_FUNCTION_BODY_SHA256']
    for signature, row in FUNCTIONS.items():
        name, arguments = signature[:-1].split('(', 1)
        if name == 'rsc_oam_runtime_binding_ready_0044':
            continue
        coordinate = (name, arguments)
        if hashes.get(coordinate) != row['beforeSha256']:
            raise ValueError('0168 exact request guard predecessor required')
        hashes[coordinate] = row['afterSha256']
