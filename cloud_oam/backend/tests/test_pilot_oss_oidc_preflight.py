from copy import deepcopy

import pytest

from test_pilot_preflight import _document
from scripts.pilot_preflight import checks_for, private_oss_oidc_configured


@pytest.mark.parametrize("key,value", [
    ("OAM_FILE_STORAGE_CREDENTIAL_MODE", "environment"),
    ("OAM_FILE_STORAGE_OIDC_ROLE_ARN", "acs:ram::9999999999999999:role/rsc-files"),
    ("OAM_FILE_STORAGE_OIDC_PROVIDER_ARN", ""),
    ("OAM_FILE_STORAGE_OIDC_TOKEN_FILE", "/run/../tmp/token"),
    ("OAM_FILE_STORAGE_OIDC_TOKEN_FILE", "/run/token"),
    ("OAM_FILE_STORAGE_OIDC_SESSION_NAME", "bad session"),
    ("OSS_ACCESS_KEY_ID", "synthetic-static"),
    ("OSS_SESSION_TOKEN", "synthetic-static"),
    ("ALIBABA_CLOUD_ACCESS_KEY_SECRET", "synthetic-static"),
])
def test_pilot_rejects_wrong_or_mixed_oss_identity(tmp_path, key, value):
    document, path = _document(tmp_path)
    api = document["services"]["api"]
    api["environment"][key] = value
    assert private_oss_oidc_configured(api) is False
    assert {r["name"]: r["ok"] for r in checks_for(document, path)}["private_oss_oidc_identity"] is False


@pytest.mark.parametrize("change", ["writable", "file", "outside_run", "ambiguous", "covering", "missing"])
def test_pilot_requires_one_exact_readonly_directory_projection(tmp_path, change):
    document, _ = _document(tmp_path)
    api = document["services"]["api"]
    mount = api["volumes"][1]
    if change == "writable": mount["read_only"] = False
    elif change == "file": mount["target"] += "/oidc.jwt"
    elif change == "outside_run": mount["source"] = "/tmp/files"
    elif change == "ambiguous": api["volumes"].append(deepcopy(mount))
    elif change == "covering": api["volumes"].append(dict(mount, target="/run"))
    elif change == "missing": api["volumes"].pop()
    assert not private_oss_oidc_configured(api)


def test_private_projection_is_only_configuration_proof(tmp_path):
    document, _ = _document(tmp_path)
    assert private_oss_oidc_configured(document["services"]["api"])
    # No real file/token, identity, request, renewal or private bucket was read.
