"""The filing candidate must contain private flows without replacing public code."""
import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / 'scripts/build_trial_miniprogram.py'
spec = importlib.util.spec_from_file_location('trial_package', SCRIPT)
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


def test_isolated_package_retains_real_flows_and_refuses_unknown_backend(tmp_path):
    before = (builder.SOURCE / 'project.config.json').read_bytes()
    output = tmp_path / 'candidate'
    result = builder.build(output)
    app = json.loads((output / 'app.json').read_text())
    assert app['pages'][0] == 'pages/pilot-entry/index'
    assert {'pages/login/index', 'pages/formal-material-requests/index',
        'pages/formal-my-receipt/index', 'pages/formal-my-inbound/index'} <= set(app['pages'])
    assert not any('stocktake' in page or 'stock-return' in page or 'work-order' in page for page in app['pages'])
    assert not (output / 'tests').exists()
    assert not (output / 'project.private.config.json').exists()
    assert (output / 'utils/api.js').exists()
    assert (output / 'utils/client-capabilities.js').exists()
    assert '127.0.0.1' not in (output / 'utils/config.js').read_text()
    assert not result['apiTargetConfigured'] and not result['uploaded']
    assert result['packageBytes'] < 2*1024*1024
    assert result['sourceFiles']['scripts/build_trial_miniprogram.py']
    assert builder.verify(output) == result
    with pytest.raises(ValueError, match='API target missing'):
        builder.verify(output, require_api_target=True)
    assert (builder.SOURCE / 'project.config.json').read_bytes() == before
    with pytest.raises(ValueError, match='already exists'):
        builder.build(output)


@pytest.mark.parametrize('url', ['http://example.com/api', 'https://u:p@example.com/api',
    'https://127.0.0.1/api', 'https://example.com/api?token=secret'])
def test_unreviewable_backend_rejected_before_writing(tmp_path, url):
    output = tmp_path / 'candidate'
    with pytest.raises(ValueError):
        builder.build(output, api_base_url=url, pc_origin='https://example.com/xx')
    assert not output.exists()


def test_upload_verification_rejects_package_tampering(tmp_path):
    output = tmp_path / 'candidate'
    builder.build(output, api_base_url='https://example.com/api', pc_origin='https://example.com')
    assert builder.verify(output, require_api_target=True)['apiTargetConfigured']
    with (output / 'utils/release-config.js').open('a') as file:
        file.write('// changed after review\n')
    with pytest.raises(ValueError, match='package differs'):
        builder.verify(output)


def test_upload_verification_rejects_source_drift(tmp_path, monkeypatch):
    output = tmp_path / 'candidate'
    builder.build(output)
    monkeypatch.setattr(builder, 'CLOUD', tmp_path / 'different-source')
    with pytest.raises(ValueError, match='source differs'):
        builder.verify(output)


def test_build_preserves_existing_receipt(tmp_path):
    output = tmp_path / 'candidate'
    receipt = output.with_suffix('.receipt.json')
    receipt.write_text('previous evidence')
    with pytest.raises(ValueError, match='already exists'):
        builder.build(output)
    assert receipt.read_text() == 'previous evidence'
    assert not output.exists()
