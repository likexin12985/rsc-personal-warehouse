-- Candidate only: existing-source rows are immutable under predecessor guards.
-- These checks supplement, and never replace, full source/authority/posting proof.
CREATE FUNCTION public.rsc_condition_reject_mutation() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
BEGIN
    RAISE EXCEPTION 'condition history is append only' USING ERRCODE='23514';
END $$;

CREATE FUNCTION public.rsc_condition_lock_source() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE source_id uuid;
BEGIN
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'condition writes require read committed' USING ERRCODE='23514';
    END IF;
    IF TG_TABLE_NAME = 'stock_condition_files' THEN
        SELECT inbound_line_id INTO source_id FROM public.stock_condition_events WHERE id=NEW.event_id;
    ELSE
        source_id := NEW.inbound_line_id;
    END IF;
    -- NO KEY UPDATE is compatible with FK KEY SHARE: don't upgrade an FK
    -- lock and deadlock two concurrent claims. Hold through COMMIT/rollback.
    PERFORM 1 FROM public.stock_operation_return_inbound_lines
        WHERE id=source_id FOR NO KEY UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'condition source must exist before child insertion' USING ERRCODE='23514';
    END IF;
    IF TG_TABLE_NAME = 'stock_condition_events' THEN
        IF EXISTS (
            SELECT 1 FROM public.stock_condition_events
            WHERE inbound_line_id=source_id AND event_sequence >= NEW.event_sequence
        ) THEN
            RAISE EXCEPTION 'condition event cannot be inserted into prior history' USING ERRCODE='23514';
        END IF;
    END IF;
    RETURN NEW;
END $$;

CREATE FUNCTION public.rsc_condition_check_source(source_id uuid) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE origin record; entry record; submitter record; regional record;
        original_sn_count bigint; damaged_sn_count bigint;
BEGIN
    -- Read afresh under the source lock. No caller-supplied JSON or affected
    -- quantity is treated as the original exception budget.
    SELECT l.id, l.accepted_qty, l.target_account_id, l.condition_code,
           l.receipt_line_id, r.damaged_qty, h.posting_transaction_id
      INTO origin
      FROM public.stock_operation_return_inbound_lines l
      JOIN public.stock_operation_return_inbounds h ON h.id=l.inbound_id
      JOIN public.stock_operation_receipt_lines r ON r.id=l.receipt_line_id AND r.receipt_id=h.receipt_id
      WHERE l.id=source_id AND h.plan_jsonb->>'schema_version'='1.0'
        AND l.condition_code IN ('new','used') AND l.accepted_qty=r.accepted_qty
        AND r.damaged_qty>0 AND r.damaged_qty<=r.accepted_qty;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'condition original damaged acceptance missing' USING ERRCODE='23514';
    END IF;
    SELECT count(*), count(*) FILTER (WHERE r.result='accepted' AND r.damaged
            AND r.line_id=origin.receipt_line_id AND r.serial_id=s.serial_id)
      INTO original_sn_count, damaged_sn_count
      FROM public.stock_operation_return_inbound_serials s
      LEFT JOIN public.stock_operation_receipt_serials r ON r.id=s.receipt_serial_id
      WHERE s.line_id=source_id;
    IF original_sn_count>0 AND (original_sn_count<>origin.accepted_qty OR damaged_sn_count<>origin.damaged_qty) THEN
        RAISE EXCEPTION 'condition original serial evidence incomplete' USING ERRCODE='23514';
    END IF;
    IF EXISTS (
        SELECT 1 FROM public.stock_condition_cases c WHERE c.inbound_line_id=source_id AND (
            c.affected_quantity<>origin.damaged_qty OR c.source_account_id<>origin.target_account_id
            OR c.recorded_condition<>origin.condition_code OR c.original_transaction_id<>origin.posting_transaction_id
            OR (c.tracking_mode IN ('serial','lot_and_serial')) <> (original_sn_count>0)
            OR c.quantity<>round(c.quantity, c.quantity_scale::int)
            OR (NOT c.allow_fraction AND c.quantity<>trunc(c.quantity))
            OR (SELECT count(*) FROM public.stock_condition_serials s WHERE s.case_id=c.id)
                <> CASE WHEN original_sn_count>0 THEN c.quantity ELSE 0 END
        )
    ) THEN
        RAISE EXCEPTION 'condition share does not match original exception' USING ERRCODE='23514';
    END IF;
    IF EXISTS (
        SELECT 1 FROM public.stock_condition_serials s WHERE s.inbound_line_id=source_id AND NOT EXISTS (
            SELECT 1 FROM public.stock_operation_return_inbound_serials b
            JOIN public.stock_operation_receipt_serials r ON r.id=b.receipt_serial_id
            WHERE b.line_id=source_id AND b.serial_id=s.serial_id
              AND r.line_id=origin.receipt_line_id AND r.serial_id=s.serial_id AND r.result='accepted' AND r.damaged
        )
    ) THEN
        RAISE EXCEPTION 'condition serial was not accepted damaged' USING ERRCODE='23514';
    END IF;
    -- Test every historical prefix. A later release cannot hide an earlier
    -- overclaim. Execute consumes the exception permanently; only release
    -- subtracts the claim, and its exact decision/movement is FK-bound.
    IF EXISTS (
        SELECT 1 FROM (
            SELECT sum(CASE kind WHEN 'submit' THEN quantity WHEN 'release' THEN -quantity ELSE 0 END)
                OVER (ORDER BY event_sequence ROWS UNBOUNDED PRECEDING) AS occupied
            FROM public.stock_condition_events WHERE inbound_line_id=source_id
        ) prefixes WHERE occupied<0 OR occupied>origin.damaged_qty
    ) THEN
        RAISE EXCEPTION 'condition exception budget exceeded' USING ERRCODE='23514';
    END IF;
    IF EXISTS (
        WITH intervals AS (
            SELECT s.serial_id, c.id, e.event_sequence AS opened, r.event_sequence AS released
            FROM public.stock_condition_serials s
            JOIN public.stock_condition_cases c ON c.id=s.case_id
            JOIN public.stock_condition_events e ON e.id=c.submit_event_id
            LEFT JOIN public.stock_condition_events r ON r.case_id=c.id AND r.kind='release'
            WHERE c.inbound_line_id=source_id
        )
        SELECT 1 FROM intervals a JOIN intervals b ON a.serial_id=b.serial_id AND a.id<b.id
        WHERE (a.released IS NULL OR b.opened<a.released) AND (b.released IS NULL OR a.opened<b.released)
    ) THEN
        RAISE EXCEPTION 'condition serial claimed more than once' USING ERRCODE='23514';
    END IF;
    FOR entry IN SELECT * FROM public.stock_condition_events WHERE inbound_line_id=source_id ORDER BY event_sequence LOOP
        SELECT * INTO submitter FROM public.stock_condition_events WHERE id=entry.submit_event_id;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'condition submission missing' USING ERRCODE='23514';
        END IF;
        IF entry.kind IN ('supplement','withdraw','execute','release') THEN
            IF entry.actor_user_id<>submitter.actor_user_id OR entry.actor_person_id<>submitter.actor_person_id THEN
                RAISE EXCEPTION 'condition requester identity mismatch' USING ERRCODE='23514';
            END IF;
        ELSIF entry.kind<>'submit' THEN
            IF entry.actor_user_id=submitter.actor_user_id OR entry.actor_person_id=submitter.actor_person_id THEN
                RAISE EXCEPTION 'condition requester cannot review' USING ERRCODE='23514';
            END IF;
            IF entry.kind IN ('approve_hq','reject_hq','return_region','cancel_approved') THEN
                SELECT * INTO regional FROM public.stock_condition_events
                    WHERE case_id=entry.case_id AND kind='verify_region' AND event_sequence<entry.event_sequence
                    ORDER BY event_sequence DESC LIMIT 1;
                IF NOT FOUND OR entry.actor_user_id=regional.actor_user_id OR entry.actor_person_id=regional.actor_person_id THEN
                    RAISE EXCEPTION 'condition headquarters must be independent' USING ERRCODE='23514';
                END IF;
            END IF;
        END IF;
    END LOOP;
END $$;

CREATE FUNCTION public.rsc_condition_validate_insert() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE source_id uuid;
BEGIN
    IF TG_TABLE_NAME='stock_condition_files' THEN
        SELECT inbound_line_id INTO source_id FROM public.stock_condition_events WHERE id=NEW.event_id;
    ELSE
        source_id := NEW.inbound_line_id;
    END IF;
    PERFORM public.rsc_condition_check_source(source_id);
    RETURN NULL;
END $$;
