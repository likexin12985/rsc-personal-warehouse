"""Observe real predecessor registries in a fresh, local-only test process.

Capture just before each production registration call. No reverse-derived
expectations, historical acceptance flag or altered production verifier.
The pickle is exclusively the output of this fixed child program, not a file
or external artifact supplied by a user.
"""
from copy import deepcopy
from functools import cache
from pathlib import Path
import pickle
import subprocess
import sys

PROGRAM = '''
from copy import deepcopy
from types import SimpleNamespace
import pickle, sys
from app import stock_scrap_security as scrap, stock_scrap_readiness as ready
from app import return_condition_security as condition
captured = {}
def intercept(module, stage):
    original = module.register
    def register(namespace):
        scope = {key: deepcopy(value) for key, value in namespace.items()
                 if not key.startswith('__') and isinstance(value, (dict, set, frozenset))}
        for key in ('_loss_correction_catalog', '_loss_return_stop_catalog', '_authentication_fence_catalog', '_stock_scrap_catalog'):
            catalog = namespace[key]
            scope[key] = SimpleNamespace(DATA=deepcopy(catalog.DATA), TABLES=getattr(catalog, 'TABLES', ()))
        captured[stage] = scope
        original(namespace)
    module.register = register
intercept(scrap, 'scrap')
intercept(ready, 'readiness')
intercept(condition, 'condition')
from app import database_security
sys.stdout.buffer.write(pickle.dumps(captured))
'''


@cache
def _captures():
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run([sys.executable, '-c', PROGRAM], cwd=root,
                            capture_output=True, check=True, timeout=30)
    return pickle.loads(result.stdout)


def predecessor(stage):
    return deepcopy(_captures()[stage])
