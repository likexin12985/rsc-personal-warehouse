"""Pure loss-operation planning; never authorization or inventory posting.

Inputs are server-resolved immutable fact/snapshot values. They are not HTTP
models and must not be accepted from a client as trusted evidence. The service
adapter must lock/reload identity, material policy, location, ledger and command
facts, then call the existing atomic posting service. No app, DB, network or
storage imports are permitted here. No output is evidence that a write occurred.
"""
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from uuid import UUID


class ContractError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class DispositionKind(str, Enum):
    RESTORE = "restore_available"
    USED = "convert_used"
    DAMAGED = "convert_damaged"
    RETURN = "return_to_region"
    SCRAP = "scrap"


CONDITIONS = frozenset({"new", "used", "damaged"})
TRACKING = frozenset({"none", "lot", "serial", "lot_and_serial"})
MAX_QUANTITY = Decimal("1000000000000000")


def require_id(value):
    if not isinstance(value, UUID) or value.int == 0:
        raise ContractError("identifier_invalid")
    return value


def require_quantity(value, *, positive=True):
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ContractError("quantity_decimal_required")
    if value >= MAX_QUANTITY or value < 0 or (positive and value == 0) or value.as_tuple().exponent < -3:
        raise ContractError("quantity_out_of_range")
    return value


def require_reason(value):
    if (not isinstance(value, str) or not value.strip() or value != value.strip()
            or len(value) > 500 or any(ord(c) < 32 and c not in "\n\t" for c in value)):
        raise ContractError("reason_invalid")
    return value


def _significant_scale(value: Decimal) -> int:
    # Work on the exact coefficient: normalize() may round at the caller's
    # Decimal precision and erase a real fraction before policy validation.
    scale = max(0, -value.as_tuple().exponent)
    for digit in reversed(value.as_tuple().digits):
        if scale == 0 or digit != 0:
            break
        scale -= 1
    return scale


@dataclass(frozen=True, slots=True)
class Account:
    id: UUID
    owner_org_id: UUID
    custodian_person_id: UUID
    location_id: UUID
    material_id: UUID
    condition: str
    bucket: str
    lot_id: UUID | None = None

    def dimensions(self):
        for value in (self.id, self.owner_org_id, self.custodian_person_id, self.location_id, self.material_id):
            require_id(value)
        if self.lot_id is not None:
            require_id(self.lot_id)
        if self.condition not in CONDITIONS:
            raise ContractError("source_condition_invalid")
        return (self.owner_org_id, self.custodian_person_id, self.location_id, self.material_id, self.lot_id)


@dataclass(frozen=True, slots=True)
class TrackedQuantity:
    quantity: Decimal
    tracking_mode: str
    quantity_scale: int
    allow_fraction: bool
    serial_ids: tuple[UUID, ...] = ()

    def validate(self, lot_id):
        require_quantity(self.quantity)
        if self.tracking_mode not in TRACKING:
            raise ContractError("tracking_mode_invalid")
        if type(self.quantity_scale) is not int or not 0 <= self.quantity_scale <= 3 or type(self.allow_fraction) is not bool:
            raise ContractError("material_policy_invalid")
        if _significant_scale(self.quantity) > self.quantity_scale:
            raise ContractError("quantity_policy_scale")
        if not self.allow_fraction and self.quantity != self.quantity.to_integral_value():
            raise ContractError("quantity_fraction_forbidden")
        needs_lot = self.tracking_mode in {"lot", "lot_and_serial"}
        if needs_lot != (lot_id is not None):
            raise ContractError("tracking_lot_mismatch")
        if lot_id is not None:
            require_id(lot_id)
        if not isinstance(self.serial_ids, tuple):
            raise ContractError("serial_tuple_required")
        for value in self.serial_ids:
            require_id(value)
        if len(self.serial_ids) != len(set(self.serial_ids)):
            raise ContractError("serial_duplicate")
        needs_serials = self.tracking_mode in {"serial", "lot_and_serial"}
        if needs_serials:
            if self.quantity != Decimal(len(self.serial_ids)):
                raise ContractError("serial_quantity_mismatch")
        elif self.serial_ids:
            raise ContractError("untracked_serial_forbidden")
        return tuple(sorted(self.serial_ids, key=str))


@dataclass(frozen=True, slots=True)
class FreezeLine:
    line_id: UUID
    source: Account
    frozen: Account
    selected: TrackedQuantity
    available_quantity: Decimal
    source_serial_ids: frozenset[UUID]


@dataclass(frozen=True, slots=True)
class MovementOutline:
    line_id: UUID
    movement_type: str
    from_account_id: UUID
    to_account_id: UUID | None
    quantity: Decimal
    serial_ids: tuple[UUID, ...]
    external_boundary_code: str | None = None


def plan_freeze(*, requester_person_id: UUID, lines: tuple[FreezeLine, ...]):
    """Shape-only all-lines plan; locked live eligibility remains adapter-owned."""
    require_id(requester_person_id)
    if not lines or len(lines) > 100:
        raise ContractError("line_count_invalid")
    seen_lines, seen_accounts, seen_serials = set(), set(), set()
    moves = []
    for line in lines:
        require_id(line.line_id)
        if line.line_id in seen_lines or line.source.id in seen_accounts:
            raise ContractError("duplicate_source_line")
        seen_lines.add(line.line_id)
        seen_accounts.add(line.source.id)
        if (line.source.dimensions() != line.frozen.dimensions()
                or line.source.condition != line.frozen.condition or line.source.id == line.frozen.id):
            raise ContractError("freeze_account_dimensions")
        if line.source.bucket != "available" or line.frozen.bucket != "frozen":
            raise ContractError("freeze_account_buckets")
        if line.source.custodian_person_id != requester_person_id:
            raise ContractError("source_not_requester")
        require_quantity(line.available_quantity, positive=False)
        serials = line.selected.validate(line.source.lot_id)
        if line.selected.quantity > line.available_quantity:
            raise ContractError("source_insufficient")
        if not set(serials) <= line.source_serial_ids:
            raise ContractError("serial_not_at_source")
        if seen_serials.intersection(serials):
            raise ContractError("serial_reused_across_lines")
        seen_serials.update(serials)
        moves.append(MovementOutline(line.line_id, "freeze", line.source.id, line.frozen.id,
                                     line.selected.quantity, serials))
    return tuple(moves)


@dataclass(frozen=True, slots=True)
class ReviewAuthority:
    """Already resolved/current principal; never deserialize from HTTP JSON."""
    user_id: str
    person_id: UUID
    role: str
    allowed_actions: frozenset[str]  # Effective scoped rights AFTER deny evaluation.
    scope_org_ids: frozenset[UUID]
    national_scope: bool = False


def next_review_stage(*, current_stage: str, decision: str, actor: ReviewAuthority,
                      requester_user_id: str, requester_person_id: UUID, owner_org_id: UUID):
    """Return only an approval-axis transition. Never indicates stock posting."""
    require_id(actor.person_id)
    require_id(requester_person_id)
    require_id(owner_org_id)
    if not actor.user_id or not requester_user_id:
        raise ContractError("review_identity_invalid")
    if actor.person_id == requester_person_id or actor.user_id == requester_user_id:
        raise ContractError("self_approval_forbidden")
    if current_stage in {"awaiting_regional", "needs_evidence", "needs_regional_review"}:
        if (actor.role != "provincial_manager" or "review_loss_regional" not in actor.allowed_actions
                or owner_org_id not in actor.scope_org_ids):
            raise ContractError("regional_review_forbidden")
        targets = {"verified": "awaiting_headquarters", "needs_evidence": "needs_evidence"}
    elif current_stage == "awaiting_headquarters":
        if actor.role != "admin" or "finalize_loss" not in actor.allowed_actions or actor.national_scope is not True:
            raise ContractError("headquarters_review_forbidden")
        targets = {"approve": "approved", "request_regional_review": "needs_regional_review"}
    else:
        raise ContractError("review_stage_conflict")
    if decision not in targets:
        raise ContractError("review_decision_invalid")
    return targets[decision]


@dataclass(frozen=True, slots=True)
class HeldLine:
    """One persisted loss line, with its own remaining hold, not pooled balance."""
    line_id: UUID
    original: Account
    frozen: Account
    selected: TrackedQuantity
    remaining_quantity: Decimal
    remaining_serial_ids: frozenset[UUID]


@dataclass(frozen=True, slots=True)
class Disposition:
    line_id: UUID
    kind: DispositionKind
    reason: str
    target: Account | None = None
    return_operation_id: UUID | None = None
    scrap_operation_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class DispositionOutline:
    movement: MovementOutline
    source_document_type: str
    source_document_id: UUID
    lifecycle_after: str
    return_fulfillment_required: bool


def plan_dispositions(*, approval_fact_id: UUID, approval_stage: str,
                      disposition_fact_ids: dict[UUID, UUID], held_lines: tuple[HeldLine, ...],
                      decisions: tuple[Disposition, ...]):
    """Describe five outcomes against an approved immutable graph.

    approval_fact_id existence/binding, custody, true pending-line quantities,
    server target resolution and downstream absence MUST be proved by adapters
    and deferred DB guards. This function cannot establish that proof itself.
    """
    require_id(approval_fact_id)
    if approval_stage != "approved":
        raise ContractError("headquarters_approval_required")
    if not held_lines or len(held_lines) > 100:
        raise ContractError("line_count_invalid")
    by_id = {row.line_id: row for row in held_lines}
    wanted = {row.line_id for row in decisions}
    if len(by_id) != len(held_lines) or len(wanted) != len(decisions) or wanted != set(by_id):
        raise ContractError("decision_line_coverage")
    if set(disposition_fact_ids) != set(by_id) or len(set(disposition_fact_ids.values())) != len(by_id):
        raise ContractError("disposition_fact_binding")
    seen_serials, seen_children, result = set(), set(), []
    for decision in decisions:
        line = by_id[decision.line_id]
        require_id(line.line_id)
        require_id(disposition_fact_ids[line.line_id])
        require_reason(decision.reason)
        if not isinstance(decision.kind, DispositionKind):
            raise ContractError("disposition_kind_invalid")
        if (line.original.dimensions() != line.frozen.dimensions()
                or line.original.condition != line.frozen.condition
                or line.original.bucket != "available" or line.frozen.bucket != "frozen"
                or line.original.id == line.frozen.id):
            raise ContractError("freeze_account_dimensions")
        serials = line.selected.validate(line.original.lot_id)
        require_quantity(line.remaining_quantity, positive=False)
        if line.remaining_quantity != line.selected.quantity or set(serials) != line.remaining_serial_ids:
            raise ContractError("line_hold_not_exact")
        if seen_serials.intersection(serials):
            raise ContractError("serial_reused_across_lines")
        seen_serials.update(serials)
        expected_child = None
        if decision.kind == DispositionKind.SCRAP:
            if decision.target is not None or decision.return_operation_id is not None:
                raise ContractError("scrap_target_invalid")
            expected_child = require_id(decision.scrap_operation_id)
            movement_type, boundary, lifecycle = "scrap", "stock_operation_scrap", "scrapped"
            document_type, document_id = "stock_operation_scrap", expected_child
        else:
            if decision.scrap_operation_id is not None or decision.target is None:
                raise ContractError("disposition_target_invalid")
            if line.frozen.dimensions() != decision.target.dimensions() or decision.target.id == line.frozen.id:
                raise ContractError("disposition_account_dimensions")
            expected_condition = {DispositionKind.RESTORE: line.original.condition,
                                  DispositionKind.USED: "used", DispositionKind.DAMAGED: "damaged",
                                  DispositionKind.RETURN: line.original.condition}[decision.kind]
            expected_bucket = "return_pending" if decision.kind == DispositionKind.RETURN else "available"
            if decision.target.condition != expected_condition or decision.target.bucket != expected_bucket:
                raise ContractError("disposition_target_semantics")
            if decision.kind == DispositionKind.RETURN:
                expected_child = require_id(decision.return_operation_id)
                movement_type, document_type, document_id = "reserve", "stock_operation_return", expected_child
            else:
                if decision.return_operation_id is not None:
                    raise ContractError("unexpected_return_link")
                movement_type = "unfreeze" if decision.kind == DispositionKind.RESTORE else "status_change"
                document_type, document_id = "stock_loss_disposition", disposition_fact_ids[line.line_id]
            boundary, lifecycle = None, "active"
        if expected_child is not None:
            if expected_child in seen_children:
                raise ContractError("child_operation_reused")
            seen_children.add(expected_child)
        move = MovementOutline(line.line_id, movement_type, line.frozen.id,
                               decision.target.id if decision.target else None,
                               line.selected.quantity, serials, boundary)
        result.append(DispositionOutline(move, document_type, document_id, lifecycle,
                                         decision.kind == DispositionKind.RETURN))
    return tuple(result)


@dataclass(frozen=True, slots=True)
class Progress:
    approval_stage: str
    disposition_stage: str
    return_fulfillment_stage: str
    closed: bool


def derive_progress(*, approval_stage: str, expected_lines: frozenset[UUID],
                    posted_lines: frozenset[UUID], return_lines: frozenset[UUID],
                    completed_return_lines: frozenset[UUID], reversal_pending: bool = False):
    """Project verified facts only; notification or OAM receipt never enters."""
    if approval_stage not in {"awaiting_regional", "needs_evidence", "awaiting_headquarters",
                              "needs_regional_review", "approved"}:
        raise ContractError("approval_stage_invalid")
    if not expected_lines or not posted_lines <= expected_lines or not return_lines <= expected_lines:
        raise ContractError("progress_line_binding")
    if not completed_return_lines <= (posted_lines & return_lines):
        raise ContractError("return_completion_without_posting")
    if approval_stage != "approved" and (posted_lines or return_lines):
        raise ContractError("posting_before_approval")
    disposition_stage = ("not_approved" if approval_stage != "approved" else
                         "pending" if not posted_lines else
                         "complete" if posted_lines == expected_lines else "partial")
    return_stage = ("not_required" if not return_lines else
                    "complete" if completed_return_lines == return_lines else "pending")
    return Progress(approval_stage, disposition_stage, return_stage,
                    disposition_stage == "complete" and return_stage != "pending" and not reversal_pending)
