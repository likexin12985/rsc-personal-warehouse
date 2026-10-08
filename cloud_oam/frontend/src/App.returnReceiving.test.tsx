// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, beforeAll, beforeEach, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import App from './App';
import { api, apiNoReplay } from './api';
import type { AccessContext, AuthenticatedUser } from './types';
import type { ComponentProps } from 'react';
import type FormalReturnReceivingPage from './FormalReturnReceivingPage';
import type FormalRejectionWarehousePage from './FormalRejectionWarehousePage';
type Props = ComponentProps<typeof FormalReturnReceivingPage>;
type WarehouseProps = ComponentProps<typeof FormalRejectionWarehousePage>;

const observed = vi.hoisted(() => ({ page: vi.fn(), warehouse: vi.fn() }));
vi.mock('./api', async loadOriginal => ({
  ...await loadOriginal<typeof import('./api')>(), api: vi.fn(), apiNoReplay: vi.fn(),
}));
vi.mock('./FormalReturnReceivingPage', () => ({ default: (props: Props) => {
  observed.page(props);
  return <section aria-label="接收路由测试">{props.identity.person_id}</section>;
} }));
vi.mock('./FormalRejectionWarehousePage', () => ({ default: (props: WarehouseProps) => {
  observed.warehouse(props); return <section aria-label="拒收仓库路由测试">{props.identity.person_id}</section>;
} }));

// Load the real route wrapper before permission assertions; keep page mocks and
// no-replay adapter checks intact without timing cold transforms as UI updates.
beforeAll(async () => { await import('./FormalOperationRoutes'); }, 30_000);

const person = '10000000-0000-4000-8000-000000000001';
beforeEach(() => { vi.mocked(api).mockReset(); vi.mocked(apiNoReplay).mockReset(); observed.page.mockClear(); observed.warehouse.mockClear(); });
afterEach(cleanup);

it.each([
  { roles: ['admin'], read: true, allowed: true },
  { roles: ['provincial_manager'], read: true, allowed: true },
  { roles: ['admin', 'provincial_manager'], read: true, allowed: true },
  { roles: ['admin'], read: false, allowed: false },
  { roles: ['provincial_manager'], read: false, allowed: false },
  { roles: ['technician'], read: true, allowed: false },
])('gates direct URL and navigation for $roles with read=$read', async ({ roles, read, allowed }) => {
  const identity: AuthenticatedUser = {
    person_id: person, name: '审批测试人员', mobile: '13800000000', role_codes: roles,
    organization_name: '测试组织', account_status: 'active', employment_status: 'active',
    access_mode: 'active', authorization_version: 7,
  };
  const access: AccessContext = {
    person_id: person, role_codes: roles, account_status: 'active', employment_status: 'active',
    authorization_version: 7, access_mode: 'active', assignments: [],
    // Read-only reviewers can still reach original-request recovery after write revocation.
    permissions: read ? [{ resource: 'stock_operation', action: 'read', field_code: '' }] : [],
  };
  vi.mocked(api).mockImplementation(async path => {
    if (path === '/auth/me') return identity;
    if (path === '/access/context') return access;
    throw new Error(`Unexpected route request: ${path}`);
  });
  render(<MemoryRouter initialEntries={['/return-receiving']}><App /></MemoryRouter>);
  await screen.findByRole('button', { name: '退出登录' });
  if (allowed) {
    expect((await screen.findByRole('region', { name: '接收路由测试' })).textContent).toBe(person);
    expect(screen.getAllByRole('link', { name: '退回收货与入库' }).every(link => link.getAttribute('href') === '/return-receiving')).toBe(true);
    const props = observed.page.mock.lastCall![0] as Props;
    expect(props.identity).toEqual({ person_id: person, authorization_version: 7 });
    // The real wrapper must bind the no-replay API to its adapter.
    vi.mocked(apiNoReplay).mockRejectedValueOnce(new Error('read probe'));
    await expect(props.adapter.context()).rejects.toThrow('read probe');
    expect(apiNoReplay).toHaveBeenCalledWith('/auth/me', expect.objectContaining({ cache: 'no-store' }));
  } else {
    await screen.findByText('审批测试人员，欢迎进入 RSC 个人仓');
    expect(screen.queryByRole('link', { name: '退回收货与入库' })).toBeNull();
    expect(observed.page).not.toHaveBeenCalled();
    expect(apiNoReplay).not.toHaveBeenCalled();
  }
});
it.each([
  { role: 'admin', inventory: true, allowed: true },
  { role: 'provincial_manager', inventory: true, allowed: true },
  { role: 'admin', inventory: false, allowed: false },
  { role: 'technician', inventory: true, allowed: false },
])('gates refusal warehouse by current $role and inventory=$inventory', async ({ role, inventory, allowed }) => {
  const shared = { person_id: person, role_codes: [role], account_status: 'active', employment_status: 'active', access_mode: 'active', authorization_version: 7 };
  const identity = { ...shared, name: '审批测试人员', mobile: '13800000000', organization_name: '测试组织' };
  const access = { ...shared, assignments: [], permissions: [{ resource: 'stock_operation', action: 'read', field_code: '' },
    ...(inventory ? [{ resource: 'inventory', action: 'read', field_code: '' }] : [])] };
  vi.mocked(api).mockImplementation(async path => {
    if (path === '/auth/me') return identity; if (path === '/access/context') return access; throw new Error(path);
  });
  render(<MemoryRouter initialEntries={['/rejection-return-receiving']}><App /></MemoryRouter>);
  await screen.findByRole('button', { name: '退出登录' });
  if (allowed) {
    expect((await screen.findByRole('region', { name: '拒收仓库路由测试' })).textContent).toBe(person);
    const props = observed.warehouse.mock.lastCall![0] as WarehouseProps;
    vi.mocked(apiNoReplay).mockRejectedValueOnce(new Error('read probe'));
    await expect(props.adapter.context()).rejects.toThrow('read probe');
    expect(apiNoReplay).toHaveBeenCalledWith('/auth/me', expect.objectContaining({ cache: 'no-store' }));
  } else {
    await screen.findByText('审批测试人员，欢迎进入 RSC 个人仓'); expect(observed.warehouse).not.toHaveBeenCalled();
  }
});
