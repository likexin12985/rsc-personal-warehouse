"""Internal daily-capture adapter candidate, without I/O or authority claims.

Only the verified internal readers may supply raw captures. A JSON digest is
content integrity, not authorization. Mapping publication, capture authorization,
and immutable persistence remain required before this can become an API.
"""
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
import re

from . import kernel as kernel


class BridgeError(ValueError):
    pass


def require(ok, code):
    if not ok:
        raise BridgeError(code)


def sealed(document, schema, code):
    require(type(document) is dict and document.get('schema') == schema, code + '_schema')
    expected = kernel.hash_value(document.get('content_sha256'))
    require(kernel.digest({k: v for k, v in document.items() if k != 'content_sha256'}) == expected,
            code + '_hash')
    return document


def at(value):
    require(type(value) is str, 'capture_time_invalid')
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        raise BridgeError('capture_time_invalid') from None
    kernel.instant(result)
    return result


def qty(value):
    require(type(value) is str and re.fullmatch(r'(0|[1-9][0-9]{0,14})\.[0-9]{3}', value) is not None,
            'quantity_not_canonical')
    result = Decimal(value)
    kernel.quantity(result)
    return result


def indexed(rows, code):
    require(type(rows) is list, code + '_rows')
    result = {}
    for row in rows:
        require(type(row) is dict, code + '_row')
        identity = kernel.identifier(row['id'])
        require(identity not in result, code + '_duplicate')
        result[identity] = row
    return result


def adapt(*, source, ledger, mapping):
    """Preserve source time, ledger prefix and explicit physical-location scope."""
    sealed(source, 'rsc.daily_published_control_capture_candidate.v1', 'source')
    sealed(ledger, 'rsc.daily_raw_ledger_capture_candidate.v1', 'ledger')
    sealed(mapping, 'rsc.daily_comparison_mapping_candidate.v1', 'mapping')
    require(source['historical_graph_verified'] is True, 'source_graph_unverified')
    observation = source['database_observation']
    require(observation['read_only'] is True and observation['repeatable_read'] is True
            and observation['role'] == 'rsc_control_capture', 'source_read_boundary')
    require(ledger['transaction_read_only'] is True and ledger['transaction_isolation'] == 'repeatable read'
            and ledger['observation']['role_name'] == ledger['observation']['session_role'] == 'rsc_reconciliation_capture',
            'ledger_read_boundary')
    require(ledger['owner_and_location_scope_distinct'] is True, 'scope_separation_missing')
    require(set(mapping) == {'schema', 'version_id', 'source_system_id', 'region_id', 'catalog_sha256',
            'warehouses', 'location_bindings', 'included_buckets', 'location_facts_sha256',
            'organization_facts_sha256', 'scope_policy', 'source_quantity_policy', 'content_sha256'},
            'mapping_fields_invalid')
    require(mapping['scope_policy'] == 'explicit_physical_location'
            and mapping['source_quantity_policy'] == 'published_quantity_locked_separate', 'comparison_policy_invalid')
    require(mapping['source_system_id'] == source['source_system_id'] and mapping['region_id'] == source['region_id']
            and mapping['catalog_sha256'] == source['catalog_sha256'], 'mapping_source_mismatch')
    warehouses = mapping['warehouses']
    require(type(warehouses) is list and warehouses and len(set(warehouses)) == len(warehouses)
            and sorted(warehouses) == sorted(source['covered_warehouses']), 'mapping_warehouses_mismatch')
    for warehouse in warehouses:
        kernel.text(warehouse)
    coverage = source['coverage']
    require(type(coverage) is list and len({w['warehouse_code'] for w in coverage}) == len(coverage)
            and {w['warehouse_code'] for w in coverage if w['target_positions']} == set(warehouses),
            'source_coverage_incomplete')
    positions = {}
    for warehouse in coverage:
        key = warehouse['warehouse_code']
        require(type(warehouse['source_total']) is int and warehouse['source_total'] >= 0
                and type(warehouse['page_count']) is int and warehouse['page_count'] > 0, 'source_coverage_invalid')
        value = warehouse['target_positions']
        require(type(value) is list and len(set(value)) == len(value)
                and bool(value) == (key in warehouses), 'source_positions_invalid')
        positions[key] = set(value)
        for position in value:
            kernel.text(position)
    captured_at = at(source['captured_at'])
    require(captured_at <= at(source['published_at']) <= at(observation['observed_at']), 'source_time_order')
    require(at(source['published_at']) <= at(source['publication_valid_until']), 'publication_time_invalid')
    require(type(source['origin_count']) is int and type(source['rows']) is list
            and source['origin_count'] == len(source['rows']), 'source_origins_incomplete')
    control_rows = []
    locked = []
    for row in source['rows']:
        require(row['warehouse_code'] in positions and row['position_code'] in positions[row['warehouse_code']],
                'source_origin_outside_positions')
        control_rows.append(kernel.ControlRow(row['origin_id'], row['source_business_key'], row['warehouse_code'],
                                             row['material_id'], row['condition'], qty(row['quantity'])))
        kernel.hash_value(row['origin_sha256'])
        locked.append(dict(origin_id=row['origin_id'], locked_quantity=kernel.quantity(qty(row['locked_quantity']))))
    source_args = dict(publication_id=source['publication_id'], source_system_id=source['source_system_id'],
        region_id=source['region_id'], captured_at=captured_at, publication_sha256=source['publication_sha256'],
        catalog_sha256=source['catalog_sha256'], covered_warehouses=tuple(warehouses),
        expected_row_count=source['origin_count'], rows=tuple(control_rows))
    provisional = kernel.ControlSnapshot(**source_args, content_sha256='')
    control = kernel.ControlSnapshot(**source_args, content_sha256=kernel.digest(kernel.control_document(provisional)))
    facts = ledger['facts']
    expected_tables = {'inventory_transactions', 'inventory_movements', 'stock_accounts', 'stock_locations', 'organizations'}
    require(set(facts) == set(ledger['counts']) == expected_tables, 'ledger_tables_invalid')
    for name in expected_tables:
        require(type(facts[name]) is list and type(ledger['counts'][name]) is int
                and len(facts[name]) == ledger['counts'][name], 'ledger_count_mismatch')
    locations = indexed(facts['stock_locations'], 'location')
    organizations = indexed(facts['organizations'], 'organization')
    accounts = indexed(facts['stock_accounts'], 'account')
    require(mapping['location_facts_sha256'] == kernel.digest(sorted(locations.values(), key=lambda r: r['id']))
            and mapping['organization_facts_sha256'] == kernel.digest(sorted(organizations.values(), key=lambda r: r['id'])),
            'mapping_reference_facts_changed')
    for tree in (locations, organizations):
        for row in tree.values():
            cursor = row
            seen = set()
            while cursor is not None:
                require(cursor['id'] not in seen and len(seen) < 128, 'reference_cycle_or_depth')
                seen.add(cursor['id'])
                parent = cursor['parent_id']
                require(parent is None or parent in tree, 'reference_parent_missing')
                cursor = tree.get(parent)
    require(all(row['owner_org_id'] in organizations for row in locations.values()), 'location_owner_missing')
    bindings = mapping['location_bindings']
    require(type(bindings) is list, 'mapping_bindings_invalid')
    scope = {}
    for binding in bindings:
        require(set(binding) == {'location_id', 'region_id', 'warehouse_code'}, 'mapping_binding_fields')
        location_id = kernel.identifier(binding['location_id'])
        region_id = kernel.identifier(binding['region_id'])
        require(location_id in locations and location_id not in scope, 'mapping_location_invalid')
        require(region_id in organizations and organizations[region_id]['org_type'] == 'region_company', 'mapping_region_invalid')
        target = region_id == source['region_id']
        require((target and binding['warehouse_code'] in warehouses)
                or (not target and binding['warehouse_code'] is None), 'mapping_warehouse_invalid')
        scope[location_id] = binding
    # Include even zero-balance and currently unused locations. A new location
    # must explicitly update the reviewed mapping; never infer via an owner.
    require(set(scope) == set(locations), 'mapping_locations_incomplete')
    converted_accounts = []
    for row in accounts.values():
        require(row['location_id'] in scope and row['owner_org_id'] in organizations, 'account_reference_missing')
        converted_accounts.append(kernel.Account(row['id'], scope[row['location_id']]['region_id'],
            row['location_id'], row['material_id'], row['condition_code'], row['availability_bucket']))
    moves = indexed(facts['inventory_movements'], 'movement')
    transactions = indexed(facts['inventory_transactions'], 'transaction')
    by_transaction = defaultdict(list)
    for row in moves.values():
        require(row['transaction_id'] in transactions, 'movement_transaction_missing')
        by_transaction[row['transaction_id']].append(kernel.Movement(row['line_no'], row['from_account_id'],
            row['to_account_id'], qty(row['quantity']), row['external_boundary_code']))
    converted_transactions = tuple(kernel.Transaction(row['id'], row['ledger_cursor'], row['movement_type'],
        len(by_transaction[row['id']]), tuple(by_transaction[row['id']]), row['reversed_transaction_id'], row['status'])
        for row in transactions.values())
    head = ledger['ledger_head']
    require(type(head['next_cursor']) is int and head['next_cursor'] > 0 and head['stream_key'] == 'inventory'
            and type(ledger['cutoff_cursor']) is int and ledger['cutoff_cursor'] == head['next_cursor'] - 1, 'ledger_head_mismatch')
    local_at = at(ledger['observation']['captured_at'])
    require(local_at <= at(ledger['completed_at']), 'ledger_time_order')
    local = kernel.LedgerSnapshot(local_at, ledger['cutoff_cursor'], head['next_cursor'] - 1,
                                 tuple(converted_accounts), converted_transactions)
    basis = kernel.ComparisonBasis(mapping['version_id'], mapping['content_sha256'], mapping['source_system_id'],
        mapping['region_id'], tuple(warehouses), tuple(kernel.LocationBinding(row['location_id'], row['warehouse_code'])
            for row in bindings if row['region_id'] == source['region_id']), tuple(mapping['included_buckets']))
    return control, local, basis, sorted(locked, key=lambda r: r['origin_id'])


def compare_captures(*, day, source, ledger, mapping, maximum_capture_skew_seconds=300):
    control, local, basis, locked = adapt(source=source, ledger=ledger, mapping=mapping)
    report = kernel.reconcile_daily(day=day, source=control, local=local, basis=basis,
                                   maximum_capture_skew_seconds=maximum_capture_skew_seconds)
    document = dict(schema='rsc.daily_capture_comparison_candidate.v1', comparison=report,
        input_hashes=dict(source=source['content_sha256'], ledger=ledger['content_sha256'], mapping=mapping['content_sha256']),
        source_observed_at=source['database_observation']['observed_at'],
        source_published_at=source['published_at'], source_valid_until=source['publication_valid_until'],
        source_locked_quantities=locked, scope_policy=mapping['scope_policy'],
        source_quantity_policy=mapping['source_quantity_policy'],
        authority_boundary=dict(historical_source_graph_verified=True, operator_authority_verified=False,
            current_source_authorized=False, mapping_approval_verified=False, production_schema_verified=False,
            persisted=False, stock_written=False))
    return {**document, 'content_sha256': kernel.digest(document)}
