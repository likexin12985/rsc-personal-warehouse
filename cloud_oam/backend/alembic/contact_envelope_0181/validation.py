"""Frozen v2 contact SQL, independent of application/provider implementations.

The predecessor's v1 expression is retained verbatim by the caller. SQLite is
schema tooling only: it cannot recompute SHA-256 or authenticate ciphertext.
PostgreSQL binds the complete business AAD and the immutable provider claim.
"""

SCHEMA = 'rsc.material_request_contact.v2'
PROVIDER = 'openbao_transit_v1'
PURPOSE = 'material_request_contact'
KEY_PATH = 'transit/keys/rsc-material-request-contact'
KEYS = ('schema', 'provider', 'purpose', 'environment', 'provider_instance_id',
        'key_path', 'application_key_version', 'transit_key_version',
        'ciphertext_b64', 'nonce_b64', 'aad_sha256', 'mobile_hmac', 'contact_hmac')
ENVIRONMENTS = ('development', 'test', 'staging', 'production')
VERSION_KEYS = ('application_key_version', 'transit_key_version')
STRING_KEYS = tuple(key for key in KEYS if key not in VERSION_KEYS)


def _quoted(values):
    return ', '.join("'" + value + "'" for value in values)


def postgres_aad(document, request_id, person_id):
    parts = ["convert_to('cloud_oam.material_request.contact.envelope.v2', 'UTF8')"]
    for key in ('provider', 'purpose', 'environment', 'provider_instance_id',
                'key_path', *VERSION_KEYS):
        parts.extend(["decode('00', 'hex')",
                      f"convert_to('{key}=' || ({document}->>'{key}'), 'UTF8')"])
    for key, value in (('request_id', request_id), ('requester_person_id', person_id)):
        parts.extend(["decode('00', 'hex')", f"convert_to('{key}=' || ({value})::text, 'UTF8')"])
    return "encode(sha256(" + ' || '.join(parts) + "), 'hex')"


def postgres_v2_invalid(document, request_id, person_id):
    value = lambda key: f"({document}->>'{key}')"
    terms = [f"jsonb_typeof({document}) IS DISTINCT FROM 'object'",
             f"(CASE WHEN jsonb_typeof({document}) = 'object' THEN "
             f"(SELECT count(*) FROM jsonb_object_keys({document})) ELSE -1 END) <> {len(KEYS)}",
             f"NOT ({document} ?& ARRAY[{_quoted(KEYS)}])"]
    terms.extend(f"jsonb_typeof({document}->'{key}') IS DISTINCT FROM 'string'" for key in STRING_KEYS)
    terms.extend([f"{value('schema')} <> '{SCHEMA}'", f"{value('provider')} <> '{PROVIDER}'",
                  f"{value('purpose')} <> '{PURPOSE}'", f"{value('key_path')} <> '{KEY_PATH}'",
                  f"{value('environment')} NOT IN ({_quoted(ENVIRONMENTS)})",
                  f"{value('provider_instance_id')} !~ '^[a-z0-9][a-z0-9-]{{2,62}}$'"])
    # CASE is intentional: hostile JSON must never reach a numeric cast merely
    # because PostgreSQL chose a different OR evaluation order.
    versions = {}
    for key in VERSION_KEYS:
        versions[key] = (f"(CASE WHEN jsonb_typeof({document}->'{key}') = 'number' "
                         f"AND {value(key)} ~ '^[1-9][0-9]{{0,9}}$' "
                         f"THEN {value(key)}::bigint END)")
        terms.append(f"COALESCE({versions[key]} BETWEEN 1 AND 2147483647, false) IS NOT TRUE")
    ciphertext, nonce = value('ciphertext_b64'), value('nonce_b64')
    decoded_ciphertext = (f"(CASE WHEN jsonb_typeof({document}->'ciphertext_b64') = 'string' "
        f"AND {ciphertext} ~ '^[A-Za-z0-9+/]+={{0,2}}$' AND length({ciphertext}) % 4 = 0 "
        f"THEN decode({ciphertext}, 'base64') END)")
    decoded_nonce = (f"(CASE WHEN jsonb_typeof({document}->'nonce_b64') = 'string' "
        f"AND {nonce} ~ '^[A-Za-z0-9+/]{{16}}$' THEN decode({nonce}, 'base64') END)")
    terms.extend([
        f"{ciphertext} !~ '^[A-Za-z0-9+/]+={{0,2}}$'",
        f"length({ciphertext}) < 24 OR length({ciphertext}) % 4 <> 0",
        f"octet_length({decoded_ciphertext}) < 17",
        f"replace(encode({decoded_ciphertext}, 'base64'), E'\\n', '') IS DISTINCT FROM {ciphertext}",
        f"{nonce} !~ '^[A-Za-z0-9+/]{{16}}$'",
        f"octet_length({decoded_nonce}) <> 12",
        f"{value('aad_sha256')} !~ '^[0-9a-f]{{64}}$'",
        f"{request_id} IS NULL OR {person_id} IS NULL",
        f"{request_id} = '00000000-0000-0000-0000-000000000000'::uuid",
        f"{person_id} = '00000000-0000-0000-0000-000000000000'::uuid",
        f"{value('aad_sha256')} IS DISTINCT FROM {postgres_aad(document, request_id, person_id)}",
    ])
    terms.extend(f"{value(key)} !~ '^hmac:[1-9][0-9]{{0,9}}:[0-9a-f]{{64}}$'"
                 for key in ('mobile_hmac', 'contact_hmac'))
    terms.append(f"""(SELECT count(*) FROM public.openbao_data_key_pins AS contact_pin
JOIN public.application_key_version_claims AS contact_claim
  ON contact_claim.purpose = contact_pin.purpose
 AND contact_claim.application_key_version = contact_pin.application_key_version
 AND contact_claim.provider = '{PROVIDER}'
 AND contact_claim.ciphertext_sha256 = contact_pin.ciphertext_sha256
 AND contact_claim.created_at = contact_pin.created_at
WHERE contact_pin.purpose = '{PURPOSE}'
  AND contact_pin.environment = {value('environment')}
  AND contact_pin.provider_instance_id = {value('provider_instance_id')}
  AND contact_pin.key_path = '{KEY_PATH}'
  AND contact_pin.application_key_version = {versions['application_key_version']}
  AND contact_pin.transit_key_version = {versions['transit_key_version']}) <> 1""")
    return '\nOR '.join(terms)


def sqlite_v2_invalid(document):
    value = lambda key: f"json_extract({document}, '$.{key}')"
    terms = [f"json_valid({document}) IS NOT 1", f"json_type({document}) IS NOT 'object'",
             f"(SELECT count(*) FROM json_each({document})) <> {len(KEYS)}"]
    terms.extend(f"json_type({document}, '$.{key}') IS NOT 'text'" for key in STRING_KEYS)
    terms.extend(f"instr({value(key)}, char(0)) <> 0" for key in STRING_KEYS)
    terms.extend([f"{value('schema')} <> '{SCHEMA}'", f"{value('provider')} <> '{PROVIDER}'",
                  f"{value('purpose')} <> '{PURPOSE}'", f"{value('key_path')} <> '{KEY_PATH}'",
                  f"{value('environment')} NOT IN ({_quoted(ENVIRONMENTS)})",
                  f"length({value('provider_instance_id')}) NOT BETWEEN 3 AND 63",
                  f"substr({value('provider_instance_id')}, 1, 1) = '-'",
                  f"{value('provider_instance_id')} GLOB '*[^a-z0-9-]*'"])
    for key in VERSION_KEYS:
        terms.extend([f"json_type({document}, '$.{key}') IS NOT 'integer'",
                      f"{value(key)} NOT BETWEEN 1 AND 2147483647"])
    ciphertext, nonce = value('ciphertext_b64'), value('nonce_b64')
    terms.extend([
        f"length({ciphertext}) < 24 OR length({ciphertext}) % 4 <> 0",
        f"{ciphertext} GLOB '*[^A-Za-z0-9+/=]*'",
        f"length({ciphertext}) - length(rtrim({ciphertext}, '=')) > 2",
        f"instr(rtrim({ciphertext}, '='), '=') <> 0",
        # Seventeen authenticated bytes require at least 24 base64 characters;
        # exactly 24 characters may contain at most one padding character.
        f"(length({ciphertext}) = 24 AND substr({ciphertext}, -2) = '==')",
        f"length({nonce}) <> 16 OR {nonce} GLOB '*[^A-Za-z0-9+/]*'",
        f"length({value('aad_sha256')}) <> 64 OR {value('aad_sha256')} GLOB '*[^0-9a-f]*'",
    ])
    for key in ('mobile_hmac', 'contact_hmac'):
        item = value(key)
        separator = f"instr(substr({item}, 6), ':')"
        version = f"substr({item}, 6, {separator} - 1)"
        digest = f"substr({item}, 6 + {separator})"
        terms.extend([f"substr({item}, 1, 5) <> 'hmac:'", f"{separator} NOT BETWEEN 2 AND 11",
                      f"substr({version}, 1, 1) NOT GLOB '[1-9]'", f"{version} GLOB '*[^0-9]*'",
                      f"length({digest}) <> 64 OR {digest} GLOB '*[^0-9a-f]*'"])
    terms.append(f"""(SELECT count(*) FROM openbao_data_key_pins AS contact_pin
JOIN application_key_version_claims AS contact_claim
  ON contact_claim.purpose = contact_pin.purpose
 AND contact_claim.application_key_version = contact_pin.application_key_version
 AND contact_claim.provider = '{PROVIDER}'
 AND contact_claim.ciphertext_sha256 = contact_pin.ciphertext_sha256
 AND contact_claim.created_at = contact_pin.created_at
WHERE contact_pin.purpose = '{PURPOSE}'
  AND contact_pin.environment = {value('environment')}
  AND contact_pin.provider_instance_id = {value('provider_instance_id')}
  AND contact_pin.key_path = '{KEY_PATH}'
  AND contact_pin.application_key_version = {value('application_key_version')}
  AND contact_pin.transit_key_version = {value('transit_key_version')}) <> 1""")
    return '\nOR '.join(terms)


def dual_invalid(legacy, document, request_id=None, person_id=None, *, sqlite=False):
    if sqlite:
        schema, provider = (f"json_extract({document}, '$.{key}')" for key in ('schema', 'provider'))
        current = sqlite_v2_invalid(document)
    else:
        schema, provider = (f"{document}->>'{key}'" for key in ('schema', 'provider'))
        current = postgres_v2_invalid(document, request_id, person_id)
    return (f"CASE WHEN {schema} = 'rsc.material_request_contact.v1' AND {provider} = 'aliyun_kms' "
            f"THEN ({legacy})\nWHEN {schema} = '{SCHEMA}' AND {provider} = '{PROVIDER}' "
            f"THEN COALESCE(({current}), true)\nELSE true END")
