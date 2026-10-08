"""Optional browser tail for the runner's newly owned, synthetic PG16 cluster.

Never mount this in the product application: it injects a fixture identity.
Only loopback and same-origin writes to the current receipt/inbound are allowed.
"""
import hashlib
import json
import re
import threading
import time
from unittest.mock import patch

import uvicorn
from fastapi import Depends, FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.dependencies import get_current_user
from app.formal_access import load_formal_principal
from app.models import User
from app.routers import auth
from test_material_request_draft_service import SECRET

ORIGIN = 'http://127.0.0.1:18087'


def frontend_manifest(cloud):
    build = cloud / 'frontend/dist-warehouse'
    for required in ('index.html', 'sw.js', 'manifest.webmanifest', 'assets'):
        if not (build / required).exists():
            raise ValueError('Build the warehouse frontend before --browser-tail')
    paths = [p for name in ('src', 'public', 'dist-warehouse')
             for p in (cloud / 'frontend' / name).rglob('*') if p.is_file()]
    return {str(p.relative_to(cloud)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}


def browser_application(mounted, cloud, *, api, admin, directory, request_id, closure_only=False, cancellation_only=False, personal_only=False, warehouse_return_id=None, supply_task_id=None, supply_create_only=False):
    if sum((closure_only, cancellation_only, personal_only, warehouse_return_id is not None, supply_task_id is not None, supply_create_only)) > 1:
        raise ValueError('select one browser write scope')
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None,
        exception_handlers=dict(mounted.exception_handlers), middleware=list(mounted.user_middleware))
    app.include_router(mounted.router)
    prefix = '/api/v1/material-requests/' + str(request_id)
    allowed_get = {'/api/auth/me', '/api/access/context', '/api/v1/material-requests', prefix}
    allowed_get.update(prefix + '/' + suffix for suffix in (
        'inbound-orders', 'receipts', 'receipt-command-status', 'shipments',
        'shipment-options', 'oam-receipt-evidence', 'completion-quantities', 'remaining-fulfillment',
        'closure', 'close-command-status', 'remaining-cancellation', 'cancel-remaining-command-status',
        'my-receiving', 'my-inbounds/candidates', 'my-receipts/command-status', 'my-receipts/trace-status',
        'my-inbounds/command-status', 'my-inbounds/trace-status'))
    allowed_post = ({prefix + '/my-receipts', prefix + '/my-inbounds'} if personal_only else
        {prefix + '/cancel-remaining'} if cancellation_only else
        {prefix + '/close'} if closure_only else {prefix + '/receipts', prefix + '/inbound-orders'})
    supply_path = None
    if supply_task_id is not None:
        from uuid import UUID
        supply_path = prefix + "/supply-tasks/" + str(UUID(str(supply_task_id)))
        allowed_get.update({"/api/v1/material-request-supply-command-status",
                            prefix + "/return-compensations/candidates"})
        allowed_post = {supply_path}
    if supply_create_only:
        allowed_get.update({prefix + '/supply-planning-capacity',
            prefix + '/return-compensations/candidates', '/api/v1/material-request-supply-command-status'})
        allowed_post = {prefix + '/supply-tasks'}
    warehouse = None
    if warehouse_return_id is not None:
        from uuid import UUID
        warehouse = '/api/v1/rejection-returns/' + str(UUID(str(warehouse_return_id)))
        allowed_get = {'/api/auth/me', '/api/access/context', '/api/v1/rejection-returns/my-warehouse',
            warehouse + '/warehouse', warehouse + '/warehouse-receipts/command-status'}
        allowed_post = {warehouse + '/warehouse-receipts'}
    observed = []

    @app.middleware('http')
    async def boundary(request: Request, call_next):
        path = request.url.path
        is_get = request.method == 'GET' and (path.startswith(('/xx/', '/brand/'))
            or path in allowed_get or (warehouse is None and (re.fullmatch(re.escape(prefix) + r'/shipments/[0-9a-f-]{36}/logistics-events', path)
            or re.fullmatch(re.escape(prefix) + r'/my-receiving/[0-9a-f-]{36}/candidates', path)))
            or (warehouse is not None and re.fullmatch(re.escape(warehouse) + r'/warehouse-receipts/[0-9a-f-]{36}/inbounds/(preview|command-status)', path)))
        is_post = request.method == 'POST' and (path in allowed_post
            or (not closure_only and not cancellation_only and not personal_only and warehouse is None and supply_path is None and not supply_create_only
                and re.fullmatch(re.escape(prefix) + r'/inbound-orders/[0-9a-f-]{36}/post', path))
            or (warehouse is not None and re.fullmatch(re.escape(warehouse) + r'/warehouse-receipts/[0-9a-f-]{36}/inbounds', path)))
        if (request.client is None or request.client.host not in ('127.0.0.1', '::1')
                or request.headers.get('host') != '127.0.0.1:18087'
                or request.headers.get('sec-fetch-site') == 'cross-site'
                or not (is_get or is_post)
                or (is_post and request.headers.get('origin') != ORIGIN)):
            return Response(status_code=403, headers={'Cache-Control': 'no-store'})
        response = await call_next(request)
        response.headers['Cache-Control'] = 'private, no-store, max-age=0'
        response.headers['Referrer-Policy'] = 'no-referrer'
        observed.append({'method': request.method, 'path': path, 'status': response.status_code})
        (directory / 'browser-requests.json').write_text(json.dumps(observed, indent=2) + '\n')
        return response

    def database():
        with Session(api) as db:
            yield db

    def identity(request: Request, db: Session = Depends(get_db)):
        request.state.formal_principal = load_formal_principal(db, admin)
        return db.get(User, admin)

    settings = get_settings().model_copy(update={'material_request_writes_enabled': True,
        'material_request_idempotency_hmac_secret': SECRET.decode()})
    app.dependency_overrides = {**mounted.dependency_overrides,
        get_db: database, get_current_user: identity, get_settings: lambda: settings}
    app.mount('/xx/assets', StaticFiles(directory=cloud / 'frontend/dist-warehouse/assets'))
    app.mount('/brand', StaticFiles(directory=cloud / 'frontend/public/brand'))

    @app.get('/xx/sw.js')
    def worker():
        return FileResponse(cloud / 'frontend/dist-warehouse/sw.js', media_type='application/javascript')

    @app.get('/xx/manifest.webmanifest')
    def manifest():
        return FileResponse(cloud / 'frontend/dist-warehouse/manifest.webmanifest', media_type='application/manifest+json')

    @app.get('/xx/{path:path}')
    def ui(path: str):
        html = (cloud / 'frontend/dist-warehouse/index.html').read_text()
        scope = '剩余供给计划创建' if supply_create_only else '供给计划更新与取消' if supply_path else '拒收退回来源仓验收与独立入账' if warehouse else '本人收货与入账' if personal_only else '取消剩余需求' if cancellation_only else '业务关闭' if closure_only else '收货与入账'
        notice = f'<aside style="background:#fff2ca;color:#614700;padding:12px">本地联验 · 隔离 PostgreSQL 16 合成数据及测试身份。仅开放当前测试单的{scope}操作，不是正式环境或用户验收。</aside>'
        return HTMLResponse(html.replace('<body>', '<body>' + notice))

    return app, observed


def serve(mounted, cloud, *, api, admin, directory, request_id, shipment=None, serial_ids=(), timeout_seconds=1200, closure_only=False, cancellation_only=False, personal_only=False, warehouse_return_id=None, supply_task_id=None, supply_create_only=False):
    if not 60 <= timeout_seconds <= 3600:
        raise ValueError('browser timeout must be between 60 and 3600 seconds')
    sources = frontend_manifest(cloud)
    if personal_only:
        from app.demand_models import MaterialRequest
        with Session(api) as db:
            admin = db.get(MaterialRequest, request_id).requester_user_id
    (directory / 'browser-frontend-manifest.json').write_text(json.dumps(sources, indent=2) + '\n')
    app, observed = browser_application(mounted, cloud, api=api, admin=admin,
        directory=directory, request_id=request_id, closure_only=closure_only, cancellation_only=cancellation_only, personal_only=personal_only, warehouse_return_id=warehouse_return_id, supply_task_id=supply_task_id, supply_create_only=supply_create_only)
    prefix = '/api/v1/material-requests/' + str(request_id)
    with patch.object(auth, 'settings', auth.settings.model_copy(update={'environment': 'production'})):
        server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=18087,
            lifespan='off', log_level='warning', access_log=False))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 15
            while not server.started:
                if not thread.is_alive() or time.monotonic() > deadline:
                    raise RuntimeError('browser tail did not start')
                time.sleep(.1)
            ready = {'url': ORIGIN + ('/xx/rejection-return-receiving' if warehouse_return_id else '/xx/material-requests'), 'requestId': str(request_id),
                'shipment': shipment, 'serialIds': [str(value) for value in serial_ids],
                'directory': str(directory), 'syntheticIdentity': True,
                'supplyTaskId': str(supply_task_id) if supply_task_id else None,
                'browserWriteScope': ['supply-tasks'] if supply_create_only else ['supply-tasks/' + str(supply_task_id)] if supply_task_id else ['warehouse-receipts', 'warehouse-receipts/{id}/inbounds'] if warehouse_return_id else ['my-receipts', 'my-inbounds'] if personal_only else ['cancel-remaining'] if cancellation_only else ['close'] if closure_only else ['receipts', 'inbound-orders', 'inbound-orders/{id}/post']}
            (directory / 'browser-ready.json').write_text(json.dumps(ready, indent=2, default=str) + '\n')
            print(json.dumps(ready, default=str), flush=True)
            deadline = time.monotonic() + timeout_seconds
            while thread.is_alive() and time.monotonic() < deadline and not (directory / 'STOP-BROWSER').exists():
                time.sleep(.2)
            assert (directory / 'STOP-BROWSER').exists(), 'browser operation timeout; no automatic replay'
        finally:
            server.should_exit = True
            thread.join(timeout=20)
            assert not thread.is_alive(), 'browser server failed to stop'
    assert frontend_manifest(cloud) == sources, 'frontend source or build drift during browser operation'
    writes = [row for row in observed if row['method'] == 'POST']
    if supply_create_only:
        assert writes == [dict(method='POST', path=prefix + '/supply-tasks', status=201)], writes
        position = next(i for i, row in enumerate(observed) if row['method'] == 'POST')
        assert observed[:position].count(dict(method='GET', path=prefix + '/supply-planning-capacity', status=200)) >= 2
        assert dict(method='GET', path=prefix, status=200) in observed[position + 1:]
        assert not [row for row in observed if row['status'] >= 400], observed
        return dict(httpCommands=[dict(row, browser=True) for row in writes],
            capacityReadBeforeCreateAndSubmit=True, detailReadbackAfterEachWrite=True)
    if supply_task_id is not None:
        path = prefix + '/supply-tasks/' + str(supply_task_id)
        assert writes == [dict(method='POST', path=path, status=200)] * 2, writes
        # On success the real page verifies the current detail; the trace
        # endpoint is used for uncertain responses. Check each actual tail.
        posts = [index for index, row in enumerate(observed) if row['method'] == 'POST']
        for start, end in zip(posts, [*posts[1:], len(observed)]):
            assert dict(method='GET', path=prefix, status=200) in observed[start + 1:end], observed
        assert not [row for row in observed if row['status'] >= 400], observed
        return dict(httpCommands=[dict(row, browser=True) for row in writes],
                    detailReadbackAfterEachWrite=True)
    if warehouse_return_id is not None:
        base = '/api/v1/rejection-returns/' + str(warehouse_return_id) + '/warehouse-receipts'
        assert len(writes) == 2 and writes[0] == {'method': 'POST', 'path': base, 'status': 201}, writes
        assert writes[1]['status'] == 201 and re.fullmatch(re.escape(base) + r'/[0-9a-f-]{36}/inbounds', writes[1]['path']), writes
        for row in writes:
            assert {'method': 'GET', 'path': row['path'] + '/command-status', 'status': 200} in observed
        assert not [row for row in observed if row['status'] >= 400], observed
        return {'httpCommands': [dict(row, browser=True) for row in writes], 'originalCommandReadbackObserved': True}
    if personal_only:
        assert writes == [{'method': 'POST', 'path': prefix + '/' + route, 'status': 201}
                          for route in ('my-receipts', 'my-inbounds')], writes
        for route in ('my-receipts', 'my-inbounds'):
            for suffix in ('command-status', 'trace-status'):
                assert any(row == {'method': 'GET', 'path': prefix + '/' + route + '/' + suffix, 'status': 200} for row in observed)
        assert not [row for row in observed if row['status'] >= 400], observed
        return {'httpCommands': [{'route': route, 'status': 201, 'browser': True} for route in ('my-receipts', 'my-inbounds')]}
    if cancellation_only:
        assert len(writes) == 1 and writes[0] == {'method': 'POST', 'path': prefix + '/cancel-remaining', 'status': 201}, writes
        assert any(row == {'method': 'GET', 'path': prefix + '/cancel-remaining-command-status', 'status': 200} for row in observed)
        return {'httpCommands': [{'route': 'cancel-remaining', 'status': 201, 'browser': True}], 'originalCommandReadbackObserved': True}
    if closure_only:
        assert len(writes) == 1 and writes[0] == {'method': 'POST', 'path': prefix + '/close', 'status': 201}, writes
        return {'httpCommands': [{'route': 'close', 'status': 201, 'browser': True}]}
    assert len(writes) == 3 and [row['status'] for row in writes] == [201, 201, 200], writes
    assert [row['path'] for row in writes[:2]] == [prefix + '/receipts', prefix + '/inbound-orders'], writes
    assert writes[2]['path'].startswith(prefix + '/inbound-orders/') and writes[2]['path'].endswith('/post'), writes
    return {'httpCommands': [{'route': row['path'][len(prefix) + 1:], 'status': row['status'], 'browser': True} for row in writes]}


def cancel_remaining_in_browser(mounted, cloud, *, engines, directory, request_id, admin, timeout_seconds):
    """Real requester UI cancellation, then a separate administrator closure.

    The isolated fixture is already approved for two, posted for one and has
    released the other unit. No cancellation is precommitted by this helper.
    """
    from uuid import UUID
    from sqlalchemy import select, text
    from app.demand_models import MaterialRequest
    from app.material_request_remaining_cancel_schema import cancellations
    from app.formal_services import material_request_remaining_cancel as cancellation
    from app.formal_services.material_request_closure import close_material_request, closure_command_status
    from pg16_stock_scrap_structure_gate import original_columns, facts
    request_id = UUID(request_id)
    api, owner = engines['star_oam_api'], engines['star_oam_migrator']
    with owner.connect() as db:
        columns = {k: v for k, v in original_columns(db).items() if not k.startswith('audit_')
            and not k.startswith('material_request_remaining_cancel') and k != 'material_request_closures'}
        before = facts(db, columns)
    with Session(api) as db:
        db.execute(text('SET TRANSACTION READ ONLY'))
        request = db.get(MaterialRequest, request_id)
        requester, version = request.requester_user_id, request.version
        actor = load_formal_principal(db, requester)
        state = cancellation.read_remaining_cancellation(db, actor=actor, request_id=request_id)
        remainder = cancellation.remaining_fulfillment(db, actor=actor, request_id=request_id)
        assert state.cancel_permitted and state.cancellation is None
        assert len(remainder.lines) == 1
        assert (remainder.lines[0].posted_qty, remainder.lines[0].unreserved_qty) == ('1.000', '1.000')
    result = serve(mounted, cloud, api=api, admin=requester, directory=directory,
        request_id=request_id, cancellation_only=True, timeout_seconds=timeout_seconds)
    with Session(api) as db:
        db.execute(text('SET TRANSACTION READ ONLY'))
        actor = load_formal_principal(db, requester)
        state = cancellation.read_remaining_cancellation(db, actor=actor, request_id=request_id)
        assert state.cancellation is not None and not state.cancel_permitted
        assert len(db.scalars(select(cancellations.c.id).where(cancellations.c.request_id == request_id)).all()) == 1
        complete = cancellation.completion_quantities(db, actor=actor, request_id=request_id)
        assert complete.quantity_coverage_complete
        assert [(x.approved_qty, x.cancelled_qty, x.posted_qty, x.remaining_qty) for x in complete.lines] == [('2.000', '1.000', '1.000', '0.000')]
        result['readback'] = state.model_dump(mode='json')
    # The browser keeps the original requester identity; only this separate
    # transaction uses the existing authorized headquarters fixture.
    with Session(api) as db, db.begin():
        closed = close_material_request(db, actor=load_formal_principal(db, admin), request_id=request_id,
            payload=dict(expected_request_version=version, reason='浏览器取消剩余数量后独立核对关闭'),
            idempotency_key='browser-cancel-close-0001', secret=SECRET, trace_request_id='browser-cancel-close-trace-0001')
    with Session(api) as db:
        db.execute(text('SET TRANSACTION READ ONLY'))
        recovered = closure_command_status(db, actor=load_formal_principal(db, admin), request_id=request_id,
            idempotency_key='browser-cancel-close-0001', secret=SECRET)
        assert recovered.closure_id == closed.closure_id
    with owner.connect() as db:
        assert facts(db, columns) == before
    result.update(closure=closed.model_dump(mode='json'), originalBusinessUnchanged=True,
        readOnlyRecovery=True, syntheticIdentity=True, productionAcceptance=False)
    (directory / 'browser-cancellation-checks.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


def supply_in_browser(mounted, cloud, *, engines, admin, directory, request_id, supply_task_id=None, timeout_seconds, create_only=False):
    """UI is the first writer of both late plan commands; then verify PG facts."""
    from dataclasses import asdict
    from sqlalchemy import select, text
    from app.demand_models import MaterialRequest, MaterialRequestCommand, MaterialRequestLine, SupplyTask
    from app.foundation_models import AuditEvent
    from app.formal_services.material_request_supply_command_status import material_request_supply_command_status
    from pg16_stock_scrap_structure_gate import original_columns, facts
    api, owner = engines['star_oam_api'], engines['star_oam_migrator']
    with owner.connect() as db:
        columns = {k: v for k, v in original_columns(db).items()
            if k.startswith(('stock_', 'inventory_', 'serial_', 'allocation_', 'custody_'))}
        before = facts(db, columns)
    with Session(api) as db:
        db.execute(text('SET TRANSACTION READ ONLY'))
        request = db.get(MaterialRequest, request_id)
        version = request.version
        if create_only:
            assert request.allocation_status == 'partially_allocated'
            assert not tuple(db.scalars(select(SupplyTask.id).join(MaterialRequestLine).where(MaterialRequestLine.request_id == request_id)))
        else:
            task = db.get(SupplyTask, supply_task_id)
            task_version, amount = task.version, task.expected_qty
            assert request.allocation_status == 'allocated' and task.status == 'open'

    result = serve(mounted, cloud, api=api, admin=admin, directory=directory,
        request_id=request_id, supply_task_id=supply_task_id, timeout_seconds=timeout_seconds, supply_create_only=create_only)
    with Session(api) as db:
        db.execute(text('SET TRANSACTION READ ONLY'))
        request = db.get(MaterialRequest, request_id)
        if create_only:
            task = db.scalars(select(SupplyTask).join(MaterialRequestLine).where(MaterialRequestLine.request_id == request_id)).one()
            supply_task_id, amount = task.id, task.expected_qty
            assert request.version == version + 1 and request.allocation_status == 'partially_allocated'
            assert task.status == 'open' and amount == 1 and task.original_equivalent_qty == 1
        else:
            task = db.get(SupplyTask, supply_task_id)
            assert request.version == version + 2 and request.allocation_status == 'allocated'
            assert task.status == 'cancelled' and task.version == task_version + 2
            assert task.expected_qty == amount and task.original_equivalent_qty == amount
        commands = tuple(db.scalars(select(MaterialRequestCommand).where(
            MaterialRequestCommand.request_id == request_id,
            MaterialRequestCommand.target_version > version).order_by(MaterialRequestCommand.target_version)))
        assert [row.operation for row in commands] == (['create_supply_task'] if create_only else ['update_supply_task', 'cancel_supply_task'])
        recovered = []
        for command in commands:
            audits = [row for row in db.scalars(select(AuditEvent).where(
                AuditEvent.aggregate_type == 'material_request',
                AuditEvent.aggregate_id == str(request_id)))
                if row.after_jsonb.get('command_id') == str(command.id)]
            assert len(audits) == 1
            found = material_request_supply_command_status(db, actor=load_formal_principal(db, admin),
                trace_request_id=audits[0].request_id)
            assert found.lookup_status == 'confirmed'
            assert found.command.supply_task_id == supply_task_id
            assert found.command.request_version == command.target_version
            recovered.append(asdict(found.command))
        result.update(supplyTaskId=str(task.id), taskVersion=task.version, planStatus=task.status, requestVersion=request.version,
            expectedQuantity=str(amount), recoveredCommands=recovered)
    with owner.connect() as db:
        assert facts(db, columns) == before
    result.update(inventoryUnchanged=True, readOnlyRecovery=True, syntheticIdentity=True,
        productionAcceptance=False)
    (directory / 'browser-supply-checks.json').write_text(json.dumps(result, indent=2, default=str) + '\n')
    # Return only serializable evidence to the enclosing fulfillment receipt.
    return json.loads((directory / 'browser-supply-checks.json').read_text())
