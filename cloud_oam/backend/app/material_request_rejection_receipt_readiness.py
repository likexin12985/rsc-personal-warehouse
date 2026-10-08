"""Pin warehouse acceptance before existing readiness allowlists publish."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path

RAW = Path(__file__).with_suffix('.json').read_bytes()
if sha256(RAW).hexdigest() != 'cdd73d27f60a4ee30c40e6d8b7ae1759b1be8b05f3fab8b331fd8082122fb447':
    raise ValueError('0174 runtime readiness catalog changed')
DATA = json.loads(RAW)


def overlay(previous):
    expected = deepcopy(previous.DATA)
    if expected['after'] != DATA['before'] or expected['afterSha256'] != DATA['beforeSha256']:
        raise ValueError('0174 exact readiness predecessor required')
    expected['after'] = deepcopy(DATA['after'])
    expected['afterSha256'] = DATA['afterSha256']
    expected['revision'] = DATA['revision']
    previous.DATA = expected
