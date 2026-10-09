"""C1 file-boundary tests only; no prior candidate/transport suite is rerun."""

import base64
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import socket
import tempfile
import time
import traceback
from types import SimpleNamespace

import pytest

from app import openbao_registry_candidate as registry
from app.openbao_transit_candidate import (
    OpenBaoCandidateUnavailable, OpenBaoDecryptResponse, OpenBaoKeyCoordinate,
    OpenBaoReviewedPin, associated_data_b64, context_b64,
)


INSTANCE = "registry-candidate-isolated"
PURPOSES = ("authentication_idempotency", "material_request_contact")
MARKER = "sensitive-registry-marker-must-not-escape"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        pytest.fail("network forbidden in registry file tests")
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket.socket, "connect", blocked)


@pytest.fixture
def private_directory():
    # /tmp is a symlink on macOS: resolve only OUR own newly created directory.
    with tempfile.TemporaryDirectory(prefix="rsc-registry-", dir="/tmp") as name:
        directory = Path(name).resolve() / "private"
        directory.mkdir(mode=0o700)
        yield directory


def material(purpose=PURPOSES[0], version=1):
    coordinate = OpenBaoKeyCoordinate(purpose, "test", INSTANCE, version)
    cipher = "vault:v2:" + base64.b64encode(hashlib.sha512(f"{purpose}/{version}".encode()).digest()[:60]).decode()
    item = {
        "purpose": purpose, "environment": "test", "provider_instance_id": INSTANCE,
        "application_key_version": version, "key_path": coordinate.key_path,
        "transit_key_version": 2, "ciphertext": cipher,
        "context_b64": context_b64(coordinate),
        "associated_data_b64": associated_data_b64(coordinate),
    }
    # External manifest fixture, never obtained from the registry loader.
    pin = OpenBaoReviewedPin(
        coordinate, 2, hashlib.sha256(cipher.encode()).hexdigest(),
        hashlib.sha256(base64.b64decode(item["context_b64"])).hexdigest(),
        hashlib.sha256(base64.b64decode(item["associated_data_b64"])).hexdigest(),
    )
    return item, pin


def document(items=None):
    return {"schema": registry.REGISTRY_SCHEMA, "provider": "openbao_transit_v1",
            "entries": items if items is not None else [material()[0]]}


def write_file(directory, content=None):
    path = directory / "registry.json"
    if content is None:
        content = document()
    path.write_bytes(json.dumps(content).encode() if isinstance(content, (dict, list)) else content)
    path.chmod(0o600)
    return path


class Transport:
    def __init__(self):
        self.calls = []

    def decrypt(self, *, request, timeout_seconds):
        self.calls.append((request, timeout_seconds))
        return OpenBaoDecryptResponse(200, {"data": {
            "plaintext": base64.b64encode(hashlib.sha256(request.ciphertext.encode()).digest()).decode()
        }})


def load(path, *, pins=None, transport=None, **kwargs):
    return registry.load_openbao_registry_candidate(
        registry_path=path, environment=kwargs.pop("environment", "test"),
        provider_instance_id=kwargs.pop("provider_instance_id", INSTANCE),
        reviewed_pins=(material()[1],) if pins is None else pins,
        transport=transport or Transport(), **kwargs,
    )


def test_exact_history_two_purposes_and_no_decrypt_during_load(private_directory):
    pairs = [material(purpose, version) for purpose in PURPOSES for version in (1, 2)]
    path = write_file(private_directory, document([item for item, _ in pairs]))
    transport = Transport()
    candidate = load(path, pins=tuple(pin for _, pin in pairs), transport=transport)
    assert transport.calls == []
    path.unlink()  # Loaded immutable snapshot; no implicit reload on resolve.
    for item, _ in reversed(pairs):
        assert candidate.resolve(item["purpose"], item["application_key_version"]) == hashlib.sha256(item["ciphertext"].encode()).digest()
        request, budget = transport.calls[-1]
        assert request.ciphertext == item["ciphertext"] and budget == 3.0
    with pytest.raises(OpenBaoCandidateUnavailable):
        candidate.resolve(PURPOSES[0], 3)


@pytest.mark.parametrize("content", [
    b'{"schema":"first","schema":"second"}',
    b'{"schema":"first","\\u0073chema":"second"}',
    b'{"entries":[{"purpose":"first","purpose":"second"}]}',
    b'{"entries":NaN}', b'{"entries":Infinity}', b'{"entries":1.5}',
    b'{"entries":11111111111}', b'{"entries":true}',
    b'{"entries":', b'{} trailing', b'\xef\xbb\xbf{}', b'\xff',
    b'[]', b'null', b'"string"', b'{"value":"unescaped\x00"}',
    b'[' * 9 + b'0' + b']' * 9,
], ids=["duplicate_root", "escaped_duplicate", "duplicate_nested", "nan", "infinity",
        "float", "large_integer", "wrong_type", "malformed", "trailing", "bom", "utf8",
        "array", "null", "string", "control", "depth"])
def test_invalid_json_is_rejected_before_transport(private_directory, content):
    path = write_file(private_directory, content)
    transport = Transport()
    with pytest.raises(registry.OpenBaoRegistryCandidateError):
        load(path, transport=transport)
    assert transport.calls == []


@pytest.mark.parametrize("field,value", [
    ("schema", "rsc.openbao.wrapped-data-key-registry.v2"),
    ("provider", "aliyun_kms"), ("entries", []), ("entries", {}),
    ("entries", ["not-an-entry"]), ("entries", [material()[0]] * 65),
    ("reviewed_pins", [{"ciphertext_sha256": "0" * 64}]),
    ("plaintext_key", MARKER),
])
def test_registry_shape_is_closed_including_no_self_declared_pins(private_directory, field, value):
    doc = document()
    doc[field] = value
    with pytest.raises(registry.OpenBaoRegistryCandidateError):
        load(write_file(private_directory, doc))


@pytest.mark.parametrize("field,value", [
    ("purpose", "other"), ("environment", "production"),
    ("provider_instance_id", "different-instance"),
    ("application_key_version", True), ("application_key_version", 0),
    ("application_key_version", "1"), ("application_key_version", 2_147_483_648),
    ("transit_key_version", 1), ("transit_key_version", False),
    ("key_path", "transit/keys/rsc-material-request-contact"),
    ("ciphertext", "vault:v01:" + "A" * 80),
    ("context_b64", "wrong-context"), ("associated_data_b64", "wrong-aad"),
    ("kms_key_version_id", MARKER), ("plaintext", MARKER),
])
def test_entry_metadata_cannot_override_candidate_contract(private_directory, field, value):
    item, _ = material()
    item[field] = value
    with pytest.raises(registry.OpenBaoRegistryCandidateError):
        load(write_file(private_directory, document([item])))


def test_missing_field_duplicate_version_and_mismatched_external_pin(private_directory):
    item, pin = material()
    missing = dict(item)
    missing.pop("context_b64")
    for doc, pins in (
        (document([missing]), (pin,)),
        (document([item, item]), (pin, pin)),
        (document(), (replace(pin, ciphertext_sha256="0" * 64),)),
    ):
        with pytest.raises(registry.OpenBaoRegistryCandidateError):
            load(write_file(private_directory, doc), pins=pins)


@pytest.mark.parametrize("pins", [(), [], ("unreviewed",), {"pin": "from-file"}])
def test_external_pins_required_before_file_io(monkeypatch, pins):
    def forbidden(*args, **kwargs):
        pytest.fail("invalid pins must be rejected before file access")
    monkeypatch.setattr(registry.os, "open", forbidden)
    with pytest.raises(registry.OpenBaoRegistryCandidateError):
        load("/does-not-exist/registry.json", pins=pins)


@pytest.mark.parametrize("field,value", [("environment", "production"), ("provider_instance_id", "other-instance")])
def test_expected_scope_remains_external(private_directory, field, value):
    with pytest.raises(registry.OpenBaoRegistryCandidateError):
        load(write_file(private_directory), **{field: value})


@pytest.mark.parametrize("mode", [0o400, 0o640, 0o644, 0o666, 0o4600])
def test_file_must_be_exact_owner_0600(private_directory, mode):
    path = write_file(private_directory)
    path.chmod(mode)
    with pytest.raises(registry.OpenBaoRegistryCandidateError):
        load(path)


@pytest.mark.parametrize("kind", ["leaf_symlink", "ancestor_symlink", "hardlink", "directory", "fifo"])
def test_non_regular_or_linked_registry_is_rejected_without_blocking(private_directory, kind):
    path = write_file(private_directory)
    if kind == "leaf_symlink":
        target = path.with_name("original.json")
        path.rename(target)
        path.symlink_to(target)
    elif kind == "ancestor_symlink":
        alias = private_directory.parent / "alias"
        alias.symlink_to(private_directory, target_is_directory=True)
        path = alias / path.name
    elif kind == "hardlink":
        os.link(path, path.with_name("linked.json"))
    else:
        path.unlink()
        if kind == "directory":
            path.mkdir(mode=0o600)
        else:
            os.mkfifo(path, 0o600)
    start = time.monotonic()
    with pytest.raises(registry.OpenBaoRegistryCandidateError):
        load(path)
    assert time.monotonic() - start < 0.5


@pytest.mark.parametrize("mode", [0o750, 0o755, 0o770, 0o777])
def test_final_parent_is_owner_private(private_directory, mode):
    path = write_file(private_directory)
    private_directory.chmod(mode)
    with pytest.raises(registry.OpenBaoRegistryCandidateError):
        load(path)


def test_unsafe_ancestor_rejected_even_when_final_parent_is_private(private_directory):
    path = write_file(private_directory)
    private_directory.parent.chmod(0o777)
    with pytest.raises(registry.OpenBaoRegistryCandidateError):
        load(path)
    private_directory.parent.chmod(0o700)


@pytest.mark.parametrize("form", ["relative", "dot", "parent", "double_separator", "nul"])
def test_path_is_not_normalized_or_resolved_to_hide_links(private_directory, form):
    path = write_file(private_directory)
    candidates = {
        "relative": "registry.json", "dot": str(path.parent) + "/./" + path.name,
        "parent": str(path.parent) + "/../private/" + path.name,
        "double_separator": str(path.parent) + "//" + path.name,
        "nul": str(path) + "\x00",
    }
    with pytest.raises(registry.OpenBaoRegistryCandidateError):
        load(candidates[form])


@pytest.mark.parametrize("size", [0, registry.MAX_REGISTRY_BYTES + 1])
def test_size_limit_checked_before_content_read(private_directory, monkeypatch, size):
    path = write_file(private_directory, b" " * size)
    def forbidden(*args):
        pytest.fail("invalid size must fail before read")
    monkeypatch.setattr(registry.os, "read", forbidden)
    with pytest.raises(registry.OpenBaoRegistryCandidateError):
        load(path)


def test_maximum_size_is_bounded_and_valid_padding_is_accepted(private_directory, monkeypatch):
    raw = json.dumps(document()).encode()
    path = write_file(private_directory, raw + b" " * (registry.MAX_REGISTRY_BYTES - len(raw)))
    original = os.read
    sizes = []
    def observed(fd, size):
        sizes.append(size)
        return original(fd, size)
    monkeypatch.setattr(registry.os, "read", observed)
    load(path)
    assert max(sizes) <= 64 * 1024
    assert sum(sizes) == registry.MAX_REGISTRY_BYTES + 1


@pytest.mark.parametrize("field", ["st_uid", "st_mode", "st_nlink"])
def test_descriptor_metadata_checked_independently_of_path(private_directory, monkeypatch, field):
    path = write_file(private_directory)
    inode = path.stat().st_ino
    original = os.fstat
    def changed(fd):
        info = original(fd)
        if info.st_ino != inode:
            return info
        values = {name: getattr(info, name) for name in ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns")}
        values[field] = {"st_uid": os.geteuid() + 1, "st_mode": info.st_mode | 0o044, "st_nlink": 2}[field]
        return SimpleNamespace(**values)
    monkeypatch.setattr(registry.os, "fstat", changed)
    with pytest.raises(registry.OpenBaoRegistryCandidateError):
        load(path)


@pytest.mark.parametrize("change", ["grow", "truncate", "same_size", "replace_leaf", "replace_parent"])
def test_concurrent_changes_rejected_after_read(private_directory, monkeypatch, change):
    raw = json.dumps(document()).encode() + b"\n"
    path = write_file(private_directory, raw)
    original = os.read
    applied = False
    def changed(fd, size):
        nonlocal applied
        result = original(fd, size)
        if not applied:
            applied = True
            if change == "grow":
                path.write_bytes(raw + b" ")
            elif change == "truncate":
                path.write_bytes(raw[:-1])
            elif change == "same_size":
                prior = path.stat()
                path.write_bytes(raw[:-1] + b" ")
                os.utime(path, ns=(prior.st_atime_ns, prior.st_mtime_ns))
            elif change == "replace_leaf":
                replacement = path.with_name("new.json")
                replacement.write_bytes(raw)
                replacement.chmod(0o600)
                replacement.replace(path)
            else:
                moved = private_directory.with_name("moved")
                private_directory.rename(moved)
                private_directory.mkdir(mode=0o700)
                write_file(private_directory, raw)
        return result
    monkeypatch.setattr(registry.os, "read", changed)
    with pytest.raises(registry.OpenBaoRegistryCandidateError):
        load(path)
    assert applied


def test_partial_reads_are_supported_without_extra_plaintext_or_pin_fields(private_directory, monkeypatch):
    path = write_file(private_directory)
    original = os.read
    monkeypatch.setattr(registry.os, "read", lambda fd, size: original(fd, min(size, 7)))
    load(path)


def test_failures_close_all_fds_and_have_no_raw_exception_chain(private_directory, monkeypatch, caplog, capsys):
    path = write_file(private_directory, b'{"secret":"' + MARKER.encode())
    opened = []
    original = os.open
    def observed(*args, **kwargs):
        fd = original(*args, **kwargs)
        opened.append(fd)
        return fd
    monkeypatch.setattr(registry.os, "open", observed)
    with pytest.raises(registry.OpenBaoRegistryCandidateError) as caught:
        load(path)
    for fd in opened:
        with pytest.raises(OSError):
            os.fstat(fd)
    assert caught.value.__context__ is None and caught.value.__cause__ is None
    captured = capsys.readouterr()
    rendered = "".join(traceback.format_exception(caught.value))
    assert MARKER not in rendered + caplog.text + captured.out + captured.err
    assert str(path) not in str(caught.value)


def test_file_candidate_requires_explicit_inputs_and_provider_defaults_stay_disabled():
    import ast
    import inspect
    from app.config import Settings

    for field in (
        "auth_idempotency_encryption_provider",
        "material_request_contact_encryption_provider",
    ):
        assert Settings.model_fields[field].default == "disabled"
    parameters = inspect.signature(registry.load_openbao_registry_candidate).parameters
    for field in (
        "registry_path", "environment", "provider_instance_id", "reviewed_pins", "transport",
    ):
        assert parameters[field].default is inspect.Parameter.empty
        assert parameters[field].kind is inspect.Parameter.KEYWORD_ONLY
    source = inspect.getsource(registry)
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        (node.module or "").split(".")[0]
        for node in ast.walk(ast.parse(source)) if isinstance(node, ast.ImportFrom)
    }
    assert not imported.intersection({
        "socket", "http", "requests", "httpx", "config", "database", "sqlalchemy",
        "production_adapters", "production_key_runtime", "openbao_runtime_transport",
    })
