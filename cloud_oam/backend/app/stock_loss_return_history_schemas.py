"""Public historical fulfillment facts; never a present-stock or write promise."""
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import Field, model_validator

from .stock_loss_correction_http_schemas import Output, Quantity
from .formal_services.stock_loss_corrections.request_contracts import Digest, FactId

NonnegativeQuantity = Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=3)]
Stage = Literal['not_outbound', 'outbound_not_shipped', 'shipped_unconfirmed',
    'accepted_not_inbound', 'rejected', 'posted_inbound']
STAGES = ('not_outbound', 'outbound_not_shipped', 'shipped_unconfirmed',
    'accepted_not_inbound', 'rejected', 'posted_inbound')


class FulfillmentShare(Output):
    stage: Stage
    quantity: NonnegativeQuantity
    serial_ids: tuple[FactId, ...]


class ShortageObservation(Output):
    receipt_line_id: FactId
    quantity: Quantity


class FulfillmentLine(Output):
    operation_line_id: FactId
    original_quantity: Quantity
    shares: tuple[FulfillmentShare, ...]
    damaged_accepted_quantity: NonnegativeQuantity
    shortage_observations: tuple[ShortageObservation, ...]

    @model_validator(mode='after')
    def conserve(self):
        if tuple(s.stage for s in self.shares) != STAGES:
            raise ValueError('all disjoint fulfillment stages are required once')
        if sum((s.quantity for s in self.shares), Decimal(0)) != self.original_quantity:
            raise ValueError('historical shares do not conserve original quantity')
        accepted = sum((s.quantity for s in self.shares
            if s.stage in ('accepted_not_inbound', 'posted_inbound')), Decimal(0))
        if self.damaged_accepted_quantity > accepted:
            raise ValueError('damage is a subset of acceptance')
        serials = [identifier for share in self.shares for identifier in share.serial_ids]
        if len(serials) != len(set(serials)) or (serials and any(
                share.quantity != len(share.serial_ids) for share in self.shares)):
            raise ValueError('historical serial shares do not conserve')
        if len({row.receipt_line_id for row in self.shortage_observations}) != len(self.shortage_observations):
            raise ValueError('duplicate shortage observation')
        return self


class FulfillmentCoordinates(Output):
    outbounds: tuple[FactId, ...]
    shipments: tuple[FactId, ...]
    receipts: tuple[FactId, ...]
    inbounds: tuple[FactId, ...]


class ClassificationException(Output):
    inbound_id: FactId
    receipt_line_id: FactId
    inbound_line_id: FactId
    original_target_account_id: FactId
    recorded_condition: Literal['new', 'used']
    required_condition: Literal['damaged']
    affected_quantity: Quantity
    affected_serial_ids: tuple[FactId, ...]
    code: Literal['legacy_damaged_acceptance_classification']
    current_stock_verified: Literal[False]
    correction_authorized: Literal[False]


class LossReturnHistory(Output):
    schema_version: Literal['1.0'] = '1.0'
    result_scope: Literal['verified_loss_return_history'] = 'verified_loss_return_history'
    stock_effect: Literal['none'] = 'none'
    current_stock_verified: Literal[False] = False
    write_authorization_provided: Literal[False]
    root_disposition_id: FactId
    operation_id: FactId
    observed_ledger_cursor: int = Field(ge=1)
    evidence_fingerprint: Digest
    coordinates: FulfillmentCoordinates
    lines: tuple[FulfillmentLine, ...] = Field(min_length=1)
    classification_exceptions: tuple[ClassificationException, ...]

    @model_validator(mode='after')
    def exact_coordinates(self):
        if len({row.operation_line_id for row in self.lines}) != len(self.lines):
            raise ValueError('duplicate return line')
        for identifiers in self.coordinates.model_dump().values():
            if len(identifiers) != len(set(identifiers)):
                raise ValueError('duplicate fulfillment coordinate')
        if any(row.inbound_id not in self.coordinates.inbounds for row in self.classification_exceptions):
            raise ValueError('exception outside verified inbound history')
        if len({row.inbound_line_id for row in self.classification_exceptions}) != len(self.classification_exceptions):
            raise ValueError('duplicate classification exception')
        return self


def public_history(proof):
    """Explicit allowlist prevents stored commands or idempotency keys leaking."""
    return LossReturnHistory(
        root_disposition_id=proof.root_disposition_id, operation_id=proof.operation_id,
        observed_ledger_cursor=proof.observed_ledger_cursor,
        evidence_fingerprint=proof.evidence_fingerprint,
        write_authorization_provided=proof.write_authorization_provided,
        coordinates=dict(proof.coordinates),
        lines=[dict(operation_line_id=line.operation_line_id,
            original_quantity=line.original_quantity,
            shares=[dict(stage=s.stage, quantity=s.quantity, serial_ids=s.serial_ids) for s in line.shares],
            damaged_accepted_quantity=line.damaged_accepted_quantity,
            shortage_observations=[dict(receipt_line_id=identifier, quantity=amount)
                for identifier, amount in line.shortage_observations]) for line in proof.lines],
        classification_exceptions=[dict(inbound_id=row.inbound_id,
            receipt_line_id=row.receipt_line_id, inbound_line_id=row.inbound_line_id,
            original_target_account_id=row.original_target_account_id,
            recorded_condition=row.recorded_condition, required_condition=row.required_condition,
            affected_quantity=row.affected_quantity, affected_serial_ids=row.affected_serial_ids,
            code=row.code, current_stock_verified=row.current_stock_verified,
            correction_authorized=row.correction_authorized) for row in proof.classification_exceptions])
