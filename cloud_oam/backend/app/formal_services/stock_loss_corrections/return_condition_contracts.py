"""Internal facts for historical inbound condition correction.

These are NOT HTTP commands, permission proofs or persistence models. Adapters
must prove the complete source, approval, ledger, audit and outbox graph under
locks. Neither a source-evidence JSON document nor a caller-created fact grants
authority to write. A case selects one immutable subset; changing that subset
requires release and a new case, never overwriting an old claim.
"""
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from app.formal_services.stock_loss_planning import Account, TrackedQuantity


@dataclass(frozen=True, slots=True)
class Basis:
    root_disposition_id: UUID
    inbound_line_id: UUID
    original_transaction_id: UUID
    original_movement_id: UUID
    original_ledger_cursor: int
    source: Account
    affected: TrackedQuantity


@dataclass(frozen=True, slots=True)
class Posting:
    transaction_id: UUID
    movement_id: UUID
    ledger_cursor: int
    from_account_id: UUID
    to_account_id: UUID
    quantity: Decimal
    serial_ids: tuple[UUID, ...]
    movement_type: str


@dataclass(frozen=True, slots=True)
class Claim:
    id: UUID
    case_id: UUID
    sequence: int
    inbound_line_id: UUID
    requester_user_id: str
    requester_person_id: UUID
    selected: TrackedQuantity
    frozen: Account
    posting: Posting
    reason: str
    evidence_file_ids: tuple[UUID, ...]


@dataclass(frozen=True, slots=True)
class Action:
    id: UUID
    case_id: UUID
    sequence: int
    kind: str
    actor_user_id: str
    actor_person_id: UUID
    reason: str
    evidence_file_ids: tuple[UUID, ...] = ()
    decision_id: UUID | None = None
    posting: Posting | None = None
    target: Account | None = None


@dataclass(frozen=True, slots=True)
class CaseState:
    case_id: UUID
    claim: Claim
    status: str
    regional_decision_id: UUID | None = None
    regional_reviewer_user_id: str | None = None
    regional_reviewer_person_id: UUID | None = None
    terminal_decision_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class Projection:
    cases: tuple[CaseState, ...]
    held_quantity: Decimal
    corrected_quantity: Decimal
    unclaimed_quantity: Decimal
    held_serial_ids: frozenset[UUID]
    corrected_serial_ids: frozenset[UUID]
    unclaimed_serial_ids: frozenset[UUID]
