"""Read cursor for work-order facts that do not necessarily move inventory."""
from sqlalchemy import select
from ..foundation_models import AuditChainHead


def material_audit_cursor(db):
    return tuple(db.execute(select(AuditChainHead.version, AuditChainHead.last_event_id, AuditChainHead.last_hash)
        .where(AuditChainHead.stream_key == "material_request")))
