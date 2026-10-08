"""0180 additive key registry readiness; exact read-only runtime expectations."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path


RAW = Path(__file__).with_suffix('.json').read_bytes()
if sha256(RAW).hexdigest() != '39f4a03998a528a5dd9a0e5314e783b9bd9f3ccb14a364fe8014f97a048d4109':
    raise ValueError('0180 runtime readiness catalog changed')
DATA = json.loads(RAW)


def overlay(previous):
    """Advance only the exact 0179 function, preserving its independent ACL."""
    ready = DATA['readiness']
    if (previous.DATA['revision'] != DATA['previousRevision']
            or previous.DATA['after'] != ready['before']
            or previous.DATA['afterSha256'] != ready['beforeSha256']):
        raise ValueError('0180 exact readiness predecessor required')
    updated = deepcopy(previous.DATA)
    updated.update(after=deepcopy(ready['after']),
                   afterSha256=ready['afterSha256'], revision=DATA['revision'])
    previous.DATA = updated
