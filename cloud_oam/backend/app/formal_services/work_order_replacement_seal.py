"""Resolve or seal one parent replacement; neither child alone is completion."""
import re

from . import work_order_command_seal as seals
from .inventory_posting import _require_current_actor
from .work_order_replacement_read import lookup_replacement


def lookup_replacement_result(db, *, actor, work_order_id, request_id):
    # Existing lookup verifies current read permission and both original stock
    # facts, independently of the work order's later assignment/status.
    original = lookup_replacement(db, actor=actor, work_order_id=work_order_id, request_id=request_id)
    current = _require_current_actor(db, actor)
    row = seals._row(db, actor=current, work_order_id=work_order_id, operation_type="replace", request_id=request_id)
    if row is None:
        _require_current_actor(db, current)
        return original
    if original is not None:
        seals._fail("work_order_seal_evidence_invalid", "原替换同时存在过账和封存记录，请保留记录核验", "service_unavailable")
    result = seals._verified_seal(db, actor=current, row=row)
    _require_current_actor(db, current)
    return result


def seal_replacement(db, *, actor, work_order_id, request_id, request_hash):
    if (not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9._:-]{8,160}", request_id)
            or not isinstance(request_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", request_hash)):
        seals._fail("work_order_seal_input_invalid", "原替换封存坐标无效", "invalid_request")
    current = seals._lock_request(db, actor=actor, work_order_id=work_order_id)
    original = lookup_replacement_result(db, actor=current, work_order_id=work_order_id, request_id=request_id)
    if original is not None:
        digest = original.seal.request_hash if hasattr(original, "seal") else original.request_hash
        if digest != request_hash:
            seals._fail("work_order_seal_request_conflict", "原替换已绑定其他内容，请保留恢复记录核验")
        return original
    return seals._write_seal(db, current=current, work_order_id=work_order_id,
        operation_type="replace", request_id=request_id, request_hash=request_hash)
