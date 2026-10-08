"""Read-only forward readiness expectations shipped in the API image."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path

from .stock_loss_return_stop_security import verify_function

RAW = Path(__file__).with_suffix('.json').read_bytes()
if sha256(RAW).hexdigest() != '57b5c0faae11e542cc3920e2362da76bd22d168ce3654a1d3a479bd9324446e5':
    raise ValueError('0165 runtime readiness catalog digest mismatch')
DATA = json.loads(RAW)


def register(namespace):
    """Validate old independent verifiers before publishing forward bodies.

    The OAM manifest must already select the new digest before it builds its
    SQL. No old-schema production fallback or runtime SQL replacement exists.
    """
    coordinate = (DATA['after']['proname'], '')
    if namespace['OAM_SYNC_RUNTIME_FUNCTION_BODY_SHA256'].get(coordinate) != DATA['afterSha256']:
        raise ValueError('0165 forward OAM readiness manifest required')
    staged = {}
    for name in ('_loss_return_stop_catalog',):
        data = deepcopy(namespace[name].DATA)
        matches = [row for family in ('newFunctions', 'replacedFunctions') for row in data[family]
                   if row['signature'] == DATA['before']['signature']]
        if len(matches) != 1:
            raise ValueError('0165 predecessor readiness catalog entry required')
        row = matches[0]
        if any(row.get(key) != value for key, value in DATA['before'].items()):
            raise ValueError('0165 predecessor full readiness definition changed')
        row.update(deepcopy(DATA['after']))
        for digest in ('sha256', 'prosrcSha256'):
            if digest in row:
                row[digest] = DATA['afterSha256']
        staged[name] = data
    name = '_authentication_fence_catalog'
    data = deepcopy(namespace[name].DATA)
    matches = [patch for patch in data['patches'] if patch['after']['signature'] == DATA['before']['signature']]
    if len(matches) != 1 or matches[0]['after'] != DATA['before']:
        raise ValueError('0165 exact authentication readiness predecessor required')
    matches[0]['after'] = deepcopy(DATA['after'])
    matches[0]['afterSha256'] = DATA['afterSha256']
    staged[name] = data
    for name, data in staged.items():
        namespace[name].DATA = data


def verify(db):
    verify_function(db, DATA['after'])
