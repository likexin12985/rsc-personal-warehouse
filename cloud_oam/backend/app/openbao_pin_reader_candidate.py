"""Read immutable DB bindings for the unregistered OpenBao candidate only.

The caller supplies a previously validated connection and independently
reviewed, exact historical coordinates. This module does not discover a DSN,
create a connection, select an active version, read a registry, or decrypt.
It executes one bounded SELECT and never controls the caller's transaction.

Typed pins prove shape and consistency, NOT independent/two-person review
provenance, database identity, permissions, immutable triggers or readiness.
Those remain caller prerequisites. The existing registry/candidate must still
verify these original DB hashes against ciphertext and canonical context/AAD.
No Settings/factory/readiness registration or production enablement exists.
"""
from __future__ import annotations

from datetime import datetime
from typing import Mapping

from sqlalchemy import text

from .openbao_transit_candidate import (
    PROVIDER,
    OpenBaoCandidateUnavailable,
    OpenBaoKeyCoordinate,
    OpenBaoReviewedPin,
)


class OpenBaoPinReaderCandidateError(OpenBaoCandidateUnavailable):
    """Fixed unavailable/unknown; not evidence of authentication failure."""


_FIELDS = frozenset({
    "requested_purpose", "requested_application_key_version",
    "purpose", "environment", "provider_instance_id", "key_path",
    "application_key_version", "transit_key_version", "ciphertext_sha256",
    "context_sha256", "associated_data_sha256", "created_at",
    "claim_purpose", "claim_application_key_version", "claim_provider",
    "claim_ciphertext_sha256", "claim_created_at",
    "legacy_purpose", "legacy_application_key_version",
})


def _reject() -> None:
    raise ValueError("invalid OpenBao candidate pins")


def _scope(environment, provider_instance_id, expected_coordinates):
    OpenBaoKeyCoordinate("authentication_idempotency", environment, provider_instance_id, 1)
    if type(expected_coordinates) is not tuple or not 1 <= len(expected_coordinates) <= 64:
        _reject()
    expected = {}
    for item in expected_coordinates:
        if type(item) is not OpenBaoKeyCoordinate:
            _reject()
        # Revalidate values instead of trusting an object's construction time.
        coordinate = OpenBaoKeyCoordinate(item.purpose, item.environment,
                                          item.provider_instance_id, item.application_key_version)
        identity = (coordinate.purpose, coordinate.application_key_version)
        if (coordinate.environment != environment
                or coordinate.provider_instance_id != provider_instance_id
                or identity in expected):
            _reject()
        expected[identity] = coordinate
    return expected


def _query(expected):
    # Only generated integer indexes enter SQL text; every supplied value is a
    # bind. Start from requested identities so absent pins cannot disappear.
    values = ", ".join(f"(:purpose_{index}, :version_{index})" for index in range(len(expected)))
    statement = text("""
WITH requested(purpose, application_key_version) AS (VALUES """ + values + """)
SELECT r.purpose AS requested_purpose,
       r.application_key_version AS requested_application_key_version,
       p.purpose, p.environment, p.provider_instance_id, p.key_path,
       p.application_key_version, p.transit_key_version, p.ciphertext_sha256,
       p.context_sha256, p.associated_data_sha256, p.created_at,
       c.purpose AS claim_purpose,
       c.application_key_version AS claim_application_key_version,
       c.provider AS claim_provider, c.ciphertext_sha256 AS claim_ciphertext_sha256,
       c.created_at AS claim_created_at, l.purpose AS legacy_purpose,
       l.application_key_version AS legacy_application_key_version
  FROM requested r
  LEFT JOIN public.openbao_data_key_pins p
    ON p.purpose = r.purpose AND p.application_key_version = r.application_key_version
  LEFT JOIN public.application_key_version_claims c
    ON c.purpose = r.purpose AND c.application_key_version = r.application_key_version
  LEFT JOIN public.kms_data_key_pins l
    ON l.purpose = r.purpose AND l.application_key_version = r.application_key_version
 LIMIT :row_limit
""")
    parameters = {"row_limit": len(expected) + 1}
    for index, (purpose, version) in enumerate(expected):
        parameters[f"purpose_{index}"] = purpose
        parameters[f"version_{index}"] = version
    return statement, parameters


def _timestamp(value):
    return type(value) is datetime and value.tzinfo is not None and value.utcoffset() is not None


def _pins(rows, expected):
    if len(rows) != len(expected):
        _reject()
    found = {}
    fingerprints = set()
    for row in rows:
        if not isinstance(row, Mapping) or set(row) != _FIELDS:
            _reject()
        identity = (row["requested_purpose"], row["requested_application_key_version"])
        if (type(identity[0]) is not str or type(identity[1]) is not int
                or identity not in expected or identity in found):
            _reject()
        coordinate = OpenBaoKeyCoordinate(row["purpose"], row["environment"],
                                          row["provider_instance_id"], row["application_key_version"])
        if (coordinate != expected[identity] or type(row["key_path"]) is not str
                or row["key_path"] != coordinate.key_path):
            _reject()
        # Preserve DB values as the expected pins; never rebuild them from a
        # local registry or a mutable remote decrypt response.
        pin = OpenBaoReviewedPin(coordinate, row["transit_key_version"],
                                 row["ciphertext_sha256"], row["context_sha256"],
                                 row["associated_data_sha256"])
        if (
            type(row["claim_purpose"]) is not str or row["claim_purpose"] != coordinate.purpose
            or type(row["claim_application_key_version"]) is not int
            or row["claim_application_key_version"] != coordinate.application_key_version
            or type(row["claim_provider"]) is not str or row["claim_provider"] != PROVIDER
            or type(row["claim_ciphertext_sha256"]) is not str
            or row["claim_ciphertext_sha256"] != pin.ciphertext_sha256
            or not _timestamp(row["created_at"]) or not _timestamp(row["claim_created_at"])
            or row["claim_created_at"] != row["created_at"]
            or row["legacy_purpose"] is not None or row["legacy_application_key_version"] is not None
            or pin.ciphertext_sha256 in fingerprints
        ):
            _reject()
        found[identity] = pin
        fingerprints.add(pin.ciphertext_sha256)
    return tuple(found[identity] for identity in expected)


def read_openbao_reviewed_pins_candidate(
    db,
    *,
    environment: str,
    provider_instance_id: str,
    expected_coordinates: tuple[OpenBaoKeyCoordinate, ...],
) -> tuple[OpenBaoReviewedPin, ...]:
    """Read exactly 1..64 independently supplied coordinates in one SELECT.

    The supplied connection must already have the required DB identity,
    read-only permissions, timeout and logging controls. This helper does not
    infer any such guarantees from successful SQL or from typed return values.
    It neither commits/rolls back nor retries; failure invalidates the entire
    read. Missing historical versions never fall back to a newer version.
    """
    pins = None
    try:
        expected = _scope(environment, provider_instance_id, expected_coordinates)
        statement, parameters = _query(expected)
        result = db.execute(statement, parameters)
        try:
            # Bound both the SQL result and client-side consumption. One extra
            # row detects ambiguity without materializing an unbounded result.
            rows = result.mappings().fetchmany(len(expected) + 1)
        finally:
            result.close()
        pins = _pins(rows, expected)
    except Exception:
        pass
    if pins is None:
        # Raise outside the handler: no SQL/parameters/driver error is retained
        # in __context__ or __cause__, including errors while closing a cursor.
        raise OpenBaoPinReaderCandidateError("OpenBao candidate pins are unavailable")
    return pins
