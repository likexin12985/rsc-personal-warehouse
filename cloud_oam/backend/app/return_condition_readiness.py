"""Pin the condition revision before existing readiness allowlists publish."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path

RAW = Path(__file__).with_suffix('.json').read_bytes()
if sha256(RAW).hexdigest() != '2432894c13542bdc64180038cb791217edab45d5b7b40f143f09127732420bfb':
    raise ValueError('0167 runtime readiness catalog changed')
DATA = json.loads(RAW)


def overlay(previous):
    expected = deepcopy(previous.DATA)
    if expected['after'] != DATA['before'] or expected['afterSha256'] != DATA['beforeSha256']:
        raise ValueError('0167 exact readiness predecessor required')
    expected['after'] = deepcopy(DATA['after'])
    expected['afterSha256'] = DATA['afterSha256']
    expected['revision'] = DATA['revision']
    previous.DATA = expected
