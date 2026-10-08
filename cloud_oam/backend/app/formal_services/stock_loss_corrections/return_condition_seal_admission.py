"""Read-only admission for closing an exact, still-unknown initial request.

This is not an absence certificate or a write permit. The eventual registrar
must repeat this proof under inventory/principal locks and enforce reciprocal
late-write fences at COMMIT. Call historical lookup first: a found result (or a
future existing seal) needs current read scope, not fresh action authority.
Nothing here admits a new stock action or verifies an unsaved physical scan.
"""
from dataclasses import dataclass, field
import json
from uuid import UUID

from app.formal_access import FormalPrincipal
from app.foundation_models import Organization
from app.return_condition_requests import validate_submit
from app.formal_services import inventory_posting as posting
from app.formal_services.stock_loss_planning import Account
from .historical_original import _bound
from .return_condition_contracts import Basis
from . import return_condition_authority as authority
from . import return_condition_coordinates as coordinates
from . import return_condition_history_read as history
from . import return_condition_keys as keys
from . import return_condition_request_inputs as inputs


@dataclass(frozen=True)
class SealScope:
    actor: FormalPrincipal
    inbound_id: UUID
    source: Account
    organization_path: tuple


@dataclass(frozen=True)
class SealAdmission:
    actor: FormalPrincipal
    action: str
    inbound_id: UUID
    basis: Basis
    original_input_json: str
    original_input_hash: str
    key_aliases: tuple
    condition_history_hash: str
    observed_boundary: tuple
    # Fixed, explicit limits: even an empty scan is not durable absence.
    absence_sealed: bool = field(default=False, init=False)
    retry_allowed: bool = field(default=False, init=False)
    current_stock_verified: bool = field(default=False, init=False)


def _current_scope(db, actor, inbound_line_id):
    current = authority._current(db, actor)
    current, line, source = history._scope(db, current, inbound_line_id)
    authority._permission(db, current, 'submit', source.owner_org_id)
    if current.person_id != source.custodian_person_id:
        authority._fail()
    # Current organization authority still matters. Current warehouse custody,
    # balance, location status and today's material policy do not grant or deny
    # closure of a historical coordinate; they belong to new stock actions.
    node = db.get(Organization, source.owner_org_id, populate_existing=True)
    if node is None or node.org_type != 'region_company':
        authority._fail()
    path = []
    seen = set()
    while node is not None:
        if node.id in seen or node.status != 'active':
            authority._fail()
        seen.add(node.id)
        path.append((node.id, node.parent_id, node.org_type, node.status))
        if node.parent_id is None:
            break
        node = db.get(Organization, node.parent_id, populate_existing=True)
        if node is None:
            authority._fail()
    account = Account(source.id, source.owner_org_id, source.custodian_person_id,
        source.location_id, source.material_id, source.condition_code,
        source.availability_bucket, source.lot_id)
    return SealScope(current, line.inbound_id, account, tuple(path))


def authorize_absence_seal(db, *, actor, request):
    """Prepare immutable inputs for a future registrar; never persist/retry.

    The full original command is retained as supplied (canonically ordered),
    including its old source hash and scans. Today's SKU/policy/available stock
    cannot reconstruct a missing preflight or certify old physical evidence.
    Existing results and any ambiguous residue must go through lookup instead.
    """
    request = validate_submit(request)
    document = inputs.canonical(request)
    with db.no_autoflush:
        scope = _current_scope(db, actor, request.inbound_line_id)
        before = _bound(db)
        observed = coordinates.capture(db, actor=scope.actor, request=request)
        coordinates.verify(observed)
        proved = history.read(db, actor=scope.actor, inbound_line_id=request.inbound_line_id)
        if proved.basis.source != scope.source:
            coordinates.unknown()
        fresh = coordinates.capture(db, actor=scope.actor, request=request)
        coordinates.verify(fresh)
        if fresh != observed:
            coordinates.unknown()
        # Recheck real identity/action/read scope after the long history scan.
        # No authorization result is cached or reused across requests.
        latest = _current_scope(db, scope.actor, request.inbound_line_id)
        if latest != scope or _bound(db) != before:
            coordinates.unknown()
        return SealAdmission(scope.actor, authority.ACTIONS['submit'], scope.inbound_id,
            proved.basis, json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(',', ':')),
            posting._canonical_hash(document), tuple(keys.aliases(request.idempotency_key).items()),
            proved.graph.fingerprint, before)
