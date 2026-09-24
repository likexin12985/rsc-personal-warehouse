from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
import unittest
from uuid import UUID

from app.daily_reconciliation import kernel
from app.daily_reconciliation.capture_bridge import BridgeError, adapt, at, compare_captures

HERE = Path(__file__).resolve().parent
ARTIFACTS = HERE/'fixtures/daily_reconciliation'
REGION = '34572b4f-8e74-48ff-9dae-33bb499fe69f'
# Explicit synthetic fixture coordinates, never derived from location labels,
# account owner, parent inheritance or a source material name.
LOCATIONS = {
    '092d25d9-84a1-4eb3-a487-763fc30a61a3': 'W-B',
    '108c07d8-edf4-45de-a214-d7e4f8947aef': 'W-A',
    '126328d6-bedd-4c16-9342-d8093689276f': 'W-A',
    '69b48b8b-4b37-455a-aaf8-8eec080c6f57': 'W-B',
    '6e71aa18-fd50-4d4f-af7f-e1bcd028b26f': 'W-A',
    '9169c73d-152c-4c8e-a82d-3e23b934c904': 'W-A',
    '920e308f-c548-4b05-92f9-20dfbc610dce': 'W-A',
    'fa52aa5c-e6af-47f5-808d-283600a6c50b': 'W-A',
}


def seal(value):
    value['content_sha256'] = kernel.digest({k: v for k, v in value.items() if k != 'content_sha256'})
    return value


def mapping_for(source, ledger):
    assert set(LOCATIONS) == {row['id'] for row in ledger['facts']['stock_locations']}
    return seal(dict(schema='rsc.daily_comparison_mapping_candidate.v1', version_id=str(UUID(int=793)),
        source_system_id=source['source_system_id'], region_id=REGION, catalog_sha256=source['catalog_sha256'],
        warehouses=['W-A', 'W-B'], location_bindings=[dict(location_id=k, region_id=REGION, warehouse_code=v)
            for k, v in sorted(LOCATIONS.items())], included_buckets=['available', 'reserved'],
        location_facts_sha256=kernel.digest(sorted(ledger['facts']['stock_locations'], key=lambda r: r['id'])),
        organization_facts_sha256=kernel.digest(sorted(ledger['facts']['organizations'], key=lambda r: r['id'])),
        scope_policy='explicit_physical_location', source_quantity_policy='published_quantity_locked_separate'))


def fixtures():
    source = json.loads((ARTIFACTS/'source.json').read_text())
    ledger = json.loads((ARTIFACTS/'ledger.json').read_text())
    # Unit-only synthetic input, not a new PG capture. Native checks below use
    # fresh publications and untouched timestamps from both real readers.
    ledger['observation']['captured_at'] = (at(source['captured_at']) + timedelta(seconds=10)).isoformat()
    ledger['completed_at'] = ledger['observation']['captured_at']
    seal(ledger)
    return source, ledger, mapping_for(source, ledger)


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.source, self.ledger, self.mapping = fixtures()

    def compare(self):
        return compare_captures(day=at(self.source['captured_at']).date(), source=self.source,
                                ledger=self.ledger, mapping=self.mapping)

    def reject(self, code):
        with self.assertRaisesRegex((BridgeError, kernel.ReconciliationError), '^' + code + '$'):
            self.compare()

    def test_exact_full_outer_grains_and_locked_not_subtracted(self):
        result = self.compare()
        rows = result['comparison']['items']
        self.assertEqual(len(rows), 5)
        self.assertEqual(sorted((r['external_qty'], r['local_qty'], r['difference']) for r in rows),
                         [('0.000', '1.000', '-1.000'), ('0.000', '1.000', '-1.000'),
                          ('4.000', '0.000', '4.000'), ('5.000', '0.000', '5.000'), ('7.000', '0.000', '7.000')])
        self.assertEqual([r['locked_quantity'] for r in result['source_locked_quantities']],
                         [r['locked_quantity'] for r in sorted(self.source['rows'], key=lambda r: r['origin_id'])])
        self.assertEqual(result['comparison']['local_ledger_cursor'], 3)
        self.assertEqual(result['comparison']['capture_skew_seconds'], 10)
        self.assertFalse(result['authority_boundary']['mapping_approval_verified'])

    def test_input_documents_unchanged(self):
        original = deepcopy((self.source, self.ledger, self.mapping))
        self.compare()
        self.assertEqual((self.source, self.ledger, self.mapping), original)

    def test_raw_hash_binding(self):
        result = self.compare()
        self.assertEqual(result['input_hashes'], dict(source=self.source['content_sha256'],
            ledger=self.ledger['content_sha256'], mapping=self.mapping['content_sha256']))

    def test_historical_read_does_not_replace_source_time(self):
        self.source['database_observation']['observed_at'] = '2026-09-22T12:00:00+00:00'
        seal(self.source)
        self.assertEqual(self.compare()['comparison']['external_snapshot_at'], self.source['captured_at'])
        self.assertFalse(self.compare()['authority_boundary']['current_source_authorized'])

    def test_fresh_read_cannot_mask_stale_source(self):
        self.ledger['observation']['captured_at'] = (at(self.source['captured_at']) + timedelta(seconds=301)).isoformat()
        self.ledger['completed_at'] = self.ledger['observation']['captured_at']
        self.source['database_observation']['observed_at'] = self.ledger['completed_at']
        seal(self.source); seal(self.ledger)
        self.reject('capture_skew_exceeded')

    def test_source_hash_corruption(self):
        self.source['rows'][0]['quantity'] = '9.000'
        self.reject('source_hash')

    def test_ledger_hash_corruption(self):
        self.ledger['cutoff_cursor'] = 0
        self.reject('ledger_hash')

    def test_mapping_hash_corruption(self):
        self.mapping['included_buckets'] = ['available']
        self.reject('mapping_hash')

    def test_origin_count_mismatch_even_rehashed(self):
        self.source['rows'].pop(); seal(self.source)
        self.reject('source_origins_incomplete')

    def test_missing_empty_warehouse_page(self):
        self.source['coverage'].pop(); seal(self.source)
        self.reject('source_coverage_incomplete')

    def test_origin_wrong_position(self):
        self.source['rows'][0]['position_code'] = 'P-OTHER'; seal(self.source)
        self.reject('source_origin_outside_positions')

    def test_mapping_catalog_changed(self):
        self.mapping['catalog_sha256'] = '0' * 64; seal(self.mapping)
        self.reject('mapping_source_mismatch')

    def test_unused_location_must_be_explicit(self):
        self.mapping['location_bindings'] = [r for r in self.mapping['location_bindings'] if r['location_id'] != '108c07d8-edf4-45de-a214-d7e4f8947aef']
        seal(self.mapping); self.reject('mapping_locations_incomplete')

    def test_duplicate_location_mapping(self):
        self.mapping['location_bindings'].append(deepcopy(self.mapping['location_bindings'][0]))
        seal(self.mapping); self.reject('mapping_location_invalid')

    def test_changed_reference_requires_new_mapping(self):
        self.ledger['facts']['stock_locations'][0]['code'] = 'renamed'
        seal(self.ledger); self.reject('mapping_reference_facts_changed')

    def test_asset_owner_does_not_decide_region(self):
        before = self.compare()['comparison']['items']
        headquarters = next(r['id'] for r in self.ledger['facts']['organizations'] if r['org_type'] == 'headquarters')
        for row in self.ledger['facts']['stock_accounts']:
            row['owner_org_id'] = headquarters
        seal(self.ledger)
        self.assertEqual(self.compare()['comparison']['items'], before)

    def test_explicit_other_region_excludes_only_that_location(self):
        other = str(UUID(int=799))
        self.ledger['facts']['organizations'].append(dict(id=other, parent_id=None, org_type='region_company', status='active', code='other-region'))
        self.ledger['counts']['organizations'] += 1; seal(self.ledger)
        self.mapping['organization_facts_sha256'] = kernel.digest(sorted(self.ledger['facts']['organizations'], key=lambda r: r['id']))
        for row in self.mapping['location_bindings']:
            if row['location_id'] == '092d25d9-84a1-4eb3-a487-763fc30a61a3':
                row.update(region_id=other, warehouse_code=None)
        seal(self.mapping)
        self.assertEqual([r['local_qty'] for r in self.compare()['comparison']['items']].count('1.000'), 1)

    def test_bucket_exclusion_keeps_explanation(self):
        self.mapping['included_buckets'] = ['available']; seal(self.mapping)
        result = self.compare()['comparison']
        self.assertEqual(len(result['excluded_local_quantities']), 1)
        self.assertEqual(result['excluded_local_quantities'][0]['quantity'], '1.000')
        self.assertEqual(result['excluded_local_quantities'][0]['bucket'], 'reserved')

    def test_decimal_float_rejected(self):
        self.source['rows'][0]['quantity'] = 2.0; seal(self.source)
        self.reject('quantity_not_canonical')

    def test_ledger_rows_count_rejected(self):
        self.ledger['facts']['inventory_movements'].pop(); seal(self.ledger)
        self.reject('ledger_count_mismatch')

    def test_missing_cursor_with_recount_rejected(self):
        self.ledger['facts']['inventory_transactions'].pop()
        self.ledger['facts']['inventory_movements'].pop()
        self.ledger['counts']['inventory_transactions'] -= 1
        self.ledger['counts']['inventory_movements'] -= 1; seal(self.ledger)
        self.reject('ledger_prefix_incomplete')

    def test_head_mismatch(self):
        self.ledger['ledger_head']['next_cursor'] = 5; seal(self.ledger)
        self.reject('ledger_head_mismatch')

    def test_unmapped_account_location(self):
        self.ledger['facts']['stock_accounts'][0]['location_id'] = str(UUID(int=999)); seal(self.ledger)
        self.reject('account_reference_missing')

    def test_unsupported_quantity_policy(self):
        self.mapping['source_quantity_policy'] = 'subtract_locked'; seal(self.mapping)
        self.reject('comparison_policy_invalid')

    def test_exact_material_match_retains_reserved_quantity(self):
        accounts = {r['id']: r for r in self.ledger['facts']['stock_accounts']}
        a = accounts['181fd2e8-257e-47df-903c-fd618e7f7730']['material_id']
        b = accounts['fd052f2b-49c6-4565-b27e-0b3d37d8f180']['material_id']
        self.source['rows'] = [deepcopy(self.source['rows'][0]), deepcopy(self.source['rows'][2])]
        for row, material in zip(self.source['rows'], (a, b), strict=True):
            row.update(material_id=material, quantity='1.000', locked_quantity='0.000')
        self.source['origin_count'] = 2; seal(self.source)
        report = self.compare()['comparison']
        self.assertEqual(report['status'], 'matched')
        self.assertEqual(len(report['items']), 2)
        self.assertTrue(all(r['external_qty'] == r['local_qty'] == '1.000' for r in report['items']))

    def test_explicit_zero_source_is_compared_against_local(self):
        self.source['rows'] = []; self.source['origin_count'] = 0
        self.source['material_publications'] = []
        for row in self.source['coverage']:
            row.update(source_total=0, page_count=1)
        seal(self.source)
        report = self.compare()['comparison']
        self.assertEqual(report['status'], 'differences')
        self.assertEqual(len(report['items']), 2)
        self.assertTrue(all(r['difference'] == '-1.000' for r in report['items']))

    def test_duplicate_origin_is_not_summed_twice(self):
        self.source['rows'][1] = deepcopy(self.source['rows'][0]); seal(self.source)
        self.reject('control_origin_duplicate')

    def test_shanghai_business_date_mismatch(self):
        self.ledger['observation']['captured_at'] = '2026-09-22T00:00:00+00:00'
        self.ledger['completed_at'] = self.ledger['observation']['captured_at']; seal(self.ledger)
        self.reject('capture_day_mismatch')

    def test_transaction_with_no_movements_rejected_even_recounted(self):
        self.ledger['facts']['inventory_movements'].pop()
        self.ledger['counts']['inventory_movements'] -= 1; seal(self.ledger)
        self.reject('movement_set_incomplete')


if __name__ == '__main__':
    unittest.main(verbosity=2)
