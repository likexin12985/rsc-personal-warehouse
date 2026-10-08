"""Verify already downloaded public 2.7.1 artifacts, then extract only bao.

Requires PGpy==0.6.0 in a disposable verifier environment, never the application.
No downloads, execution, key generation or service startup occur here.
"""
import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import tarfile
import warnings

import pgpy

PRIMARY = "66D15FDD87287219C8E15478D200CD702853E6D0"
SIGNER = "E617DCD4065C2AFC0B2CF7A7BA8BC08C0F691F94"
EXPECTED = {
    "darwin_arm64": "15625b5f69aee5bb4578b4e76e856a2141647342b0f8e5969a8875b44e0fbf91",
    "linux_amd64": "0e2f1ce10d124e03112b50dd2fbec6b78003783253bc3a91587938f39d1e2243",
}


def require(condition, message):
    if not condition:
        raise SystemExit(message)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--downloads", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    verifier_version = importlib.metadata.version("PGPy")
    require(verifier_version == "0.6.0", "unreviewed PGpy verifier version")
    require(not args.downloads.is_symlink(), "symlink downloads directory rejected")
    require(not args.manifest.is_symlink(), "symlink manifest rejected")
    for target in EXPECTED:
        destination = args.downloads / target
        require(not destination.is_symlink() and not (destination / "bao").is_symlink(),
                "symlink extraction target rejected")
    for filename in ("openbao-gpg-pub-20240618.asc", "checksums.txt", "checksums.txt.gpgsig"):
        require(not (args.downloads / filename).is_symlink(), "symlink public input rejected")
    key, _ = pgpy.PGPKey.from_file(str(args.downloads / "openbao-gpg-pub-20240618.asc"))
    signature = pgpy.PGPSignature.from_file(str(args.downloads / "checksums.txt.gpgsig"))
    checksums = (args.downloads / "checksums.txt").read_bytes()
    require(str(key.fingerprint) == PRIMARY, "official primary fingerprint mismatch")
    require(signature.signer == SIGNER[-16:], "official signing key mismatch")
    require(str(key.subkeys[signature.signer].fingerprint) == SIGNER, "official signer fingerprint mismatch")
    require(not key.is_expired and not key.subkeys[signature.signer].is_expired, "public key expired")
    # PGPy's warnings concern automatic self-signature/revocation evaluation.
    # We independently pin BOTH published primary and actual signing subkey.
    with warnings.catch_warnings(record=True) as seen:
        verified = key.verify(checksums, signature)
    require(bool(verified), "checksums detached signature invalid")
    indexed = {}
    for line in checksums.decode().splitlines():
        digest, filename = line.split(None, 1)
        filename = filename.lstrip("*")
        require(filename not in indexed, "duplicate checksum filename")
        indexed[filename] = digest
    result = {
        "version": "2.7.1", "releasePublishedAt": "2026-10-01T14:03:19Z",
        "releaseUrl": "https://github.com/openbao/openbao/releases/tag/v2.7.1",
        "officialFingerprintSource": "https://openbao.org/docs/install/",
        "primaryFingerprint": PRIMARY, "signingSubkeyFingerprint": SIGNER,
        "checksumsSha256": hashlib.sha256(checksums).hexdigest(),
        "detachedSignatureVerified": True, "verifier": "PGPy " + verifier_version,
        "signatureAssurance": "cryptographic signature plus pinned published primary and signing-subkey fingerprints",
        "revocationStatusFromExternalKeyserverVerified": False,
        "artifacts": {}, "productionReady": False,
    }
    for target, expected in EXPECTED.items():
        filename = "openbao_2.7.1_" + target + ".tar.gz"
        archive = args.downloads / filename
        require(not archive.is_symlink(), "symlink archive input rejected")
        actual = hashlib.sha256(archive.read_bytes()).hexdigest()
        require(actual == expected == indexed[filename], "archive does not match signed checksum and API metadata")
        with tarfile.open(archive, "r:gz") as tar:
            candidates = [m for m in tar.getmembers() if m.isfile() and Path(m.name).name == "bao"]
            require(len(candidates) == 1, "ambiguous executable member")
            binary = tar.extractfile(candidates[0]).read()
        destination = args.downloads / target
        destination.mkdir(mode=0o700, exist_ok=True)
        path = destination / "bao"
        path.write_bytes(binary)
        path.chmod(0o700)
        result["artifacts"][target] = {"archive": filename, "archive_sha256": actual,
                                       "binary_sha256": hashlib.sha256(binary).hexdigest()}
    args.manifest.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
