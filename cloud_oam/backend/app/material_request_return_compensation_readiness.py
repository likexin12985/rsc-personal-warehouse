"""Pin returned compensation before existing readiness allowlists publish."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path

RAW = Path(__file__).with_suffix('.json').read_bytes()
if sha256(RAW).hexdigest() != 'c564e6d316f1620d941cb6f183813af0d4fd7cff6a19dfd80fe4358cd5a97454':
    raise ValueError('0176 runtime readiness catalog changed')
DATA = json.loads(RAW)


def overlay(previous):
    expected = deepcopy(previous.DATA)
    if expected['after'] != DATA['before'] or expected['afterSha256'] != DATA['beforeSha256']:
        raise ValueError('0176 exact readiness predecessor required')
    expected['after'] = deepcopy(DATA['after'])
    expected['afterSha256'] = DATA['afterSha256']
    expected['revision'] = DATA['revision']
    previous.DATA = expected
