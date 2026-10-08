"""Pin the condition revision before existing readiness allowlists publish."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path

RAW = Path(__file__).with_suffix('.json').read_bytes()
if sha256(RAW).hexdigest() != '8d1fa9f0efd5a5ed8957a8e5979a132a0b333e12791b36eb82176ffc3be7fc2f':
    raise ValueError('0169 runtime readiness catalog changed')
DATA = json.loads(RAW)


def overlay(previous):
    expected = deepcopy(previous.DATA)
    if expected['after'] != DATA['before'] or expected['afterSha256'] != DATA['beforeSha256']:
        raise ValueError('0169 exact readiness predecessor required')
    expected['after'] = deepcopy(DATA['after'])
    expected['afterSha256'] = DATA['afterSha256']
    expected['revision'] = DATA['revision']
    previous.DATA = expected
