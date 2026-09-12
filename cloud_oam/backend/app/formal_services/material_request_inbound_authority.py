"""Transaction-bound authority for an engineer's exact accepted package.

This grants no source warehouse permission. The posting kernel rechecks the
receipt and canonical movement after taking its normal reference graph locks.
"""
from dataclasses import dataclass

from sqlalchemy import select

from ..inventory_models import InboundOrder, Receipt, StockAccount
from . import inventory_posting as inventory
from . import material_request_inbound as inbound
from . import material_request_my_receipt as receipts


_SEAL = object()


@dataclass(frozen=True, slots=True)
class _ReceiptInboundAuthority:
    session: object
    transaction: object
    actor: object
    request_id: object
    order_id: object
    receipt_id: object
    command_hash: str
    seal: object


def _deny():
    inventory._fail("personal_inbound_authority_invalid", "forbidden", "本人入账授权与当前验收或库存明细不一致")


def _validate(db, *, actor, request_id, order_id, command):
    context, request = receipts._context(db, actor, request_id)
    if context.principal != actor or not actor.allows(db, "material_request", "receive",
            target_scope_type="person", target_scope_id=str(actor.person_id)):
        _deny()
    order = db.get(InboundOrder, order_id, populate_existing=True)
    if order is None or order.status != "pending" or order.posting_transaction_id is not None:
        _deny()
    receipt = db.get(Receipt, order.receipt_id, populate_existing=True)
    if receipt is None:
        _deny()
    # Includes immutable original acceptance, original actor, request hash,
    # continuous version command, current custody and exact shipment history.
    result = receipts._result(db, context, request, receipt, replayed=True)
    shipment, _ = receipts._package(db, context, request, result.shipment_id)
    inbound._validate_inbound_target(shipment, order.target_location_id, order.target_person_id)
    expected = inbound._order_posting_command(db, order)
    if inventory._posting_document(command) != inventory._posting_document(expected):
        _deny()
    targets = tuple(sorted({move.to_account_id for move in expected.movements}, key=str))
    inventory._authorize_account_ids(db, actor, targets, action="receive",
        resource="material_request", lock_rows=False)
    ids = inventory._command_account_ids(expected)
    accounts = {row.id: row for row in db.scalars(select(StockAccount).where(
        StockAccount.id.in_(ids)).order_by(StockAccount.id))}
    if len(accounts) != len(ids):
        _deny()
    return receipt.id, accounts


def receipt_inbound_authority(db, *, actor, request_id, order_id, command):
    receipt_id, _ = _validate(db, actor=actor, request_id=request_id, order_id=order_id, command=command)
    transaction = db.get_transaction()
    if transaction is None:
        _deny()
    return _ReceiptInboundAuthority(db, transaction, actor, request_id, order_id,
        receipt_id, inventory._posting_request_hash(actor, command), _SEAL)


def require_receipt_authority(db, *, actor, command, proof):
    if (not isinstance(proof, _ReceiptInboundAuthority) or proof.seal is not _SEAL
            or proof.session is not db or proof.transaction is not db.get_transaction()
            or proof.actor != actor or proof.command_hash != inventory._posting_request_hash(actor, command)):
        _deny()
    receipt_id, accounts = _validate(db, actor=actor, request_id=proof.request_id,
        order_id=proof.order_id, command=command)
    if receipt_id != proof.receipt_id:
        _deny()
    return accounts
