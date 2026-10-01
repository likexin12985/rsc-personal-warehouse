"""UUID function member namespace regression on an already admitted disposable DB.

The caller supplies its owned administrator and API engines. No target discovery.
"""
from sqlalchemy import text
from app import stock_loss_return_stop_security as contract
from app.database_security import validate_production_database_security
import json

def run(admin,api):
    from app import stock_loss_correction_security as prior
    from app.database_security import DatabaseSecurityBoundaryError
    validate_production_database_security(api,expected_runtime_role='star_oam_api',expected_migration_role='star_oam_migrator')
    with admin.connect() as db:
        original_oid=db.scalar(text("SELECT 'rsc_loss_uuid_0159.uuid_generate_v5(uuid,text)'::regprocedure::oid"))
    moved=False;admission={}
    try:
        with admin.begin() as db:
            db.execute(text('CREATE SCHEMA rsc_uuid_namespace_probe AUTHORIZATION postgres'))
            db.execute(text('ALTER FUNCTION rsc_loss_uuid_0159.uuid_generate_v5(uuid,text) SET SCHEMA rsc_uuid_namespace_probe'))
        moved=True
        with api.connect() as db:
            assert db.scalar(text("SELECT n.nspname FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE p.oid=:oid"),dict(oid=original_oid))=='rsc_uuid_namespace_probe'
            for label,module in [('0159',prior),('0163',contract)]:
                try:module.verify_uuid(db)
                except ValueError:admission[label]=False
                else:admission[label]=True
            assert not db.scalar(text("SELECT has_schema_privilege(current_user,'rsc_loss_uuid_0159','USAGE')"))
            assert not db.scalar(text("SELECT has_function_privilege(current_user,:oid,'EXECUTE')"),dict(oid=original_oid))
        try:validate_production_database_security(api,expected_runtime_role='star_oam_api',expected_migration_role='star_oam_migrator')
        except DatabaseSecurityBoundaryError:admission['full_startup']=False
        else:admission['full_startup']=True
    finally:
        if moved:
            with admin.begin() as db:
                db.execute(text('ALTER FUNCTION rsc_uuid_namespace_probe.uuid_generate_v5(uuid,text) SET SCHEMA rsc_loss_uuid_0159'))
                db.execute(text('DROP SCHEMA rsc_uuid_namespace_probe'))
    validate_production_database_security(api,expected_runtime_role='star_oam_api',expected_migration_role='star_oam_migrator')
    with api.connect() as db:
        for module in (prior,contract):module.verify_uuid(db)
    assert not any(admission.values()),admission
    print('UUID extension-member relocation observation: '+json.dumps(admission),flush=True)
    return dict(passed=True,probeExpectation='reject',memberMovedWithoutChangingExtensionSchema=True,
        admission=admission,restoredOriginalNamespace=True,fullStartupBeforeAfter=True,
        apiPrivilegesUnchanged=True,formalMigrationInstalled=True,productionAcceptance=False)
