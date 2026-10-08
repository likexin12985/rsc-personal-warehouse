"""Native regressions in the caller's freshly owned local cluster only."""
from sqlalchemy import text
from app import database_security as security
from app import stock_scrap_security as scrap
from app.stock_scrap_security_probe import snapshot

def run(admin, api):
    def admission():
        security.validate_production_database_security(api,
            expected_runtime_role='star_oam_api',expected_migration_role='star_oam_migrator')
    with admin.connect() as db:
        baseline=snapshot(db)
    cases=[
        ('capture_update','GRANT UPDATE ON public.audit_events TO rsc_control_capture','REVOKE UPDATE ON public.audit_events FROM rsc_control_capture'),
        ('capture_grant_option','GRANT SELECT ON public.audit_events TO rsc_control_capture WITH GRANT OPTION','REVOKE GRANT OPTION FOR SELECT ON public.audit_events FROM rsc_control_capture'),
        ('capture_wrong_table','GRANT SELECT ON public.stock_scrap_request_seals TO rsc_control_capture','REVOKE SELECT ON public.stock_scrap_request_seals FROM rsc_control_capture'),
        ('capture_missing_select','REVOKE SELECT ON public.audit_events FROM rsc_control_capture','GRANT SELECT ON public.audit_events TO rsc_control_capture'),
        ('capture_superuser','ALTER ROLE rsc_control_capture SUPERUSER','ALTER ROLE rsc_control_capture NOSUPERUSER'),
        ('capture_membership','GRANT star_oam_api TO rsc_control_capture','REVOKE star_oam_api FROM rsc_control_capture'),
        ('capture_readwrite_default','ALTER ROLE rsc_control_capture SET default_transaction_read_only=off','ALTER ROLE rsc_control_capture SET default_transaction_read_only=on'),
        ('public_select','GRANT SELECT ON public.audit_events TO PUBLIC','REVOKE SELECT ON public.audit_events FROM PUBLIC'),
        ('api_extra_delete','GRANT DELETE ON public.audit_events TO star_oam_api','REVOKE DELETE ON public.audit_events FROM star_oam_api'),
        ('capture_column_acl','GRANT SELECT(id) ON public.audit_events TO rsc_control_capture','REVOKE SELECT(id) ON public.audit_events FROM rsc_control_capture'),
    ]
    rejected=[]
    admission()
    for label, mutate, restore in cases:
        with admin.begin() as db:db.execute(text(mutate))
        try:
            try:admission()
            except security.DatabaseSecurityBoundaryError:rejected.append(label)
            else:raise AssertionError('unsafe catalog admitted: '+label)
        finally:
            with admin.begin() as db:db.execute(text(restore))
        with admin.connect() as db:assert snapshot(db)==baseline,label
        admission()
    return dict(passed=True,configuredRolesAccepted=True,rejected=rejected,exactRestoration=True)
