-- Retain all three immutable legacy aliases. A dedicated nullable column binds
-- the real recovery namespace without disguising it as a legacy reversal key.
ALTER TABLE public.stock_loss_request_key_bindings ADD COLUMN recovery_key_hash varchar(64);
ALTER TABLE public.stock_loss_request_key_bindings ADD COLUMN scrap_key_hash varchar(64);
ALTER TABLE public.stock_loss_request_key_bindings ADD CONSTRAINT ck_loss_binding_recovery_0165 CHECK (
    recovery_key_hash IS NULL OR (binding_kind='inverse' AND recovery_key_hash ~ '^[a-f0-9]{64}$'
        AND recovery_key_hash NOT IN (reversal_key_hash,approval_key_hash,correction_key_hash)));
ALTER TABLE public.stock_loss_request_key_bindings ADD CONSTRAINT uq_loss_binding_recovery_0165 UNIQUE (recovery_key_hash);
ALTER TABLE public.stock_loss_request_key_bindings ADD CONSTRAINT ck_loss_binding_scrap_0165 CHECK (
    scrap_key_hash IS NULL OR (binding_kind='correction' AND recovery_key_hash IS NULL
        AND scrap_key_hash ~ '^[a-f0-9]{64}$'
        AND scrap_key_hash NOT IN (reversal_key_hash,approval_key_hash,correction_key_hash)));
ALTER TABLE public.stock_loss_request_key_bindings ADD CONSTRAINT uq_loss_binding_scrap_0165 UNIQUE (scrap_key_hash);
-- Existing table privileges continue to prohibit API INSERT/UPDATE/DELETE.
-- The only writer remains the typed, client-key-verifying SECDEF registrar.
DO $install$
DECLARE name text;
BEGIN
    FOREACH name IN ARRAY ARRAY['stock_scrap_recovery_requests','stock_scrap_recovery_regional_reviews',
        'stock_scrap_recovery_headquarters_reviews','stock_scrap_recovery_executions'] LOOP
        EXECUTE format('CREATE CONSTRAINT TRIGGER trg_recovery_binding_fence_0165 AFTER INSERT ON public.%I '
            'DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.rsc_fence_loss_request_binding_0159()',name);
        EXECUTE format('ALTER TABLE public.%I ENABLE ALWAYS TRIGGER trg_recovery_binding_fence_0165',name);
    END LOOP;
END;
$install$;
