"""Collect the existing local recovery guards in the static CI shards.

The original unittest file remains usable as a standalone deployment check.
Its three inherited cases mock all native commands and disk preflight; CI
never creates a disk image, prompts for a password or accesses an external disk.
"""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


_SOURCE = Path(__file__).resolve().parents[2] / 'deployment/openbao-pilot/recovery-vault-tests.py'
_SPEC = spec_from_file_location('recovery_vault_standalone_guards', _SOURCE)
_GUARDS = module_from_spec(_SPEC)
_SPEC.loader.exec_module(_GUARDS)


class TestRecoveryVaultExistingObjectGuards(_GUARDS.ExistingObjectGuards):
    """Expose each unchanged standalone safety case exactly once to pytest."""
