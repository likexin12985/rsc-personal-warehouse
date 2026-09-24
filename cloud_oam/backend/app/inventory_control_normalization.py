"""Deterministic, read-only control normalization review, never publication.

Condition rules are explicitly supplied review inputs, not approved mappings.
Formal master rows are observed from the database, not asserted by the caller.
Current published master evidence is re-proved separately from condition-rule approval.
The future publisher must repeat this inspection in its own transaction.
"""
from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal, localcontext
import re

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, ValidationError
from sqlalchemy import select

from . import inventory_control_authority as authority
from . import inventory_control_preparation as preparation
from .foundation_models import ExternalObject
from .inventory_models import FormalMaterial, MaterialInventoryPolicy
from .inventory_control_models import InventoryControlPreparation as Preparation
from .inventory_control_models import InventoryControlSourceBinding as Binding
from .inventory_control_models import InventoryControlCatalogVersion as Catalog
from .inventory_control_models import InventoryControlCaptureChain as Chain
from .inventory_control_evidence import _canonical, _sha, reconstruct_inventory_control_evidence
from .formal_services.opening_stocktake import opening_control_projection_payload


MAX_QUANTITY = Decimal('999999999999999.999')


class ControlNormalizationError(RuntimeError):
    pass


class ConditionRule(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    material_status: StrictStr | StrictInt
    material_stock_type: StrictStr | StrictInt
    condition_code: StrictStr


class NormalizationRules(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    revision: StrictStr = Field(min_length=1, max_length=80, pattern=r'^[A-Za-z0-9][A-Za-z0-9._-]*$')
    conditions: list[ConditionRule] = Field(max_length=100)


def _fail(code):
    raise ControlNormalizationError('control_normalization_' + code)


def _selector(status, stock_type):
    # JSON types matter: integer 1 and source string "1" are different codes.
    for value in (status, stock_type):
        if type(value) is str and (not value or value != value.strip() or len(value) > 80
                                  or any(ord(c) < 32 or ord(c) == 127 for c in value)):
            return None
        if type(value) not in (str, int) or (type(value) is int and not -2147483648 <= value <= 2147483647):
            return None
    return _canonical([status, stock_type])


def _rules(value):
    try:
        rules = NormalizationRules.model_validate(value)
    except ValidationError:
        _fail('invalid_rules')
    mapping = {}
    for rule in rules.conditions:
        key = _selector(rule.material_status, rule.material_stock_type)
        if key is None or key in mapping or rule.condition_code not in {'new', 'used', 'damaged', 'scrapped'}:
            _fail('invalid_rules')
        mapping[key] = rule.condition_code
    document = rules.model_dump()
    document['conditions'].sort(key=lambda row: _selector(row['material_status'], row['material_stock_type']))
    return document, mapping


def _quantity(value):
    if type(value) is int:
        if not 0 <= value <= 999999999999999:
            return None
        value = str(value)
    if type(value) is not str or not re.fullmatch(r'(?:0|[1-9][0-9]{0,14})(?:\.[0-9]{1,3})?', value):
        return None
    result = Decimal(value)
    return result if result <= MAX_QUANTITY else None


def _number(value):
    return format(value, '.3f')


def _masters(db, source_id, codes, now):
    """Observe exact source/SKU identity and current policy; do not guess names.

    These read locks protect returned mutable rows through inspection. They are
    not a policy-range lock and cannot qualify a publication transaction.
    """
    materials = tuple(db.scalars(select(FormalMaterial).where(FormalMaterial.sku_code.in_(codes))
        .order_by(FormalMaterial.id).with_for_update(read=True).execution_options(populate_existing=True))) if codes else ()
    external_ids = [row.external_object_id for row in materials]
    external = {row.id: row for row in db.scalars(select(ExternalObject).where(ExternalObject.id.in_(external_ids))
        .order_by(ExternalObject.id).with_for_update(read=True).execution_options(populate_existing=True))} if external_ids else {}
    identifiers = [row.id for row in materials]
    policies = defaultdict(list)
    if identifiers:
        for row in db.scalars(select(MaterialInventoryPolicy).where(MaterialInventoryPolicy.material_id.in_(identifiers))
                .order_by(MaterialInventoryPolicy.id).with_for_update(read=True).execution_options(populate_existing=True)):
            if preparation._aware(row.effective_from) <= now and (row.effective_to is None or now < preparation._aware(row.effective_to)):
                policies[row.material_id].append(row)
    result = {}
    for row in materials:
        obj = external.get(row.external_object_id)
        current = policies[row.id]
        issue = 'material_unavailable' if row.status != 'active' else \
            'material_source_mismatch' if obj is None or obj.source_system_id != source_id or obj.deleted_at is not None \
                or obj.entity_type != 'material' or obj.external_id != row.sku_code else \
            'policy_missing_or_ambiguous' if len(current) != 1 else None
        policy = current[0] if len(current) == 1 else None
        result[row.sku_code] = dict(material_id=str(row.id), external_object_id=str(row.external_object_id),
            sku_code=row.sku_code, base_unit=row.base_unit, issue=issue,
            policy_id=str(policy.id) if policy else None,
            quantity_scale=policy.quantity_scale if policy else None,
            allow_fraction=policy.allow_fraction if policy else None,
            tracking_mode=policy.tracking_mode if policy else None,
            effective_from=preparation._aware(policy.effective_from).isoformat() if policy else None,
            effective_to=preparation._aware(policy.effective_to).isoformat() if policy and policy.effective_to else None)
    return result


def normalize_records(*, records, target_positions, region_org_id, masters, rules):
    """Pure review kernel. No authentication or approval is inferred here."""
    with localcontext() as context:
        context.prec = 40
        return _normalize_records(records=records, target_positions=target_positions,
                                  region_org_id=region_org_id, masters=masters, rules=rules)


def _normalize_records(*, records, target_positions, region_org_id, masters, rules):
    rules_document, conditions = _rules(rules)
    rows = []
    serials = defaultdict(list)
    for record in sorted(records, key=lambda row: row['business_key']):
        data = record['data']
        if (data['warehouseCode'], data['positionCode']) not in target_positions:
            continue
        issues = []
        quantity, locked = _quantity(data.get('qtyStock')), _quantity(data.get('qtyLock'))
        if quantity is None: issues.append('quantity_invalid')
        if locked is None: issues.append('locked_quantity_invalid')
        if quantity is not None and locked is not None and locked > quantity: issues.append('locked_quantity_exceeds_stock')
        material = masters.get(data['materialCode'])
        if material is None: issues.append('material_unmapped')
        elif material['issue']: issues.append(material['issue'])
        else:
            if data.get('unitName') != material['base_unit'] or not material['base_unit'].strip():
                issues.append('unit_mismatch')
            for value in (quantity, locked):
                if value is not None and (value.as_tuple().exponent < -material['quantity_scale']
                        and value != value.quantize(Decimal(1).scaleb(-material['quantity_scale']))
                        or value is not None and not material['allow_fraction'] and value != value.to_integral_value()):
                    issues.append('policy_quantity_mismatch')
            tracking = material['tracking_mode']
            sn = data.get('snNo')
            if tracking in {'lot', 'lot_and_serial'}:
                # The capture whitelist has no documented lot field. Never
                # reinterpret a stock reference number as a lot identifier.
                issues.append('lot_semantics_unavailable')
            if tracking in {'serial', 'lot_and_serial'} and quantity != Decimal(0):
                if type(sn) is not str or not sn or sn != sn.strip() or len(sn) > 200 or any(ord(c) < 32 or ord(c) == 127 for c in sn):
                    issues.append('serial_missing_or_invalid')
                elif quantity != Decimal(1): issues.append('serial_quantity_invalid')
                else: serials[(material['material_id'], sn)].append(len(rows))
        selector = _selector(data.get('materialStatus'), data.get('materialStockType'))
        condition = conditions.get(selector)
        if condition is None: issues.append('condition_unmapped')
        rows.append(dict(external_business_key=record['business_key'], raw_record_sha256=_sha(record),
            source_updated_at=record['source_updated_at'], warehouse_code=data['warehouseCode'], position_code=data['positionCode'],
            control_qty=_number(quantity) if quantity is not None else None,
            locked_qty=_number(locked) if locked is not None else None,
            material_id=material['material_id'] if material and not material['issue'] else None,
            policy_id=material['policy_id'] if material and not material['issue'] else None,
            condition_code=condition, issues=sorted(set(issues))))
    for members in serials.values():
        if len(members) > 1:
            for index in members: rows[index]['issues'] = sorted(set(rows[index]['issues'] + ['serial_duplicate']))
    groups = defaultdict(list)
    for row in rows:
        if not row['issues']: groups[(row['material_id'], row['condition_code'])].append(row)
    aggregates = []
    for (material, condition), members in sorted(groups.items()):
        with localcontext() as context:
            context.prec = 40
            total = sum((Decimal(row['control_qty']) for row in members), Decimal(0))
        if total > MAX_QUANTITY:
            for row in members: row['issues'].append('aggregate_quantity_overflow')
            continue
        key = 'control:' + _sha([str(region_org_id), material, condition])
        payload = opening_control_projection_payload(external_business_key=key, region_org_id=region_org_id,
            material_id=material, condition_code=condition, control_qty=total, mapping_status='resolved', mapping_note='')
        aggregates.append(dict(payload=payload, payload_sha256=_sha(payload),
            origins=[dict(external_business_key=row['external_business_key'], raw_record_sha256=row['raw_record_sha256']) for row in members]))
    blocked = sum(bool(row['issues']) for row in rows)
    return dict(rules=rules_document, rules_sha256=_sha(rules_document), master_observations=masters,
        master_observations_sha256=_sha(masters),
        target_record_count=len(rows), blocked_record_count=blocked,
        issues=dict(sorted(Counter(issue for row in rows for issue in row['issues']).items())), rows=rows,
        # Never return a partial province total as a candidate control balance.
        candidate_groups=[] if blocked else aggregates, normalization_complete=not blocked)


def _inputs(db, preparation_id, now):
    preparation.read_inventory_control_preparation(db, preparation_id=preparation_id)
    root = db.get(Preparation, preparation_id, populate_existing=True)
    binding = db.get(Binding, root.binding_id, populate_existing=True)
    catalog = db.get(Catalog, root.catalog_id, populate_existing=True)
    chain = db.get(Chain, root.capture_chain_id, populate_existing=True)
    report, expected, records = reconstruct_inventory_control_evidence(
        expected_json=_canonical(dict(schema_version='rsc.inventory_control_coverage.v1',
            binding=binding.binding_jsonb, **catalog.catalog_jsonb)), evidence_json=_canonical(chain.evidence_jsonb), checked_at=now)
    targets = {(w.warehouse_code, p.position_code) for w in expected.warehouses for p in w.positions
               if p.region_code == expected.target_region_code}
    codes = sorted({row['data']['materialCode'] for row in records
                    if (row['data']['warehouseCode'], row['data']['positionCode']) in targets})
    return root, binding, catalog, chain, report, records, targets, codes


def _masters_with_evidence(db, source_id, codes, now, evidence):
    masters = _masters(db, source_id, codes, now)
    for code, master in masters.items():
        proof = evidence['materials'].get(code)
        if not proof or any(master[key] != proof[key] for key in ('material_id', 'external_object_id', 'policy_id')):
            master['issue'] = master['issue'] or 'material_source_evidence_unavailable'
        else:
            master['source_evidence'] = proof
    return masters


def inspect_inventory_control_normalization(db, *, preparation_id, rules=None, mapping_decision_id=None):
    """Reprove both inventory and material sources; this remains a read scope."""
    from .inventory_control_admission import inspect_inventory_control_admission, _owner
    from .material_source_proof import inspect_current_material_sources
    _owner(db)
    preparation._begin_outer(db)
    if mapping_decision_id is not None and rules is not None:
        _fail('conflicting_rule_inputs')
    # Immutable preparation coordinates locate SKU locks before control/source
    # file locks. Admission is still independently required after this read.
    inputs = _inputs(db, preparation_id, authority._now(db))
    root, binding, catalog, chain, report, records, targets, codes = inputs
    source_args = dict(source_system_id=binding.source_system_id,
                       source_instance=binding.binding_jsonb['source_instance'], sku_codes=codes)
    source_evidence = inspect_current_material_sources(db, **source_args)['evidence']
    admission = inspect_inventory_control_admission(db, preparation_id=preparation_id)
    current_inputs = _inputs(db, preparation_id, authority._now(db))
    if current_inputs[-1] != codes or current_inputs[5] != records or current_inputs[6] != targets:
        _fail('capture_changed_during_review')
    mapping = None
    if mapping_decision_id is not None:
        from .inventory_control_mapping import resolve_inventory_control_mapping
        mapping = resolve_inventory_control_mapping(db, binding_id=root.binding_id, catalog_id=root.catalog_id,
                                                    decision_id=mapping_decision_id)
        rules = mapping['rules']
    now = authority._now(db)
    masters = _masters_with_evidence(db, binding.source_system_id, codes, now, source_evidence)
    normalized = normalize_records(records=records, target_positions=targets, region_org_id=binding.region_org_id,
                                   masters=masters, rules=rules)
    if mapping is not None:
        current = resolve_inventory_control_mapping(db, binding_id=root.binding_id, catalog_id=root.catalog_id,
                                                    decision_id=mapping_decision_id)
        if current != mapping: _fail('mapping_changed_during_review')
    finished = authority._now(db)
    if _masters_with_evidence(db, binding.source_system_id, codes, finished, source_evidence) != masters:
        _fail('master_changed_during_review')
    if inspect_current_material_sources(db, **source_args)['evidence'] != source_evidence:
        _fail('master_source_changed_during_review')
    finished = authority._now(db)
    valid_until = min(preparation._aware(datetime.fromisoformat(admission['observation']['valid_until'])),
                      datetime.fromisoformat(source_evidence['valid_until']) if source_evidence['valid_until'] else
                      preparation._aware(datetime.fromisoformat(admission['observation']['valid_until'])))
    if mapping is not None and mapping['valid_until'] is not None:
        valid_until = min(valid_until, preparation._aware(datetime.fromisoformat(mapping['valid_until'])))
    if finished >= valid_until:
        _fail('observation_expired')
    document = dict(schema_version='rsc.inventory_control_normalization_review.v2', preparation_id=str(preparation_id),
        admission_observation_sha256=admission['observation_sha256'], capture_chain_sha256=chain.capture_chain_sha256,
        checked_at=finished.isoformat(), valid_until=valid_until.isoformat(), non_target_record_count=len(records)-report.target_record_count,
        master_source_evidence=source_evidence, master_source_evidence_sha256=_sha(source_evidence),
        **({'approved_mapping': mapping} if mapping is not None else {}), **normalized)
    return dict(**admission, normalization_review=document, normalization_review_sha256=_sha(document),
                normalization_rules_authorized=mapping is not None, master_source_evidence_required=bool(codes),
                master_source_evidence_verified=source_evidence['status']=='verified', normalization_valid_until=valid_until.isoformat())
