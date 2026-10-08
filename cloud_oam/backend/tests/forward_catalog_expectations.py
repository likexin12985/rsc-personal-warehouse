"""Independently replay exact frozen full-object patches for historical tests.

Keep original catalog keys and before images; only a successor's matching full
before/after object can advance an after image. Never derive it from live DATA.
"""
from copy import deepcopy
import json
from pathlib import Path

from migration_source_expectations import _successor_paths


def current_catalog(revision, original):
    result = deepcopy(original)
    root = Path(__file__).resolve().parents[1] / 'alembic'
    for successor, _ in _successor_paths(revision):
        suffix = '_' + successor.rsplit('_', 1)[-1]
        paths = sorted(path for path in root.glob('*/*.json')
                       if path.parent.name.endswith(suffix)
                       and path.name in ('catalog.json', 'functions.json'))
        for path in paths:
            data = json.loads(path.read_text())
            families = {name: dict(data.get(name, {})) for name in ('tables', 'functions')}
            for patch in data.get('patches', []):
                if isinstance(patch.get('before'), dict):
                    families['functions'][patch['before']['signature']] = patch
            if 'barrier' in data:
                patch = data['barrier']
                families['functions'][patch['before']['signature']] = patch
            for family, changes in families.items():
                for name, patch in changes.items():
                    if name not in result.get(family, {}) or not isinstance(patch.get('before'), dict):
                        continue
                    assert isinstance(patch['after'], dict), (successor, name)
                    assert result[family][name]['after'] == patch['before'], (
                        successor, name, 'full catalog predecessor discontinuity')
                    result[family][name]['after'] = deepcopy(patch['after'])
    return result
