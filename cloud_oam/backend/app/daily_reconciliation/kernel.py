"""Daily comparison kernel candidate. No I/O, authorization, persistence or stock writes.

Inputs must come from a trusted, consistent database capture. Content checks do
not authenticate a source or approve a warehouse mapping. An HTTP caller must
never be allowed to supply these snapshots as authoritative inventory.
"""
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, localcontext
from hashlib import sha256
import json
from uuid import UUID
from zoneinfo import ZoneInfo

CONDITIONS = frozenset({'new', 'used', 'damaged', 'scrapped'})
BUCKETS = frozenset({'available', 'reserved', 'picking', 'outbound', 'in_transit',
    'arrived_pending', 'frozen', 'return_pending', 'scrap_pending'})
MAX_QTY = Decimal('999999999999999.999')
MOVEMENTS = frozenset({'opening', 'transfer', 'reserve', 'release', 'pick', 'outbound',
    'transit', 'inbound', 'freeze', 'unfreeze', 'consume', 'return', 'scrap',
    'stocktake_gain', 'stocktake_loss', 'status_change', 'reversal'})


class ReconciliationError(ValueError):
    pass


def require(ok, code):
    if not ok:
        raise ReconciliationError(code)


def identifier(value):
    require(type(value) is str, 'identifier_invalid')
    try:
        parsed = UUID(value)
    except ValueError:
        raise ReconciliationError('identifier_invalid') from None
    require(parsed.int != 0 and str(parsed) == value, 'identifier_invalid')
    return value


def text(value, maximum=160):
    require(type(value) is str and 0 < len(value) <= maximum and value.strip() == value
        and all(ord(c) >= 32 and ord(c) != 127 for c in value), 'text_invalid')
    return value


def digest(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
        allow_nan=False).encode()).hexdigest()


def hash_value(value):
    require(type(value) is str and len(value) == 64 and set(value) <= set('0123456789abcdef'), 'hash_invalid')
    return value


def quantity(value, *, signed=False, positive=False):
    require(type(value) is Decimal and value.is_finite(), 'quantity_invalid')
    require(abs(value) <= MAX_QTY and (signed or value >= 0) and (not positive or value > 0), 'quantity_range')
    require(value == value.quantize(Decimal('.001')), 'quantity_scale')
    return format(value.copy_abs() if value == 0 else value, '.3f')


def instant(value):
    require(type(value) is datetime and value.tzinfo is not None and value.utcoffset() is not None, 'time_unaware')
    return value.astimezone(timezone.utc).isoformat()


def tuples(value):
    require(type(value) is tuple, 'immutable_tuple_required')
    return value


@dataclass(frozen=True, slots=True)
class ControlRow:
    origin_id: str
    source_business_key: str
    warehouse_code: str
    material_id: str
    condition: str
    quantity: Decimal


@dataclass(frozen=True, slots=True)
class ControlSnapshot:
    publication_id: str
    source_system_id: str
    region_id: str
    captured_at: datetime
    publication_sha256: str
    catalog_sha256: str
    covered_warehouses: tuple[str, ...]
    expected_row_count: int
    rows: tuple[ControlRow, ...]
    content_sha256: str


@dataclass(frozen=True, slots=True)
class Account:
    id: str
    region_id: str
    location_id: str
    material_id: str
    condition: str
    bucket: str


@dataclass(frozen=True, slots=True)
class Movement:
    line_no: int
    from_account_id: str | None
    to_account_id: str | None
    quantity: Decimal
    external_boundary_code: str | None = None


@dataclass(frozen=True, slots=True)
class Transaction:
    id: str
    cursor: int
    movement_type: str
    expected_line_count: int
    movements: tuple[Movement, ...]
    # Reversal is a new posted transaction; never remove its original.
    reversed_transaction_id: str | None = None
    status: str = 'posted'


@dataclass(frozen=True, slots=True)
class LedgerSnapshot:
    captured_at: datetime
    cutoff_cursor: int
    observed_head_cursor: int
    accounts: tuple[Account, ...]
    transactions: tuple[Transaction, ...]


@dataclass(frozen=True, slots=True)
class LocationBinding:
    location_id: str
    warehouse_code: str


@dataclass(frozen=True, slots=True)
class ComparisonBasis:
    mapping_version_id: str
    mapping_sha256: str
    source_system_id: str
    region_id: str
    warehouses: tuple[str, ...]
    locations: tuple[LocationBinding, ...]
    included_buckets: tuple[str, ...]


def control_document(source):
    rows = []
    for row in tuples(source.rows):
        require(row.condition in CONDITIONS, 'condition_invalid')
        rows.append(dict(origin_id=identifier(row.origin_id), business_key=text(row.source_business_key, 200),
            warehouse_code=text(row.warehouse_code), material_id=identifier(row.material_id),
            condition=row.condition, quantity=quantity(row.quantity)))
    return dict(schema='rsc.daily_control_capture.v1', publication_id=identifier(source.publication_id),
        source_system_id=identifier(source.source_system_id), region_id=identifier(source.region_id),
        captured_at=instant(source.captured_at), publication_sha256=hash_value(source.publication_sha256),
        catalog_sha256=hash_value(source.catalog_sha256), covered_warehouses=sorted(tuples(source.covered_warehouses)),
        expected_row_count=source.expected_row_count, rows=sorted(rows, key=lambda r: r['origin_id']))


def ledger_at_cursor(local):
    """Rebuild signed movements, including reservation transfers and reversals.

    A later live balance or effective_at timestamp is not a historical cursor.
    The database adapter must capture the transactional head and facts from one
    MVCC view. This function proves content consistency, not database coverage.
    """
    instant(local.captured_at)
    require(type(local.cutoff_cursor) is int and type(local.observed_head_cursor) is int
        and 0 <= local.cutoff_cursor <= local.observed_head_cursor, 'ledger_cursor_invalid')
    accounts = {}
    for account in tuples(local.accounts):
        identifier(account.id); identifier(account.region_id); identifier(account.location_id); identifier(account.material_id)
        require(account.condition in CONDITIONS and account.bucket in BUCKETS, 'account_dimensions_invalid')
        require(account.id not in accounts, 'account_duplicate')
        accounts[account.id] = account
    all_transactions = tuples(local.transactions)
    require(len({t.id for t in all_transactions}) == len(all_transactions), 'transaction_duplicate')
    require(len({t.cursor for t in all_transactions}) == len(all_transactions), 'cursor_duplicate')
    for t in all_transactions:
        identifier(t.id)
        require(type(t.cursor) is int and 0 < t.cursor <= local.observed_head_cursor, 'transaction_cursor_invalid')
    selected = sorted((t for t in all_transactions if t.cursor <= local.cutoff_cursor), key=lambda t: t.cursor)
    require(len(selected) == local.cutoff_cursor and all(t.cursor == n for n, t in enumerate(selected, 1)), 'ledger_prefix_incomplete')
    balances = {}; seen = {}; reversed_ids = set(); facts = []
    with localcontext() as context:
        context.prec = 60
        for t in selected:
            require(t.status == 'posted' and t.movement_type in MOVEMENTS, 'transaction_not_posted')
            moves = sorted(tuples(t.movements), key=lambda m: m.line_no)
            require(type(t.expected_line_count) is int and t.expected_line_count > 0
                and len(moves) == t.expected_line_count, 'movement_set_incomplete')
            require(all(type(m.line_no) is int and m.line_no == n for n, m in enumerate(moves, 1)), 'movement_line_sequence')
            if t.movement_type == 'reversal':
                require(t.reversed_transaction_id in seen and t.reversed_transaction_id not in reversed_ids, 'reversal_binding_invalid')
                original = seen[t.reversed_transaction_id]
                require(original.movement_type != 'reversal', 'reversal_of_reversal')
                original_moves = sorted(original.movements, key=lambda m: m.line_no)
                require(len(original_moves) == len(moves) and all(
                    (m.from_account_id, m.to_account_id, m.quantity, m.external_boundary_code) ==
                    (o.to_account_id, o.from_account_id, o.quantity, o.external_boundary_code)
                    for m, o in zip(moves, original_moves, strict=True)), 'reversal_lines_mismatch')
                reversed_ids.add(t.reversed_transaction_id)
            else:
                require(t.reversed_transaction_id is None, 'reversal_binding_invalid')
            lines = []
            touched = set()
            for m in moves:
                quantity(m.quantity, positive=True)
                require(m.from_account_id != m.to_account_id, 'movement_endpoints_invalid')
                external = m.from_account_id is None or m.to_account_id is None
                require((external and m.external_boundary_code is not None) or
                    (not external and m.external_boundary_code is None), 'movement_boundary_invalid')
                if external: text(m.external_boundary_code, 100)
                if not external:
                    require(m.from_account_id in accounts and m.to_account_id in accounts, 'movement_account_missing')
                    require(accounts[m.from_account_id].material_id == accounts[m.to_account_id].material_id, 'movement_material_mismatch')
                for account_id, sign in ((m.from_account_id, -1), (m.to_account_id, 1)):
                    if account_id is None: continue
                    require(account_id in accounts, 'movement_account_missing')
                    balances[account_id] = balances.get(account_id, Decimal(0)) + sign * m.quantity
                    touched.add(account_id)
                lines.append(dict(line_no=m.line_no, from_account_id=m.from_account_id, to_account_id=m.to_account_id,
                    quantity=quantity(m.quantity), external_boundary_code=m.external_boundary_code))
            for account_id in touched:
                require(balances[account_id] >= 0, 'historical_negative_balance')
                quantity(balances[account_id])
            seen[t.id] = t
            facts.append(dict(id=t.id, cursor=t.cursor, movement_type=t.movement_type,
                reversed_transaction_id=t.reversed_transaction_id, movements=lines))
    account_rows = [dict(id=a.id, region_id=a.region_id, location_id=a.location_id, material_id=a.material_id,
        condition=a.condition, bucket=a.bucket) for a in sorted(accounts.values(), key=lambda a: a.id) if a.id in balances]
    manifest = dict(schema='rsc.daily_ledger_cutoff.v1', cutoff_cursor=local.cutoff_cursor, accounts=account_rows, transactions=facts)
    return accounts, balances, digest(manifest)


def reconcile_daily(*, day, source, local, basis, maximum_capture_skew_seconds=300):
    require(type(day) is date, 'business_day_invalid')
    require(type(maximum_capture_skew_seconds) is int and 0 <= maximum_capture_skew_seconds <= 86400, 'capture_skew_policy_invalid')
    source_doc = control_document(source)
    require(hash_value(source.content_sha256) == digest(source_doc), 'control_manifest_mismatch')
    instant(local.captured_at)
    require(all(t.astimezone(ZoneInfo('Asia/Shanghai')).date() == day for t in (source.captured_at, local.captured_at)), 'capture_day_mismatch')
    skew = abs((local.captured_at - source.captured_at).total_seconds())
    require(skew <= maximum_capture_skew_seconds, 'capture_skew_exceeded')
    require(local.cutoff_cursor == local.observed_head_cursor, 'daily_cutoff_not_captured_head')
    identifier(basis.mapping_version_id); hash_value(basis.mapping_sha256)
    require(identifier(basis.source_system_id) == source.source_system_id and identifier(basis.region_id) == source.region_id, 'source_scope_mismatch')
    warehouses = tuples(basis.warehouses); covered = tuples(source.covered_warehouses)
    require(bool(warehouses) and len(set(warehouses)) == len(warehouses) and len(set(covered)) == len(covered), 'warehouse_coverage_invalid')
    require(all(text(w) for w in warehouses) and set(warehouses) == set(covered), 'warehouse_coverage_incomplete')
    require(type(source.expected_row_count) is int and source.expected_row_count == len(source.rows), 'control_count_mismatch')
    require(len({r.origin_id for r in source.rows}) == len(source.rows)
        and len({r.source_business_key for r in source.rows}) == len(source.rows), 'control_origin_duplicate')
    included = tuples(basis.included_buckets)
    require(bool(included) and len(set(included)) == len(included) and set(included) <= BUCKETS, 'bucket_policy_invalid')
    locations = {}
    for binding in tuples(basis.locations):
        identifier(binding.location_id)
        require(binding.location_id not in locations and binding.warehouse_code in warehouses, 'location_mapping_ambiguous')
        locations[binding.location_id] = binding.warehouse_code
    accounts, balances, ledger_hash = ledger_at_cursor(local)
    external = {}; internal = {}; excluded = {}
    with localcontext() as context:
        context.prec = 60
        for row in source.rows:
            require(row.warehouse_code in warehouses, 'control_outside_coverage')
            key = (row.warehouse_code, row.material_id, row.condition)
            external[key] = external.get(key, Decimal(0)) + row.quantity
        for account_id, amount in balances.items():
            account = accounts[account_id]
            if account.region_id != basis.region_id: continue
            require(account.location_id in locations, 'local_location_unmapped')
            if account.bucket not in included:
                key = (locations[account.location_id], account.material_id, account.condition, account.bucket)
                excluded[key] = excluded.get(key, Decimal(0)) + amount
                continue
            key = (locations[account.location_id], account.material_id, account.condition)
            internal[key] = internal.get(key, Decimal(0)) + amount
        items = []
        for warehouse, material, condition in sorted(external.keys() | internal.keys()):
            key = (warehouse, material, condition)
            e = external.get(key, Decimal(0)); i = internal.get(key, Decimal(0))
            items.append(dict(warehouse_code=warehouse, material_id=material, condition=condition,
                external_qty=quantity(e), local_qty=quantity(i), difference=quantity(e-i, signed=True),
                status='matched' if e == i else 'difference'))
    report = dict(schema='rsc.daily_reconciliation_candidate.v1', business_date=day.isoformat(), timezone='Asia/Shanghai',
        status='differences' if any(row['status'] == 'difference' for row in items) else 'matched',
        source_system_id=source.source_system_id, region_id=source.region_id, publication_id=source.publication_id,
        publication_sha256=source.publication_sha256, catalog_sha256=source.catalog_sha256,
        external_capture_sha256=source.content_sha256, external_snapshot_at=instant(source.captured_at),
        local_snapshot_at=instant(local.captured_at), local_ledger_cursor=local.cutoff_cursor,
        local_ledger_sha256=ledger_hash, capture_skew_seconds=skew, maximum_capture_skew_seconds=maximum_capture_skew_seconds,
        mapping_version_id=basis.mapping_version_id, mapping_sha256=basis.mapping_sha256,
        mapping_content_sha256=digest(dict(warehouses=sorted(warehouses), locations=sorted(locations.items()), included_buckets=sorted(included))),
        excluded_local_quantities=[dict(warehouse_code=k[0], material_id=k[1], condition=k[2],
            bucket=k[3], quantity=quantity(v)) for k, v in sorted(excluded.items())], items=items,
        source_authority_verified=False, mapping_approval_verified=False, persisted=False, stock_written=False)
    return {**report, 'report_sha256': digest(report)}
