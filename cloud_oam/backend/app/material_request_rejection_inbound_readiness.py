"""Pin warehouse posting before existing readiness allowlists publish."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path

RAW = Path(__file__).with_suffix('.json').read_bytes()
if sha256(RAW).hexdigest() != '3d6279bf088a423515cd0ceca0d61b236d60211a9d230a747cff28c2dfae9a4f':
    raise ValueError('0175 runtime readiness catalog changed')
DATA = json.loads(RAW)


def overlay(previous):
    expected = deepcopy(previous.DATA)
    if expected['after'] != DATA['before'] or expected['afterSha256'] != DATA['beforeSha256']:
        raise ValueError('0175 exact readiness predecessor required')
    expected['after'] = deepcopy(DATA['after'])
    expected['afterSha256'] = DATA['afterSha256']
    expected['revision'] = DATA['revision']
    previous.DATA = expected
