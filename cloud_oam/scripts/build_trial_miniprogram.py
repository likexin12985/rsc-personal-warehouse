"""Build a separate, auditable trial project; never upload or edit public config."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
from urllib.parse import urlsplit
from datetime import datetime, timezone

CLOUD = Path(__file__).resolve().parents[1]
SOURCE = CLOUD / 'miniprogram'
PAGES = ('pilot-entry', 'knowledge', 'home', 'login', 'formal-personal-warehouse',
    'formal-personal-warehouse-serials', 'formal-personal-warehouse-transactions',
    'formal-material-requests', 'formal-my-receiving', 'formal-my-receipt',
    'formal-my-inbound', 'formal-notifications', 'formal-scan', 'media-preview', 'profile')


def https(value):
    if not value:
        return ''
    url = urlsplit(value)
    if (url.scheme != 'https' or not url.hostname or url.username or url.password
            or url.query or url.fragment or any(c.isspace() for c in value)):
        raise ValueError('reviewed HTTPS target required; no credentials/query/fragment')
    if url.hostname in ('localhost', '127.0.0.1', '::1'):
        raise ValueError('device candidate cannot target loopback')
    return value.rstrip('/')


def build(output, *, api_base_url='', pc_origin=''):
    api_base_url, pc_origin = https(api_base_url), https(pc_origin)
    if bool(api_base_url) != bool(pc_origin):
        raise ValueError('both reviewed targets required together')
    output = Path(output).resolve()
    if output == SOURCE or SOURCE in output.parents:
        raise ValueError('candidate must be outside the public source project')
    if output.exists() or output.with_suffix('.receipt.json').exists():
        raise ValueError('candidate output already exists; preserve its receipt')
    chosen = {Path(name) for name in ('app.js', 'app.wxss', 'sitemap.json', 'data/knowledge-catalog.json')}
    for page in PAGES:
        for suffix in ('.js', '.json', '.wxml', '.wxss'):
            relative = Path('pages') / page / ('index'+suffix)
            if (SOURCE / relative).is_file():
                chosen.add(relative)
    # Follow actual local JS dependencies, retaining underlying contracts but
    # shipping no deferred pages, tests, private project config or credentials.
    pending = list(chosen)
    while pending:
        relative = pending.pop()
        data = (SOURCE / relative).read_text() if relative.suffix in ('.js', '.wxml', '.wxss') else ''
        references = re.findall(r"require\(['\"]([^'\"]+)['\"]\)", data) if relative.suffix == '.js' else []
        for name in references:
            if not name.startswith('.'):
                raise ValueError('unreviewed external dependency: '+name)
            resolved = (SOURCE / relative.parent / name).resolve()
            if not resolved.suffix:
                resolved = resolved.with_suffix('.js')
            if SOURCE not in resolved.parents or not resolved.is_file():
                raise ValueError('invalid dependency: '+str(relative))
            child = resolved.relative_to(SOURCE)
            if child not in chosen:
                chosen.add(child); pending.append(child)
        for asset in re.findall(r'/assets/[\w./-]+\.(?:png|jpg|svg)', data):
            chosen.add(Path(asset.lstrip('/')))
    inputs = {SOURCE / relative for relative in chosen} | {
        SOURCE / 'app.json', SOURCE / 'project.config.json', Path(__file__).resolve()}
    source_files = {str(path.relative_to(CLOUD)): hashlib.sha256(path.read_bytes()).hexdigest()
                    for path in sorted(inputs)}
    output.mkdir(parents=True)
    for relative in sorted(chosen):
        target = output / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(SOURCE / relative, target)
    app = json.loads((SOURCE / 'app.json').read_text())
    app['pages'] = ['pages/'+page+'/index' for page in PAGES]
    app['window']['navigationBarTitleText'] = 'RSC个人使用记录'
    app['tabBar'] = dict(color='#647872', selectedColor='#176d63', backgroundColor='#ffffff', list=[
        dict(pagePath='pages/'+page+'/index', text=label) for page, label in (
            ('home', '工作台'), ('formal-personal-warehouse', '个人仓'),
            ('formal-material-requests', '申请'), ('profile', '我的'))])
    (output / 'app.json').write_text(json.dumps(app, ensure_ascii=False, indent=2)+'\n')
    config = json.loads((SOURCE / 'project.config.json').read_text())
    config.update(projectname='RSC个人仓试点候选', packOptions={'ignore': []})
    config['setting']['urlCheck'] = True
    (output / 'project.config.json').write_text(json.dumps(config, ensure_ascii=False, indent=2)+'\n')
    (output / 'utils/release-config.js').write_text('module.exports = '+json.dumps(
        dict(API_BASE_URL=api_base_url, PC_ORIGIN=pc_origin), ensure_ascii=False)+'\n')
    # A preview runs as develop on physical phones too: never silently use
    # their loopback address or the old production API for authentication.
    (output / 'utils/config.js').write_text(
        "const config = require('./release-config')\n"
        "module.exports = { ...config, envVersion: 'trial' }\n")
    files = {str(path.relative_to(output)): hashlib.sha256(path.read_bytes()).hexdigest()
             for path in sorted(output.rglob('*')) if path.is_file()}
    size = sum(path.stat().st_size for path in output.rglob('*') if path.is_file())
    if size > 2*1024*1024:
        raise ValueError('main package exceeds 2 MiB; candidate not ready for upload')
    if any(hashlib.sha256((CLOUD / name).read_bytes()).hexdigest() != digest
           for name, digest in source_files.items()):
        raise ValueError('source changed during build; candidate has no valid receipt')
    report = dict(scope='trial-mvp', generatedAt=datetime.now(timezone.utc).isoformat(),
        sourceFiles=source_files, sourceHead=subprocess.check_output(
        ['git', 'rev-parse', 'HEAD'], cwd=CLOUD, text=True).strip(), appId=config['appid'],
        files=files, packageBytes=size,
        packageSha256=hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest(),
        apiTargetConfigured=bool(api_base_url), apiBaseUrl=api_base_url, pcOrigin=pc_origin,
        uploaded=False, filingApproved=False, codeReviewed=False, published=False,
        limitations=['platform login and filing rejection must be read',
            'privacy declaration and registered domains must match actual platform',
            'backend PNVS and UAT remain separate acceptance gates'])
    # Sibling evidence is not uploaded as part of the project.
    output.with_suffix('.receipt.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    return report


def verify(output, *, require_api_target=False):
    """Check the exact saved package and source before any platform upload."""
    output = Path(output).resolve()
    report = json.loads(output.with_suffix('.receipt.json').read_text())
    files = {str(path.relative_to(output)): hashlib.sha256(path.read_bytes()).hexdigest()
             for path in sorted(output.rglob('*')) if path.is_file()}
    if files != report['files'] or hashlib.sha256(
            json.dumps(files, sort_keys=True).encode()).hexdigest() != report['packageSha256']:
        raise ValueError('package differs from receipt; do not upload')
    sources = report.get('sourceFiles')
    if not sources:
        raise ValueError('source binding missing; rebuild to a new candidate directory')
    for name, digest in sources.items():
        source = (CLOUD / name).resolve()
        if CLOUD not in source.parents or not source.is_file() or hashlib.sha256(
                source.read_bytes()).hexdigest() != digest:
            raise ValueError('source differs from receipt; review a new candidate')
    if require_api_target and not report['apiTargetConfigured']:
        raise ValueError('API target missing; candidate is not ready for device validation')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--api-base-url', default='')
    parser.add_argument('--pc-origin', default='')
    parser.add_argument('--verify', action='store_true')
    parser.add_argument('--require-api-target', action='store_true')
    args = parser.parse_args()
    if args.require_api_target and not args.verify:
        parser.error('--require-api-target requires --verify')
    result = verify(args.output, require_api_target=args.require_api_target) if args.verify else build(
        args.output, api_base_url=args.api_base_url, pc_origin=args.pc_origin)
    print(json.dumps({key: result[key] for key in ('sourceHead', 'appId', 'packageBytes',
        'packageSha256', 'apiTargetConfigured', 'uploaded')}, ensure_ascii=False))
