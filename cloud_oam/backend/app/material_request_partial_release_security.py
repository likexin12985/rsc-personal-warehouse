"""Exact 0170 function/readiness overlay; no runtime migration execution."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path

RAW=Path(__file__).with_suffix('.json').read_bytes()
if sha256(RAW).hexdigest()!='21bb7895960dc8e913fa0f9a3efbd6fed59d1628da81ffd70008b5c6a2304d87':
    raise ValueError('0170 runtime function catalog changed')
DATA=json.loads(RAW)


def overlay(previous):
    ready=DATA['readiness']
    if previous.DATA['after']!=ready['before'] or previous.DATA['afterSha256']!=ready['beforeSha256']:
        raise ValueError('0170 exact readiness predecessor required')
    updated=deepcopy(previous.DATA)
    updated.update(after=deepcopy(ready['after']),afterSha256=ready['afterSha256'],revision=DATA['revision'])
    previous.DATA=updated


def register(namespace):
    hashes=namespace['MATERIAL_REQUEST_APPROVAL_FUNCTION_BODY_SHA256']
    for signature,row in DATA['functions'].items():
        name,args=signature[:-1].split('(',1)
        if name=='rsc_oam_runtime_binding_ready_0044':
            continue
        if hashes.get((name,args))!=row['beforeSha256']:
            raise ValueError('0170 exact release guard predecessor required')
        hashes[(name,args)]=row['afterSha256']
