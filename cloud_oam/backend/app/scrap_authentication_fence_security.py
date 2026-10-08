"""Exact 0166 runtime overlays; no old-schema fallback or executable SQL."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path

RAW = Path(__file__).with_suffix('.json').read_bytes()
if sha256(RAW).hexdigest() != '412639a1b8a3f77ad071f959e135981ea3da5f9887d2752771a3ec36c4cacb43':
    raise ValueError('0166 runtime authentication fence catalog changed')
DATA = json.loads(RAW)


def overlay(stock_catalog, readiness_catalog):
    """Stage both exact 0165 replacements before publishing either one."""
    fence, ready = DATA['patches']
    stock = deepcopy(stock_catalog.DATA)
    readiness = deepcopy(readiness_catalog.DATA)
    key = fence['before']['signature']
    if stock['functions'][key]['after'] != fence['before']:
        raise ValueError('0166 exact scrap fence predecessor required')
    if readiness['after'] != ready['before'] or readiness['afterSha256'] != ready['beforeSha256']:
        raise ValueError('0166 exact readiness predecessor required')
    stock['functions'][key]['after'] = deepcopy(fence['after'])
    readiness['after'] = deepcopy(ready['after'])
    readiness['afterSha256'] = ready['afterSha256']
    readiness['revision'] = DATA['revision']
    stock_catalog.DATA, readiness_catalog.DATA = stock, readiness
