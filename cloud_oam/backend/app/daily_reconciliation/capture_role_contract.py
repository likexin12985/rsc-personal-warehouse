"""Fixed database principals for internal daily capture; no user-selected ACLs."""

CONTROL_ROLE = 'rsc_control_capture'
LEDGER_ROLE = 'rsc_reconciliation_capture'
CONTROL_TABLES = tuple(sorted((
    'inventory_control_preparations', 'inventory_control_source_bindings',
    'inventory_control_catalog_versions', 'inventory_control_capture_chains',
    'inventory_control_capture_snapshots', 'inventory_control_authority_decisions',
    'inventory_control_mapping_decisions', 'inventory_control_capture_attestations',
    'external_sync_snapshots', 'external_sync_snapshot_batches', 'external_sync_snapshot_records',
    'control_projection_publications', 'control_projection_lines', 'control_projection_origins',
    'control_projection_closures', 'external_objects', 'external_object_versions',
    'sync_runs', 'sync_batches', 'sync_inbox_events', 'audit_events', 'audit_chain_heads',
    'material_projection_publications', 'material_projection_lines',
    'oam_material_capture_receipts', 'materials', 'material_inventory_policies',
)))
LEDGER_TABLES = ('inventory_ledger_heads', 'inventory_transactions', 'inventory_movements',
                 'stock_accounts', 'stock_locations', 'organizations')
ROLES = {
    CONTROL_ROLE: (CONTROL_TABLES, 'rsc_control_capture_select'),
    LEDGER_ROLE: (LEDGER_TABLES, 'rsc_daily_capture_select'),
}
ALL_TABLES = tuple(sorted(set(CONTROL_TABLES) | set(LEDGER_TABLES)))

# Values are reviewed identifiers, never request data. Used inside the API's
# private-table ACL query; the complete role contract is checked separately.
PRIVATE_SELECT_EXCEPTION_SQL = '(' + ' OR '.join(
    "(COALESCE(grantee.rolname,'')='" + role + "' AND class_row.relname IN (" +
    ','.join("'" + table + "'" for table in tables) + '))'
    for role, (tables, _) in ROLES.items()
) + ") AND a.privilege_type='SELECT' AND NOT a.is_grantable"
