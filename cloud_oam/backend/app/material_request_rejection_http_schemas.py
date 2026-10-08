"""Public, current-recipient views of immutable rejection-return facts."""
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import Field, StrictInt, model_validator
from .material_request_my_receipt_schemas import StrictModel
from .material_request_my_inbound_candidates_schemas import InboundCandidateDetailOut, InboundSerialOut
from .material_request_remainder_schemas import Quantity
from .material_request_rejection_return_schemas import RejectionReturnOut
from .material_request_rejection_progress_schemas import RejectionProgressOut, RejectionProgressStateOut

Action = Literal['cancel_registration', 'depart', 'handover']


class RejectionReturnHistoryOut(StrictModel):
    registration: RejectionReturnOut
    progress: RejectionProgressStateOut
    permitted_actions: tuple[Action, ...]

    @model_validator(mode='after')
    def bound_history(self):
        r, p = self.registration, self.progress
        allowed = {'registered': {'cancel_registration', 'depart'}, 'departed': {'handover'},
                   'cancelled': set(), 'handed_over': set()}[p.status]
        if (p.return_id != r.return_id or p.request_id != r.request_id
                or p.registration_request_hash != r.request_hash
                or len(set(self.permitted_actions)) != len(self.permitted_actions)
                or not set(self.permitted_actions) <= allowed):
            raise ValueError('return history and available actions mismatch')
        return self


class RejectionReturnLineOut(StrictModel):
    receipt_line_id: UUID
    available_qty: Quantity
    available_serials: tuple[InboundSerialOut, ...]
    register_permitted: bool
    registrations: tuple[RejectionReturnHistoryOut, ...] = Field(max_length=1000)


class RejectionReturnCandidateOut(StrictModel):
    receipt_id: UUID
    status: Literal['verified', 'blocked']
    message: str
    detail: InboundCandidateDetailOut | None
    lines: tuple[RejectionReturnLineOut, ...]

    @model_validator(mode='after')
    def bound_sources(self):
        if self.status == 'blocked':
            if self.detail is not None or self.lines:
                raise ValueError('blocked source must not expose unverified facts')
            return self
        if self.detail is None:
            raise ValueError('verified receipt detail required')
        original = {line.receipt_line_id: line for line in self.detail.lines if Decimal(line.rejected_qty) > 0}
        if len({line.receipt_line_id for line in self.lines}) != len(self.lines) or set(original) != {line.receipt_line_id for line in self.lines}:
            raise ValueError('return sources must cover the exact rejected lines')
        for line in self.lines:
            source = original[line.receipt_line_id]
            active = [h.registration for h in line.registrations if h.progress.status != 'cancelled']
            if (any(h.registration.receipt_id != self.receipt_id or h.registration.receipt_line_id != line.receipt_line_id
                    or h.registration.receipt_request_hash != self.detail.receipt_request_hash for h in line.registrations)
                    or len({h.registration.return_id for h in line.registrations}) != len(line.registrations)
                    or sum((Decimal(h.quantity) for h in active), Decimal(0)) + Decimal(line.available_qty) != Decimal(source.rejected_qty)
                    or (line.register_permitted and Decimal(line.available_qty) <= 0)):
                raise ValueError('return quantity or receipt binding mismatch')
            used = [sid for h in active for sid in h.serial_ids]
            expected = {s.serial_id for s in source.rejected_serials} - set(used)
            if len(set(used)) != len(used) or not set(used) <= {s.serial_id for s in source.rejected_serials}:
                raise ValueError('active return serials mismatch')
            if tuple(s for s in source.rejected_serials if s.serial_id in expected) != line.available_serials:
                raise ValueError('available return serials mismatch')
        return self


class RejectionReturnCandidatesOut(StrictModel):
    schema_version: Literal['1.0'] = '1.0'
    request_id: UUID
    request_version: StrictInt = Field(ge=0)
    items: tuple[RejectionReturnCandidateOut, ...] = Field(max_length=20)
    next_after_id: UUID | None


class RejectionReturnCommandStatusOut(StrictModel):
    lookup_status: Literal['confirmed', 'not_observed']
    command: RejectionReturnOut | None

    @model_validator(mode='after')
    def coherent(self):
        if (self.lookup_status == 'confirmed') != (self.command is not None):
            raise ValueError('lookup status mismatch')
        return self


class RejectionProgressCommandStatusOut(StrictModel):
    lookup_status: Literal['confirmed', 'not_observed']
    command: RejectionProgressOut | None

    @model_validator(mode='after')
    def coherent(self):
        if (self.lookup_status == 'confirmed') != (self.command is not None):
            raise ValueError('lookup status mismatch')
        return self
