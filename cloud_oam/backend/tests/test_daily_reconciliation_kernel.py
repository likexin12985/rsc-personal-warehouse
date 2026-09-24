from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import unittest
from uuid import UUID

from app.daily_reconciliation.kernel import (
    Account, BUCKETS, ComparisonBasis, ControlRow, ControlSnapshot, LedgerSnapshot,
    LocationBinding, Movement, ReconciliationError, Transaction, control_document,
    digest, ledger_at_cursor, reconcile_daily,
)

D = Decimal
def uid(n): return str(UUID(int=n))
REGION, FOREIGN, SOURCE, MATERIAL, OTHER = map(uid, (100, 101, 102, 103, 104))
A, B, C, E, F, G = map(uid, range(1, 7))
LOC1, LOC2, LOC3, LOC4 = map(uid, range(20, 24))
STAMP = datetime(2026, 9, 20, 15, 58, tzinfo=timezone.utc)


def movement(n, source, target, qty, boundary=None):
    return Movement(n, source, target, D(qty), boundary)


def transaction(n, kind, *moves, reversed_id=None):
    return Transaction(uid(200+n), n, kind, len(moves), tuple(moves), reversed_id)


def seal(source):
    return replace(source, content_sha256=digest(control_document(source)))


def fixture():
    accounts = (
        Account(A, REGION, LOC1, MATERIAL, 'new', 'available'),
        Account(B, REGION, LOC1, MATERIAL, 'new', 'reserved'),
        Account(C, REGION, LOC2, MATERIAL, 'new', 'available'),
        Account(E, REGION, LOC3, MATERIAL, 'new', 'available'),
        Account(F, FOREIGN, LOC4, MATERIAL, 'new', 'available'),
        Account(G, REGION, LOC1, MATERIAL, 'used', 'available'),
    )
    tx = (
        transaction(1, 'opening', movement(1, None, A, '10', 'opening'), movement(2, None, E, '4', 'opening')),
        transaction(2, 'reserve', movement(1, A, B, '3')),
        transaction(3, 'transfer', movement(1, A, C, '2')),
        transaction(4, 'consume', movement(1, C, None, '1', 'consume')),
        transaction(5, 'reversal', movement(1, None, C, '1', 'consume'), reversed_id=uid(204)),
        transaction(6, 'transfer', movement(1, A, E, '2')),
        transaction(7, 'transfer', movement(1, E, F, '1')),
        transaction(8, 'status_change', movement(1, A, G, '1')),
    )
    rows = (ControlRow(uid(301), 'external-1', 'W1', MATERIAL, 'new', D(7)),
        ControlRow(uid(302), 'external-2', 'W1', MATERIAL, 'used', D(1)),
        ControlRow(uid(303), 'external-3', 'W2', MATERIAL, 'new', D(5)))
    source = seal(ControlSnapshot(uid(400), SOURCE, REGION, STAMP, 'a'*64, 'b'*64, ('W1', 'W2'), 3, rows, '0'*64))
    local = LedgerSnapshot(STAMP+timedelta(seconds=60), 8, 8, accounts, tx)
    basis = ComparisonBasis(uid(401), 'c'*64, SOURCE, REGION, ('W1', 'W2'),
        (LocationBinding(LOC1, 'W1'), LocationBinding(LOC2, 'W1'), LocationBinding(LOC3, 'W2')), tuple(sorted(BUCKETS)))
    return source, local, basis


class DailyReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.source, self.local, self.basis = fixture()

    def compare(self, **kwargs):
        return reconcile_daily(day=date(2026, 9, 20), source=kwargs.get('source', self.source),
            local=kwargs.get('local', self.local), basis=kwargs.get('basis', self.basis),
            maximum_capture_skew_seconds=kwargs.get('skew', 300))

    def reject(self, code, **kwargs):
        with self.assertRaisesRegex(ReconciliationError, '^'+code+'$'):
            self.compare(**kwargs)

    def test_exact_warehouse_condition_comparison_preserves_stock_facts(self):
        before = repr((self.source, self.local, self.basis))
        result = self.compare()
        self.assertEqual(result['status'], 'matched')
        self.assertEqual([(r['warehouse_code'], r['condition'], r['external_qty'], r['local_qty'], r['difference'])
            for r in result['items']], [('W1', 'new', '7.000', '7.000', '0.000'),
            ('W1', 'used', '1.000', '1.000', '0.000'), ('W2', 'new', '5.000', '5.000', '0.000')])
        self.assertEqual(result['local_ledger_cursor'], 8)
        self.assertEqual(result['excluded_local_quantities'], [])
        self.assertFalse(result['stock_written'])
        self.assertFalse(result['source_authority_verified'])
        self.assertFalse(result['mapping_approval_verified'])
        self.assertEqual(repr((self.source, self.local, self.basis)), before)

    def test_cutoff_ignores_later_movements_without_rewriting_history(self):
        _, balances, h = ledger_at_cursor(replace(self.local, cutoff_cursor=3))
        self.assertEqual(balances, {A:D(5), B:D(3), C:D(2), E:D(4)})
        _, earlier, earlier_hash = ledger_at_cursor(replace(self.local, cutoff_cursor=3,
            observed_head_cursor=3, transactions=self.local.transactions[:3]))
        self.assertEqual(balances, earlier)
        self.assertEqual(h, earlier_hash)
        self.reject('daily_cutoff_not_captured_head', local=replace(self.local, cutoff_cursor=3))

    def test_all_grains_are_full_outer_compared_with_signed_differences(self):
        rows = (replace(self.source.rows[0], quantity=D(8)), self.source.rows[2],
            ControlRow(uid(304), 'external-other', 'W2', OTHER, 'damaged', D(2)))
        result = self.compare(source=seal(replace(self.source, rows=rows)))
        amounts = {(r['warehouse_code'], r['material_id'], r['condition']):r['difference'] for r in result['items']}
        self.assertEqual(amounts[('W1', MATERIAL, 'new')], '1.000')
        self.assertEqual(amounts[('W1', MATERIAL, 'used')], '-1.000')
        self.assertEqual(amounts[('W2', OTHER, 'damaged')], '2.000')
        self.assertEqual(result['status'], 'differences')

    def test_reserved_buckets_are_explicit_and_exclusions_keep_their_grain(self):
        result = self.compare(basis=replace(self.basis, included_buckets=('available',)))
        new = next(r for r in result['items'] if r['warehouse_code']=='W1' and r['condition']=='new')
        self.assertEqual((new['local_qty'], new['difference']), ('4.000', '3.000'))
        self.assertEqual(result['excluded_local_quantities'], [dict(warehouse_code='W1', material_id=MATERIAL,
            condition='new', bucket='reserved', quantity='3.000')])

    def test_empty_complete_external_snapshot_is_known_zero(self):
        source = seal(replace(self.source, rows=(), expected_row_count=0))
        result = self.compare(source=source)
        self.assertEqual([r['difference'] for r in result['items']], ['-7.000','-1.000','-5.000'])

    def test_multiple_source_origins_group_only_within_exact_grain(self):
        rows = (replace(self.source.rows[0], quantity=D(3)), *self.source.rows[1:],
            replace(self.source.rows[0], origin_id=uid(305), source_business_key='second-position', quantity=D(4)))
        result = self.compare(source=seal(replace(self.source, rows=rows, expected_row_count=4)))
        self.assertEqual(result['status'], 'matched')
        self.assertEqual(len(result['items']), 3)
        self.assertEqual(result['items'][0]['external_qty'], '7.000')

    def test_excluded_different_materials_are_not_added_as_one_quantity(self):
        account = Account(uid(10), REGION, LOC1, OTHER, 'new', 'reserved')
        tx = transaction(9, 'opening', movement(1, None, account.id, '2', 'opening'))
        local = replace(self.local, cutoff_cursor=9, observed_head_cursor=9,
            accounts=(*self.local.accounts, account), transactions=(*self.local.transactions, tx))
        result = self.compare(local=local, basis=replace(self.basis, included_buckets=('available',)))
        self.assertEqual([(r['material_id'], r['quantity']) for r in result['excluded_local_quantities']],
            [(MATERIAL, '3.000'), (OTHER, '2.000')])

    def test_empty_local_prefix_is_known_zero_only_at_captured_zero_head(self):
        source = seal(replace(self.source, rows=(), expected_row_count=0))
        local = replace(self.local, cutoff_cursor=0, observed_head_cursor=0, transactions=())
        result = self.compare(source=source, local=local)
        self.assertEqual((result['status'], result['items']), ('matched', []))
        self.assertFalse(result['source_authority_verified'])
        self.reject('daily_cutoff_not_captured_head', source=source,
            local=replace(local, observed_head_cursor=1))

    def test_missing_warehouse_is_unknown_not_zero(self):
        source = seal(replace(self.source, rows=(), expected_row_count=0, covered_warehouses=('W1',)))
        self.reject('warehouse_coverage_incomplete', source=source)

    def test_unmapped_local_location_and_ambiguous_mapping_fail(self):
        self.reject('local_location_unmapped', basis=replace(self.basis, locations=self.basis.locations[:2]))
        self.reject('location_mapping_ambiguous', basis=replace(self.basis,
            locations=self.basis.locations+(LocationBinding(LOC1, 'W2'),)))

    def test_region_and_source_cannot_be_swapped(self):
        for field in ('region_id', 'source_system_id'):
            with self.subTest(field=field):
                self.reject('source_scope_mismatch', basis=replace(self.basis, **{field:uid(999)}))

    def test_manifest_mutation_and_truncated_rows_fail(self):
        rows = (replace(self.source.rows[0], quantity=D(999)), *self.source.rows[1:])
        self.reject('control_manifest_mismatch', source=replace(self.source, rows=rows))
        self.reject('control_count_mismatch', source=seal(replace(self.source, rows=self.source.rows[:2])))

    def test_duplicate_source_identity_and_source_business_key_fail(self):
        for row in (self.source.rows[0], replace(self.source.rows[0], origin_id=uid(909))):
            with self.subTest(row=row.origin_id):
                self.reject('control_origin_duplicate', source=seal(replace(self.source,
                    rows=(*self.source.rows,row), expected_row_count=4)))

    def test_missing_cursor_and_duplicate_cursor_or_transaction_fail(self):
        self.reject('ledger_prefix_incomplete', local=replace(self.local, transactions=self.local.transactions[1:]))
        tx = (*self.local.transactions[:-1], replace(self.local.transactions[-1], cursor=7))
        self.reject('cursor_duplicate', local=replace(self.local, transactions=tx))
        self.reject('transaction_duplicate', local=replace(self.local, transactions=(*self.local.transactions,self.local.transactions[-1])))

    def test_missing_last_movement_and_reordered_line_number_fail(self):
        first = self.local.transactions[0]
        for tx, code in ((replace(first, movements=first.movements[:1]), 'movement_set_incomplete'),
            (replace(first, movements=(first.movements[0],replace(first.movements[1], line_no=3))), 'movement_line_sequence')):
            with self.subTest(code=code):
                self.reject(code, local=replace(self.local, transactions=(tx,*self.local.transactions[1:])))

    def test_nonposted_transaction_and_future_cursor_fail(self):
        tx = (replace(self.local.transactions[0], status='pending'), *self.local.transactions[1:])
        self.reject('transaction_not_posted', local=replace(self.local, transactions=tx))
        tx = (*self.local.transactions[:-1], replace(self.local.transactions[-1], cursor=99))
        self.reject('transaction_cursor_invalid', local=replace(self.local, transactions=tx))

    def test_missing_account_and_cross_material_transfer_fail(self):
        self.reject('movement_account_missing', local=replace(self.local, accounts=self.local.accounts[:-1]))
        accounts = tuple(replace(a, material_id=OTHER) if a.id==F else a for a in self.local.accounts)
        self.reject('movement_material_mismatch', local=replace(self.local, accounts=accounts))

    def test_historical_negative_cannot_be_hidden_by_later_inbound(self):
        tx = (transaction(1,'opening',movement(1,None,A,'5','opening')),
            transaction(2,'consume',movement(1,A,None,'6','consume')),
            transaction(3,'inbound',movement(1,None,A,'1','inbound')))
        self.reject('historical_negative_balance', local=replace(self.local, cutoff_cursor=3,observed_head_cursor=3,transactions=tx))

    def test_atomic_transaction_is_checked_after_all_movement_lines(self):
        tx = (transaction(1,'opening',movement(1,None,A,'5','opening'),movement(2,None,B,'5','opening')),
            transaction(2,'transfer',movement(1,A,B,'7'),movement(2,B,A,'2')))
        _, balances, _ = ledger_at_cursor(replace(self.local, cutoff_cursor=2,observed_head_cursor=2,transactions=tx))
        self.assertEqual(balances, {A:D(0),B:D(10)})

    def test_reversal_must_be_exact_unique_and_reference_prior_transaction(self):
        fifth = self.local.transactions[4]
        for tx, code in ((replace(fifth, reversed_transaction_id=uid(299)), 'reversal_binding_invalid'),
            (replace(fifth,movements=(replace(fifth.movements[0],quantity=D(2)),)), 'reversal_lines_mismatch')):
            with self.subTest(code=code):
                self.reject(code,local=replace(self.local,transactions=(*self.local.transactions[:4],tx,*self.local.transactions[5:])))
        duplicate = replace(fifth, id=uid(209), cursor=9)
        self.reject('reversal_binding_invalid',local=replace(self.local,cutoff_cursor=9,observed_head_cursor=9,
            transactions=(*self.local.transactions,duplicate)))

    def test_quantity_precision_nonfinite_and_negative_inputs_fail(self):
        for value in (D('0.0001'),D('-1'),D('NaN'),D('Infinity'),1.5,True,D('1000000000000000')):
            with self.subTest(value=str(value)), self.assertRaises(ReconciliationError):
                source = replace(self.source,rows=(replace(self.source.rows[0],quantity=value),*self.source.rows[1:]))
                self.compare(source=seal(source))

    def test_capture_dates_and_skew_are_preserved_not_guessed(self):
        self.reject('capture_day_mismatch',local=replace(self.local,captured_at=datetime(2026,9,20,16,0,tzinfo=timezone.utc)))
        self.reject('capture_skew_exceeded',skew=59)
        self.assertEqual(self.compare(skew=60)['capture_skew_seconds'],60)
        self.reject('time_unaware',local=replace(self.local,captured_at=datetime(2026,9,20,23,59)))

    def test_order_independence_and_returned_copy_do_not_mutate_capture(self):
        first = self.compare()
        source = replace(self.source, rows=tuple(reversed(self.source.rows)))
        local = replace(self.local,transactions=tuple(reversed(self.local.transactions)),accounts=tuple(reversed(self.local.accounts)))
        self.assertEqual(first,self.compare(source=source,local=local))
        first['items'][0]['local_qty']='999.000'
        self.assertEqual(self.compare()['items'][0]['local_qty'],'7.000')

    def test_comparison_hash_binds_both_snapshots_and_explicit_basis(self):
        original = self.compare()
        shifted = self.compare(local=replace(self.local,captured_at=self.local.captured_at+timedelta(seconds=1)))
        new_mapping = self.compare(basis=replace(self.basis,mapping_version_id=uid(888)))
        self.assertNotEqual(original['report_sha256'],shifted['report_sha256'])
        self.assertNotEqual(original['report_sha256'],new_mapping['report_sha256'])
        self.assertEqual(original['local_ledger_sha256'],shifted['local_ledger_sha256'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
