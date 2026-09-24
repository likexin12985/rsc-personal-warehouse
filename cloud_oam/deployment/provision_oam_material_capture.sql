\set ON_ERROR_STOP on

-- Infrastructure transport registration only. No SKU/source business approval.
-- Default is preview. registration_json contains only non-secret exact metadata:
-- action, id, source_system_id, source_instance, key_id, key_fingerprint,
-- valid_from, valid_to. Never put RSC_EDGE_SYNC_SECRET or an OAM token here.
\if :{?registration_json}
\else
\echo 'registration_json is required'
SELECT 1 / 0 AS missing_material_registration;
\endif
\if :{?apply}
\else
\set apply PREVIEW
\endif

BEGIN;
SELECT set_config('rsc.material_registration_request', :'registration_json', true);
SELECT set_config('rsc.material_registration_apply', :'apply', true);
DO $body$
DECLARE
    q jsonb := current_setting('rsc.material_registration_request')::jsonb;
    existing public.oam_material_capture_bindings%ROWTYPE;
    source public.source_systems%ROWTYPE;
    observed timestamptz;
    apply_mode boolean := current_setting('rsc.material_registration_apply')='REGISTER_TRANSPORT_ONLY';
BEGIN
    IF current_user<>'star_oam_migrator' OR session_user<>'star_oam_migrator' THEN
        RAISE EXCEPTION 'material registration requires direct migration identity'; END IF;
    IF (SELECT count(*) FROM public.alembic_version WHERE version_num='20261108_0129')<>1 THEN
        RAISE EXCEPTION 'material registration requires reviewed migration 0121'; END IF;
    IF jsonb_typeof(q) IS DISTINCT FROM 'object' OR (SELECT count(*) FROM jsonb_object_keys(q))<>8
       OR NOT q ?& ARRAY['action','id','source_system_id','source_instance','key_id','key_fingerprint','valid_from','valid_to']
       OR EXISTS (SELECT 1 FROM jsonb_each(q) x WHERE jsonb_typeof(x.value)<>'string')
       OR q->>'action' NOT IN ('register','revoke')
       OR q->>'source_instance' !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
       OR q->>'key_id' !~ '^[A-Za-z0-9._:-]{1,128}$' OR q->>'key_fingerprint' !~ '^[a-f0-9]{64}$'
       OR (q->>'valid_to')::timestamptz<=(q->>'valid_from')::timestamptz
       OR NOT isfinite((q->>'valid_from')::timestamptz) OR NOT isfinite((q->>'valid_to')::timestamptz)
    THEN RAISE EXCEPTION 'invalid exact material registration metadata'; END IF;
    SELECT * INTO existing FROM public.oam_material_capture_bindings WHERE id=(q->>'id')::uuid FOR UPDATE;
    IF FOUND THEN
        IF existing.source_system_id IS DISTINCT FROM (q->>'source_system_id')::uuid
           OR existing.source_instance IS DISTINCT FROM q->>'source_instance'
           OR existing.key_id IS DISTINCT FROM q->>'key_id' OR existing.key_fingerprint IS DISTINCT FROM q->>'key_fingerprint'
           OR existing.valid_from IS DISTINCT FROM (q->>'valid_from')::timestamptz
           OR existing.valid_to IS DISTINCT FROM (q->>'valid_to')::timestamptz
        THEN RAISE EXCEPTION 'existing material registration differs; refusing replacement'; END IF;
        IF apply_mode AND q->>'action'='revoke' AND existing.revoked_at IS NULL THEN
            UPDATE public.oam_material_capture_bindings SET revoked_at=clock_timestamp() WHERE id=existing.id;
        END IF;
    ELSE
        IF q->>'action'='revoke' THEN RAISE EXCEPTION 'exact material registration not found'; END IF;
        SELECT * INTO source FROM public.source_systems WHERE id=(q->>'source_system_id')::uuid FOR SHARE;
        IF NOT FOUND OR source.code<>'oam' OR source.mode<>'read_only' OR NOT source.enabled THEN
            RAISE EXCEPTION 'exact active read-only OAM source required'; END IF;
        observed:=clock_timestamp();
        IF (q->>'valid_from')::timestamptz<observed THEN
            RAISE EXCEPTION 'new material registration cannot be backdated'; END IF;
        IF apply_mode THEN
            INSERT INTO public.oam_material_capture_bindings
                (id,created_at,source_system_id,source_instance,key_id,key_fingerprint,valid_from,valid_to)
            VALUES ((q->>'id')::uuid,observed,source.id,q->>'source_instance',q->>'key_id',q->>'key_fingerprint',
                (q->>'valid_from')::timestamptz,(q->>'valid_to')::timestamptz);
        END IF;
    END IF;
END;
$body$;
SELECT CASE WHEN current_setting('rsc.material_registration_apply')='REGISTER_TRANSPORT_ONLY'
            THEN 'applied_or_existing' ELSE 'preview_only' END AS mode,
       current_setting('rsc.material_registration_request')::jsonb AS exact_request,
       (SELECT to_jsonb(binding) FROM public.oam_material_capture_bindings binding
        WHERE id=(current_setting('rsc.material_registration_request')::jsonb->>'id')::uuid) AS persisted_registration,
       false AS source_business_authorized, false AS projection_published;
COMMIT;
