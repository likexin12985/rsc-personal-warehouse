"""Admit regional transit locations to reviewed opening stocktake graphs.

All predecessor bodies remain frozen. Patches pin both full source digests and
exact occurrence counts; owner, ACL, security mode and search path are retained.
"""
import hashlib
from pathlib import Path
import runpy
from alembic import op

revision = "20261012_0102"
down_revision = "20261011_0101"
branch_labels = depends_on = None

# signature: (before SHA256, after SHA256, exact source replacements)
SOURCE_CHANGES = {'rsc_guard_stocktake_start_completion_0047()': ('19fcb84567c36bbcf426eac4f844bfa70e32ee3247f50c402a444171c5879718',
                                                 '469383df84072fa66ae4f8f5ad48f6230b2e2e186dc7a3a2f08eca775da35a73',
                                                 (("current_location_type NOT IN ('region', 'personal')",
                                                   "current_location_type NOT IN ('region', 'personal', "
                                                   "'transit')",
                                                   1),
                                                  ("target_location_type = 'region'",
                                                   "target_location_type IN ('region', 'transit')",
                                                   1))),
 'rsc_lock_opening_stocktake_start_reference_0027(uuid, uuid[], uuid[], uuid[], timestamp with time zone)': ('dbd0d5a71b839a4c31a8defc7089177c2d4c37409cd323710b302f6b1ba1a2e9',
                                                                                                             'd1a01b4490625242176d86f1083f3313980122d23ddaea00110e7849296eefae',
                                                                                                             (('location.location_type '
                                                                                                               'IN '
                                                                                                               "('region', "
                                                                                                               "'personal')",
                                                                                                               'location.location_type '
                                                                                                               'IN '
                                                                                                               "('region', "
                                                                                                               "'personal', "
                                                                                                               "'transit')",
                                                                                                               1),)),
 'rsc_oam_runtime_binding_ready_0044()': ('61bff09975f03d0c8049253fc733692d6d3f5b4a633bb537e9418a96b582544a',
                                          '5fa738c93218aa8f0de434935f7c6a8f94ec5e71c99a9a283df3b64b142d0de9',
                                          (('20261011_0101', '20261012_0102', 1),)),
 'rsc_opening_observation_disposition_complete_0052(uuid, boolean)': ('4a5006029f95b5425704ba2b994c0ee2ff242c74e4e5c1660d368a400ffe5b0b',
                                                                      '772a8dcd77022ee213e4c92b928a067b39f9873e5d8d0c0061259428932c3aa4',
                                                                      (('current_location.location_type NOT IN (\n'
                                                                        "                       'region', "
                                                                        "'personal'\n"
                                                                        '                   )',
                                                                        'current_location.location_type NOT IN (\n'
                                                                        "                       'region', "
                                                                        "'personal', 'transit'\n"
                                                                        '                   )',
                                                                        1),
                                                                       ('current_location.location_type = '
                                                                        "'region'",
                                                                        'current_location.location_type IN '
                                                                        "('region', 'transit')",
                                                                        1))),
 'rsc_opening_recount_complete_0052(uuid, boolean)': ('b3a26276b6b46f8e7bcbbfbd5a33f9e8171be582f471ed330dba69ea85ebcd0f',
                                                      '6dfa7e632c72989968e6adbfd2d152b29ed9af5401170797d7106a3ee2159f34',
                                                      (('current_location.location_type NOT IN (\n'
                                                        "                       'region', 'personal'\n"
                                                        '                   )',
                                                        'current_location.location_type NOT IN (\n'
                                                        "                       'region', 'personal', 'transit'\n"
                                                        '                   )',
                                                        1),
                                                       ("current_location.location_type = 'region'",
                                                        "current_location.location_type IN ('region', 'transit')",
                                                        1))),
 'rsc_opening_review_complete_0052(uuid, boolean)': ('f6b614e42e35da34e072cd12ba103688163a53fc763d3c48979883813369126a',
                                                     '295b5b8f381fa614820840d99d2a002f7007556d0bf359b20fba4a518cd3ef30',
                                                     (('current_location.location_type NOT IN (\n'
                                                       "                       'region', 'personal'\n"
                                                       '                   )',
                                                       'current_location.location_type NOT IN (\n'
                                                       "                       'region', 'personal', 'transit'\n"
                                                       '                   )',
                                                       1),
                                                      ("current_location.location_type = 'region'",
                                                       "current_location.location_type IN ('region', 'transit')",
                                                       1))),
 'rsc_opening_round_submission_complete_0052(uuid, uuid, boolean)': ('d57abd63b6be3b13ac0c19786f76fcc6e7eeaf4ec4dc9bd84d45fbe2452ff206',
                                                                     '004ac7f4e84a75cd1b64697d3f75a58278c1b182a48c8985e3a2edc2eb733eb8',
                                                                     (('resolution_location.location_type = '
                                                                       "'region'",
                                                                       'resolution_location.location_type IN '
                                                                       "('region', 'transit')",
                                                                       6),)),
 'rsc_opening_scope_count_complete_0052(uuid, uuid, uuid, boolean)': ('e0c2628cdf871c1b4e7adb8234fdce6ef1dd3ee9dc80930b8cb71684f670b1e7',
                                                                      '7bb94dc398ac0b8a5592df324d71d0f3f2ea0c485fd596715c859612b123ee2a',
                                                                      (('current_location.location_type NOT IN (\n'
                                                                        "                       'region', "
                                                                        "'personal'\n"
                                                                        '                   )',
                                                                        'current_location.location_type NOT IN (\n'
                                                                        "                       'region', "
                                                                        "'personal', 'transit'\n"
                                                                        '                   )',
                                                                        1),
                                                                       ('current_location.location_type = '
                                                                        "'region'",
                                                                        'current_location.location_type IN '
                                                                        "('region', 'transit')",
                                                                        1),
                                                                       ('resolution_location.location_type = '
                                                                        "'region'",
                                                                        'resolution_location.location_type IN '
                                                                        "('region', 'transit')",
                                                                        6),
                                                                       ("scope_location.location_type = 'region'",
                                                                        'scope_location.location_type IN '
                                                                        "('region', 'transit')",
                                                                        4))),
 'rsc_opening_start_graph_complete_0052(uuid, boolean)': ('6db62f66efe1b87c556211e9392ab701cd2d8c6fa2c86150c1b95ee0d7f15833',
                                                          '54075228a80150ff15822991e7340576dd9a50d05ea148ab9dbb5082a7848905',
                                                          (('start_location.location_type NOT IN (\n'
                                                            "                               'region', 'personal'\n"
                                                            '                           )',
                                                            'start_location.location_type NOT IN (\n'
                                                            "                               'region', 'personal', "
                                                            "'transit'\n"
                                                            '                           )',
                                                            1),
                                                           ("start_location.location_type = 'region'",
                                                            "start_location.location_type IN ('region', "
                                                            "'transit')",
                                                            6))),
 'rsc_opening_terminal_side_effects_complete_0052(uuid, boolean)': ('d816a65969b6112edb19a6d817918eded8d470f0e2ccb3087fabd9f55dac426d',
                                                                    'ed779f492d00838b9cff21cbd385676c3e4c98eafb94c6fd0127ab0f3db750cc',
                                                                    (('current_location.location_type NOT IN (\n'
                                                                      "                       'region', "
                                                                      "'personal'\n"
                                                                      '                   )',
                                                                      'current_location.location_type NOT IN (\n'
                                                                      "                       'region', "
                                                                      "'personal', 'transit'\n"
                                                                      '                   )',
                                                                      1),
                                                                     ("current_location.location_type = 'region'",
                                                                      'current_location.location_type IN '
                                                                      "('region', 'transit')",
                                                                      1))),
 'rsc_require_opening_count_write_current_0052()': ('1eba1b857922dbe1b60a9e7b3c98c5b2250162057262c8849a504441bebeb537',
                                                    '69310a342366ae78aac2fab7164328b626e6cccec0fb2bb254b8744ebf677e88',
                                                    (('current_location.location_type NOT IN (\n'
                                                      "                       'region', 'personal'\n"
                                                      '                   )',
                                                      'current_location.location_type NOT IN (\n'
                                                      "                       'region', 'personal', 'transit'\n"
                                                      '                   )',
                                                      4),
                                                     ("completion_location.location_type = 'region'",
                                                      "completion_location.location_type IN ('region', 'transit')",
                                                      1),
                                                     ("count_location.location_type = 'region'",
                                                      "count_location.location_type IN ('region', 'transit')",
                                                      12),
                                                     ("current_location.location_type = 'region'",
                                                      "current_location.location_type IN ('region', 'transit')",
                                                      4),
                                                     ("serial_location.location_type = 'region'",
                                                      "serial_location.location_type IN ('region', 'transit')",
                                                      6))),
 'rsc_require_opening_terminal_graph_0022()': ('4678c65493a2ca0c8d596343053977db4d93d7758e00f1291e30e6be038d36e8',
                                               '64563f1fc6f374bc41c9c06b6f20960bbb9598120b7f2d698ff4abdef9b066b0',
                                               (('current_location.location_type NOT IN (\n'
                                                 "                       'region', 'personal'\n"
                                                 '                   )',
                                                 'current_location.location_type NOT IN (\n'
                                                 "                       'region', 'personal', 'transit'\n"
                                                 '                   )',
                                                 1),
                                                ("current_location.location_type = 'region'",
                                                 "current_location.location_type IN ('region', 'transit')",
                                                 1))),
 'rsc_validate_stocktake_scope_region_owner_0025()': ('904a443c2c5930356af0f15f444f29ec6b6ce61f32294e4b2e3b40dd3a0e4e8e',
                                                      'db45b52dbf1146800044ec85f700053749879de20ca521db690bbd2665fcdb0f',
                                                      (("current_location_type NOT IN ('region', 'personal')",
                                                        "current_location_type NOT IN ('region', 'personal', "
                                                        "'transit')",
                                                        1),))}

GUARD_NAME = "rsc_guard_transit_opening_location_0102"
GUARD_BODY = """
DECLARE
    location public.stock_locations%ROWTYPE;
    parent public.stock_locations%ROWTYPE;
BEGIN
    IF TG_TABLE_NAME = 'stocktake_scopes' THEN
        SELECT * INTO location FROM public.stock_locations WHERE id=NEW.location_id FOR SHARE;
        IF location.location_type <> 'transit' THEN RETURN NEW; END IF;
        SELECT * INTO parent FROM public.stock_locations WHERE id=location.parent_id FOR SHARE;
        IF NOT FOUND OR location.status <> 'active' OR parent.status <> 'active'
           OR parent.location_type <> 'region' OR parent.owner_org_id <> location.owner_org_id THEN
            RAISE EXCEPTION '0102 transit opening requires its active regional parent' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END IF;
    IF EXISTS (SELECT 1 FROM public.stocktake_scopes scope WHERE scope.location_id=OLD.id)
       AND OLD.location_type='transit' THEN
        IF TG_OP='DELETE' THEN
            RAISE EXCEPTION '0102 transit opening history must be retained' USING ERRCODE='23514';
        END IF;
        IF (NEW.id,NEW.parent_id,NEW.owner_org_id,NEW.location_type,NEW.status)
           IS DISTINCT FROM (OLD.id,OLD.parent_id,OLD.owner_org_id,OLD.location_type,OLD.status) THEN
            RAISE EXCEPTION '0102 established transit coordinates are immutable' USING ERRCODE='23514';
        END IF;
    END IF;
    IF EXISTS (SELECT 1 FROM public.stock_locations child JOIN public.stocktake_scopes scope
            ON scope.location_id=child.id WHERE child.parent_id=OLD.id AND child.location_type='transit') THEN
        IF TG_OP='DELETE' THEN
            RAISE EXCEPTION '0102 established transit parent must be retained' USING ERRCODE='23514';
        END IF;
        IF (NEW.id,NEW.parent_id,NEW.owner_org_id,NEW.location_type,NEW.status)
           IS DISTINCT FROM (OLD.id,OLD.parent_id,OLD.owner_org_id,OLD.location_type,OLD.status) THEN
            RAISE EXCEPTION '0102 established transit parent coordinates are immutable' USING ERRCODE='23514';
        END IF;
    END IF;
    IF TG_OP='DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END;
"""
GUARD_HASH = hashlib.sha256(GUARD_BODY.encode()).hexdigest()
TRIGGERS = {
    "trg_stocktake_scopes_transit_opening_0102": ("stocktake_scopes", "INSERT", 7),
    "trg_stock_locations_transit_opening_0102": ("stock_locations", "UPDATE OR DELETE", 27),
}


def _replace_source(signature, before, after, replacements):
    expected_config = "search_path=pg_catalog" if signature == "rsc_oam_runtime_binding_ready_0044()" else "search_path=pg_catalog, public"
    edits = []
    for old, new, count in replacements:
        edits.append(f"""
        IF (length(source)-length(replace(source,$old${old}$old$,'')))/length($old${old}$old$) <> {count}
           OR strpos(source,$new${new}$new$) <> 0 THEN
            RAISE EXCEPTION '0102 source occurrence drift: {signature}';
        END IF;
        source := replace(source,$old${old}$old$,$new${new}$new$);
        definition := replace(definition,$old${old}$old$,$new${new}$new$);
        """)
    op.execute(f"""DO $migration$
    DECLARE
        function_oid oid := to_regprocedure('public.{signature}');
        owner_oid oid; original_acl aclitem[]; security_mode boolean; config text[];
        source text; definition text;
    BEGIN
        SELECT proowner,proacl,prosecdef,proconfig,prosrc,pg_get_functiondef(oid)
        INTO owner_oid,original_acl,security_mode,config,source,definition FROM pg_proc
        WHERE oid=function_oid AND encode(sha256(convert_to(prosrc,'UTF8')),'hex')='{before}';
        IF current_user <> 'star_oam_migrator' OR session_user <> current_user OR source IS NULL
           OR owner_oid <> (SELECT oid FROM pg_roles WHERE rolname=current_user)
           OR config IS DISTINCT FROM ARRAY['{expected_config}'] THEN
            RAISE EXCEPTION '0102 function prerequisite drift: {signature}';
        END IF;
        {''.join(edits)}
        IF encode(sha256(convert_to(source,'UTF8')),'hex') <> '{after}' THEN
            RAISE EXCEPTION '0102 transformed source drift: {signature}';
        END IF;
        EXECUTE definition;
        IF to_regprocedure('public.{signature}') IS DISTINCT FROM function_oid
           OR NOT EXISTS (SELECT 1 FROM pg_proc WHERE oid=function_oid AND proowner=owner_oid
                AND proacl IS NOT DISTINCT FROM original_acl AND prosecdef=security_mode
                AND proconfig IS NOT DISTINCT FROM config
                AND encode(sha256(convert_to(prosrc,'UTF8')),'hex')='{after}') THEN
            RAISE EXCEPTION '0102 function replacement drift: {signature}';
        END IF;
    END $migration$""")


def _transition(up):
    db = op.get_bind()
    if db.dialect.name not in {"postgresql", "sqlite"}:
        raise RuntimeError("0102 supports PostgreSQL and SQLite only")
    helper = runpy.run_path(str(Path(__file__).with_name("20260927_0087_inbound_fulfillment_boundary.py")))
    helper["_begin_sqlite"]()
    if db.dialect.name == "postgresql":
        op.execute("LOCK TABLE public.alembic_version IN ACCESS EXCLUSIVE MODE")
        op.execute("LOCK TABLE public.stock_locations, public.stocktake_tasks, public.stocktake_scopes IN SHARE ROW EXCLUSIVE MODE")
    if not up:
        helper["_preflight"]("EXISTS (SELECT 1 FROM stocktake_scopes scope JOIN stock_locations location ON location.id=scope.location_id WHERE location.location_type='transit')",
            "0102 downgrade blocked: transit stocktake history must be retained")
    if db.dialect.name != "postgresql":
        return
    if up:
        op.execute(f"CREATE FUNCTION public.{GUARD_NAME}() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $body${GUARD_BODY}$body$")
        op.execute(f"REVOKE ALL ON FUNCTION public.{GUARD_NAME}() FROM PUBLIC, star_oam_api")
        for name, (table, events, _) in TRIGGERS.items():
            op.execute(f"CREATE TRIGGER {name} BEFORE {events} ON public.{table} FOR EACH ROW EXECUTE FUNCTION public.{GUARD_NAME}()")
            op.execute(f"ALTER TABLE public.{table} ENABLE ALWAYS TRIGGER {name}")
    else:
        op.execute(f"""DO $migration$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_proc WHERE oid='public.{GUARD_NAME}()'::regprocedure
                AND encode(sha256(convert_to(prosrc,'UTF8')),'hex')='{GUARD_HASH}'
                AND proowner=(SELECT oid FROM pg_roles WHERE rolname=current_user) AND prosecdef
                AND proconfig=ARRAY['search_path=pg_catalog, public']
                AND NOT EXISTS (SELECT 1 FROM aclexplode(COALESCE(proacl,acldefault('f',proowner))) acl WHERE acl.grantee<>proowner)) THEN
                RAISE EXCEPTION '0102 transit guard source, owner or ACL drift';
            END IF;
        END $migration$""")
        for name, (table, *_) in TRIGGERS.items():
            op.execute(f"DROP TRIGGER {name} ON public.{table}")
        op.execute(f"DROP FUNCTION public.{GUARD_NAME}()")
    for signature, (before, after, changes) in SOURCE_CHANGES.items():
        _replace_source(signature, before if up else after, after if up else before,
            changes if up else tuple((new, old, count) for old, new, count in reversed(changes)))


def upgrade(): _transition(True)
def downgrade(): _transition(False)
