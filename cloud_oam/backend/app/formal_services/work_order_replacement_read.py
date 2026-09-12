"""Read an original replacement result from both immutable stock transactions."""
from dataclasses import replace
import hashlib
import re
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select

from ..demand_models import WorkOrderMaterialLine, WorkOrderMaterialOperation, WorkOrderMaterialSerial, WorkOrderReplacementPair
from ..foundation_models import AuditEvent, OutboxEvent
from ..inventory_models import InventoryMovement, InventoryTransaction
from ..work_order_material_schemas import WorkOrderMaterialLineIn, WorkOrderReplacementRecoverLineIn, WorkOrderReplacementPairIn, WorkOrderReplacementOut
from . import work_order_material as material


def _invalid():
    raise material.WorkOrderMaterialPreflightError(
        "replacement_evidence_invalid", "原替换的消耗、回收或配对证据不一致，请核验原记录", "service_unavailable")


def _unique_evidence(db, model, **values):
    return len(tuple(db.scalars(select(model.id).filter_by(**values).limit(2)))) == 1


def replacement_result(db, *, replacement, actor):
    """No locks, writes or reliance on the SN's later mutable location/status."""
    from .work_order_replacements import RecoveryLineInput, _hash, child_key, replacement_request_payload, resolve_recovery_lines
    try:
        value = replacement.command_jsonb
        if (not isinstance(value, dict) or set(value) != {"work_order_id", "operator_person_id", "consume_lines", "recover_lines", "replacement_pairs"}
                or value["work_order_id"] != str(replacement.oam_work_order_id)
                or value["operator_person_id"] != str(replacement.operator_person_id)
                or actor.person_id != replacement.operator_person_id
                or not re.fullmatch(r"[0-9a-f]{64}", replacement.idempotency_key_hash)
                or not re.fullmatch(r"[A-Za-z0-9._:-]{8,160}", replacement.request_id)
                or replacement.replacement_no != "WOR-" + replacement.idempotency_key_hash[:24].upper()
                or not (1 <= len(value["consume_lines"]) <= 100 and 1 <= len(value["recover_lines"]) <= 100)):
            _invalid()
        # Stored consume intent includes the canonical null target field.
        consume = []
        for row in value["consume_lines"]:
            if row.get("target_stock_account_id") is not None or "target_stock_account_id" not in row:
                _invalid()
            parsed = WorkOrderMaterialLineIn.model_validate({key:item for key,item in row.items() if key != "target_stock_account_id"})
            values = parsed.model_dump()
            values["serial_verifications"] = tuple(material.SerialVerificationInput(**proof) for proof in values["serial_verifications"])
            consume.append(material.WorkOrderMaterialLineInput(**values))
        recover = []
        for row in value["recover_lines"]:
            values = WorkOrderReplacementRecoverLineIn.model_validate(row).model_dump()
            values["serial_verifications"] = tuple(material.SerialVerificationInput(**proof) for proof in values["serial_verifications"])
            recover.append(RecoveryLineInput(**values))
        pairs = tuple(material.WorkOrderReplacementPairInput(**WorkOrderReplacementPairIn.model_validate(row).model_dump())
                      for row in value["replacement_pairs"])
        command = replacement_request_payload(work_order_id=replacement.oam_work_order_id,
            operator_person_id=replacement.operator_person_id,consume_lines=tuple(consume),recover_lines=tuple(recover),pairs=pairs)
        if command != value or _hash(command) != replacement.request_hash:
            _invalid()
        recovered = resolve_recovery_lines(db,operator_person_id=replacement.operator_person_id,lines=tuple(recover),create=False)
        recovered = tuple(replace(line,target_stock_account_id=None) for line in recovered)
        expected_ids = (replacement.consume_operation_id,replacement.recover_operation_id)
        if len(set(expected_ids)) != 2 or set(db.scalars(select(WorkOrderMaterialOperation.id).where(
                WorkOrderMaterialOperation.replacement_id==replacement.id))) != set(expected_ids):
            _invalid()
        transactions = []
        for kind,identifier,lines in (("consume",expected_ids[0],tuple(consume)),("recover",expected_ids[1],recovered)):
            operation = db.get(WorkOrderMaterialOperation,identifier,populate_existing=True)
            transaction = db.get(InventoryTransaction,operation.posting_transaction_id,populate_existing=True) if operation else None
            digest = hashlib.sha256(child_key(replacement.idempotency_key_hash,kind).encode()).hexdigest()
            expected = material.operation_request_payload(operation_type=kind,work_order_id=replacement.oam_work_order_id,
                operator_person_id=replacement.operator_person_id,lines=lines)
            if (operation is None or transaction is None or operation.replacement_id != replacement.id
                    or operation.oam_work_order_id != replacement.oam_work_order_id or operation.operator_person_id != actor.person_id
                    or operation.status != "posted" or operation.operation_type != kind
                    or operation.idempotency_key_hash != digest or operation.request_hash != _hash(expected)
                    or transaction.status != "posted" or transaction.actor_user_id != actor.user_id
                    or transaction.source_document_type != "work_order_material" or transaction.source_document_id != str(replacement.oam_work_order_id)
                    or transaction.movement_type != {"consume":"consume","recover":"inbound"}[kind]
                    or transaction.posting_key != f"work-order-material:{kind}:{replacement.oam_work_order_id}:{digest}"):
                _invalid()
            material._verify_posted_lines(db,transaction=transaction,operation_type=kind,operator_person_id=actor.person_id,lines=lines)
            movements = tuple(db.scalars(select(InventoryMovement).where(InventoryMovement.transaction_id==transaction.id).order_by(InventoryMovement.line_no)))
            facts = tuple(db.scalars(select(WorkOrderMaterialLine).where(WorkOrderMaterialLine.operation_id==identifier).order_by(WorkOrderMaterialLine.line_no)))
            if len(facts) != len(lines) or len(movements) != len(lines):
                _invalid()
            for number,(fact,line,movement) in enumerate(zip(facts,lines,movements),1):
                serials = tuple(db.execute(select(WorkOrderMaterialSerial.serial_id,WorkOrderMaterialSerial.sku_verified,
                    WorkOrderMaterialSerial.qr_verified).where(WorkOrderMaterialSerial.operation_line_id==fact.id)))
                if (fact.line_no != number or fact.material_id != line.material_id or fact.stock_account_id != line.stock_account_id
                        or fact.quantity != line.quantity or fact.condition_before != line.condition_before or fact.condition_after is not None
                        or movement.line_no != number or movement.external_boundary_code != "work_order_material_"+kind
                        or (kind == "consume" and (movement.from_account_id != line.stock_account_id or movement.to_account_id is not None))
                        or (kind == "recover" and (movement.to_account_id != line.stock_account_id or movement.from_account_id is not None))
                        or {row[0] for row in serials} != set(line.serial_ids) or any(not row[1] or not row[2] for row in serials)):
                    _invalid()
            if not _unique_evidence(db,AuditEvent,stream_key="material_request",actor_user_id=actor.user_id,
                    action="work_order_material."+kind,aggregate_type="work_order_material_operation",aggregate_id=str(identifier),
                    request_id="work-order-material:"+digest,before_jsonb={},after_jsonb={"work_order_id":str(replacement.oam_work_order_id),
                        "operation_type":kind,"posting_transaction_id":str(transaction.id),"line_count":len(lines),
                        "request_hash":operation.request_hash,"command":expected}):
                _invalid()
            if not _unique_evidence(db,OutboxEvent,event_type="work_order_material_operation_posted",aggregate_type="work_order_material_operation",
                    aggregate_id=str(identifier),payload_jsonb={"work_order_id":str(replacement.oam_work_order_id),"operation_type":kind,
                        "operation_no":operation.operation_no,"posting_transaction_id":str(transaction.id)}):
                _invalid()
            transactions.append(transaction)
        actual_pairs = set(db.execute(select(WorkOrderReplacementPair.operation_id,WorkOrderReplacementPair.installed_serial_id,
            WorkOrderReplacementPair.removed_serial_id).where(WorkOrderReplacementPair.replacement_id==replacement.id)))
        if actual_pairs != {(expected_ids[0],pair.installed_serial_id,pair.removed_serial_id) for pair in pairs}:
            _invalid()
        links = {"work_order_id":str(replacement.oam_work_order_id),"consume_operation_id":str(expected_ids[0]),"recover_operation_id":str(expected_ids[1])}
        if (transactions[1].ledger_cursor != transactions[0].ledger_cursor + 1
                or not _unique_evidence(db,AuditEvent,stream_key="material_request",actor_user_id=actor.user_id,
                    action="work_order_material.replace",aggregate_type="work_order_material_replacement",aggregate_id=str(replacement.id),
                    request_id=replacement.request_id,before_jsonb={},after_jsonb={**links,"request_hash":replacement.request_hash,"command":command})
                or not _unique_evidence(db,OutboxEvent,event_type="work_order_material_replacement_posted",aggregate_type="work_order_material_replacement",
                    aggregate_id=str(replacement.id),payload_jsonb=links)):
            _invalid()
        return WorkOrderReplacementOut(replacement_id=replacement.id,replacement_no=replacement.replacement_no,
            work_order_id=replacement.oam_work_order_id,consume_operation_id=expected_ids[0],recover_operation_id=expected_ids[1],
            consume_transaction_id=transactions[0].id,recover_transaction_id=transactions[1].id)
    except (KeyError, TypeError, ValueError, AttributeError, ValidationError):
        _invalid()
