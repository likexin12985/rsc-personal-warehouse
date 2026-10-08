"""Bounded public history; quantities describe retained claims, not available stock."""
from typing import Literal

from pydantic import AwareDatetime, Field, model_validator

from .return_condition_http_schemas import ConditionFact, ConditionStatus, NonnegativeQuantity
from .stock_loss_correction_http_schemas import Output, Quantity
from .formal_services.stock_loss_corrections.request_contracts import Digest, FactId
from .return_condition_schema import TRANSITIONS


class ConditionTimelineEvent(Output):
    sequence: int = Field(strict=True, ge=1)
    occurred_at: AwareDatetime
    previous_event_id: FactId | None
    evidence_file_ids: list[FactId]
    fact: ConditionFact


class ConditionCaseSummary(Output):
    case_id: FactId
    status: ConditionStatus
    quantity: Quantity
    serial_ids: list[FactId]
    latest_event_id: FactId
    latest_event_hash: Digest


class ConditionMaterialSerial(Output):
    serial_id: FactId
    serial_no: str = Field(strict=True, min_length=1, max_length=200)
    qr_code: str = Field(strict=True, min_length=1, max_length=250)


class ConditionCaseHistory(Output):
    schema_version: Literal['condition_history/1']
    result_scope: Literal['verified_condition_history']
    inbound_line_id: FactId
    root_disposition_id: FactId
    material_id: FactId
    sku_code: str = Field(strict=True, min_length=1, max_length=80)
    material_name: str = Field(strict=True, min_length=1, max_length=200)
    base_unit: str = Field(strict=True, min_length=1, max_length=32)
    serials: list[ConditionMaterialSerial]
    source_account_id: FactId
    recorded_condition: Literal['new', 'used']
    required_condition: Literal['damaged']
    historical_damaged_quantity: Quantity
    held_quantity: NonnegativeQuantity
    corrected_quantity: NonnegativeQuantity
    unclaimed_quantity: NonnegativeQuantity
    cases: list[ConditionCaseSummary]
    events: list[ConditionTimelineEvent]
    observed_ledger_cursor: int = Field(strict=True, ge=1)
    history_fingerprint: Digest
    current_stock_verified: Literal[False]
    write_authorization_provided: Literal[False]
    retry_allowed: Literal[False]
    stock_effect: Literal['none']

    @model_validator(mode='after')
    def complete_public_graph(self):
        serial_ids = {s.serial_id for s in self.serials}
        if len(serial_ids) != len(self.serials) or (self.serials and len(serial_ids) != self.historical_damaged_quantity):
            raise ValueError('historical serial identities disagree with quantity')
        if self.held_quantity + self.corrected_quantity + self.unclaimed_quantity != self.historical_damaged_quantity:
            raise ValueError('historical shares do not conserve the original quantity')
        cases = {c.case_id: c for c in self.cases}
        if len(cases) != len(self.cases) or len(self.events) > 20000:
            raise ValueError('duplicate or oversized history')
        latest = {}; seen = set()
        for sequence, event in enumerate(self.events, 1):
            fact = event.fact; case = cases.get(fact.case_id); prior = latest.get(fact.case_id)
            if (event.sequence != sequence or fact.event_id in seen or case is None
                    or fact.inbound_line_id != self.inbound_line_id or fact.quantity != case.quantity
                    or event.previous_event_id != (prior.fact.event_id if prior else None)
                    or (fact.action, prior.fact.status if prior else 'draft', fact.status) not in TRANSITIONS
                    or (prior is not None and event.occurred_at < prior.occurred_at)
                    or len(set(event.evidence_file_ids)) != len(event.evidence_file_ids)):
                raise ValueError('incomplete or contradictory public event history')
            seen.add(fact.event_id); latest[fact.case_id] = event
        for case in self.cases:
            end = latest.get(case.case_id)
            if (end is None or (end.fact.event_id, end.fact.request_hash, end.fact.status) !=
                    (case.latest_event_id, case.latest_event_hash, case.status)
                    or len(set(case.serial_ids)) != len(case.serial_ids)
                    or (case.serial_ids and len(case.serial_ids) != case.quantity)
                    or not set(case.serial_ids).issubset(serial_ids)
                    or (bool(case.serial_ids) != bool(self.serials))):
                raise ValueError('case summary differs from retained events')
        held = sum(c.quantity for c in self.cases if not c.status.startswith('released_') and c.status != 'executed')
        corrected = sum(c.quantity for c in self.cases if c.status == 'executed')
        if (held, corrected) != (self.held_quantity, self.corrected_quantity):
            raise ValueError('case outcomes differ from historical shares')
        return self


class ConditionReceiptHistory(Output):
    schema_version: Literal['condition_receipt_history/1']
    receipt_id: FactId
    shipment_id: FactId
    root_disposition_id: FactId
    operator_person_id: FactId
    authorization_version: int = Field(strict=True, ge=1)
    status: Literal['posted', 'not_posted']
    inbound_id: FactId | None
    items: list[ConditionCaseHistory] = Field(max_length=100)
    current_stock_verified: Literal[False] = False
    posting_allowed: Literal[False] = False

    @model_validator(mode='after')
    def exact_receipt(self):
        if ((self.status == 'posted') != (self.inbound_id is not None)
                or (self.status == 'not_posted' and self.items)
                or len({item.inbound_line_id for item in self.items}) != len(self.items)
                or any(item.root_disposition_id != self.root_disposition_id for item in self.items)):
            raise ValueError('receipt history coordinates do not match')
        return self


class ConditionInbox(Output):
    schema_version: Literal['condition_inbox/1'] = 'condition_inbox/1'
    person_id: FactId
    authorization_version: int = Field(strict=True, ge=1)
    view: Literal['pending', 'all']
    items: list[ConditionCaseHistory] = Field(max_length=10)
    next_after_id: FactId | None
    current_stock_verified: Literal[False] = False
    posting_allowed: Literal[False] = False

    @model_validator(mode='after')
    def bound_page(self):
        ids = [item.inbound_line_id for item in self.items]
        if ids != sorted(set(ids)) or (self.next_after_id is not None and (not ids or self.next_after_id != ids[-1])):
            raise ValueError('inbox page order or continuation mismatch')
        if any(not item.cases for item in self.items):
            raise ValueError('inbox item has no condition case')
        if self.view == 'pending' and any(all(c.status in ('executed', 'released_rejected', 'released_cancelled')
                for c in item.cases) for item in self.items):
            raise ValueError('pending inbox contains only completed cases')
        return self
