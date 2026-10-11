"""Compatibility names for the reviewed 0182 runtime overlay.

The canonical source and digest live in :mod:`condition_notification_lock_security`.
This alias is retained for focused tests and callers that use the scope name.
"""

from .condition_notification_lock_security import (  # noqa: F401
    LEGACY_SHA256 as LEGACY_HASH,
    SUCCESSOR_SHA256 as FIXED_HASH,
    SEAL_LEGACY_SHA256 as SEAL_LEGACY_HASH,
    SEAL_SUCCESSOR_SHA256 as SEAL_FIXED_HASH,
    SIGNATURE,
    SEAL_SIGNATURE,
    successor_source,
    seal_successor_source,
    register,
)

COORDINATE = ('rsc_condition_decision_seal_lock', '')
SEAL_COORDINATE = ('rsc_condition_seal_lock', '')
FIXED_HASHES = {
    COORDINATE: FIXED_HASH,
    SEAL_COORDINATE: SEAL_FIXED_HASH,
}
