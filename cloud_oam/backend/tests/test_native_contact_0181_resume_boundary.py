"""An interrupted local proof cannot become an arbitrary PostgreSQL resume."""
from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path

import pytest


PATH = Path(__file__).resolve().parents[2] / 'scripts/run_local_pg16_contact_envelope_checks.py'
SPEC = spec_from_file_location('native_contact_runner_boundary', PATH)
RUNNER = module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)


@pytest.mark.parametrize('changes', [
    {'result': 'passed', 'exitCode': 0}, {'failurePhase': 'contact_0181_after_upgrade'},
    {'sourceUnchanged': False}, {'hostedGate': True}, {'externalProviderAccess': True},
])
def test_resume_refuses_nonlocal_or_nonterminal_upgrade_evidence(tmp_path, monkeypatch, changes):
    monkeypatch.setattr(RUNNER, 'CLOUD', tmp_path)
    folder = tmp_path / 'artifacts/linux-agent-runtime-20261009/native-contact-0181-owned'
    folder.mkdir(parents=True)
    path = folder / 'receipt.json'
    path.write_text(json.dumps(dict(schema='rsc.native-contact-0181.v1', result='failed', exitCode=1,
        failurePhase='upgrade-0181', sourceUnchanged=True, hostedGate=False,
        externalProviderAccess=False) | changes))
    with pytest.raises(ValueError, match='only the exact failed 0181 upgrade can resume'):
        RUNNER.checked_resume(path, tmp_path)


def test_resume_refuses_an_unowned_receipt_before_loading_it(tmp_path, monkeypatch):
    monkeypatch.setattr(RUNNER, 'CLOUD', tmp_path)
    with pytest.raises(ValueError, match='owned native contact receipt path required'):
        RUNNER.checked_resume(tmp_path / 'production/receipt.json', tmp_path)
