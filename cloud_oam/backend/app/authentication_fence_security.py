"""Pin the forward audit-domain fence without relaxing stock evidence."""
from copy import deepcopy
from pathlib import Path
import hashlib
import json

from .stock_loss_return_stop_security import verify_function

RAW = Path(__file__).with_suffix('.json').read_bytes()
if hashlib.sha256(RAW).hexdigest() != '6333d238c90cdc7cce651935053608aac895e686c7edc355ce72ab6aa13b386e':
    raise ValueError('0164 runtime authentication fence catalog changed')
DATA = json.loads(RAW)


def register(namespace):
    fence = DATA['patches'][0]
    coordinate = ('rsc_fence_loss_correction_seal_0161', '')
    registry = namespace['FORMAL_FILE_INTERNAL_FUNCTION_BODY_SHA256']
    if registry.get(coordinate) != fence['beforeSha256']:
        raise ValueError('0164 predecessor fence body changed')
    registry[coordinate] = fence['afterSha256']
    patches = {patch['before']['signature']: patch for patch in DATA['patches']}
    covered = set()
    for name in ('_loss_correction_catalog', '_loss_return_stop_catalog'):
        catalog = namespace[name]
        updated = deepcopy(catalog.DATA)
        for family in ('newFunctions', 'replacedFunctions'):
            for row in updated[family]:
                patch = patches.get(row['signature'])
                if patch is None:
                    continue
                pair = row['prosrc'], row['definition']
                if pair not in tuple((patch[k]['prosrc'], patch[k]['definition']) for k in ('before', 'after')):
                    raise ValueError('0164 full predecessor definition changed')
                row.update(patch['after'])
                if 'sha256' in row:
                    row['sha256'] = patch['afterSha256']
                if 'prosrcSha256' in row:
                    row['prosrcSha256'] = patch['afterSha256']
                covered.add(row['signature'])
        catalog.DATA = updated
    if covered != set(patches):
        raise ValueError('0164 predecessor catalog patch missing')


def verify(db):
    for patch in DATA['patches']:
        verify_function(db, patch['after'])
