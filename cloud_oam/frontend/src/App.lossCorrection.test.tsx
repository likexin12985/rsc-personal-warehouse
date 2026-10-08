// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, beforeAll, beforeEach, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import App from './App';
import { api, apiNoReplay } from './api';
import type { AccessContext, AuthenticatedUser } from './types';
import type { ComponentProps } from 'react';
import type FormalLossCorrection from './FormalLossCorrection';
type Props = ComponentProps<typeof FormalLossCorrection>;

const observed = vi.hoisted(() => ({ page: vi.fn() }));
vi.mock('./api', async loadOriginal => ({
  ...await loadOriginal<typeof import('./api')>(), api: vi.fn(), apiNoReplay: vi.fn(),
}));
vi.mock('./FormalLossCorrection', () => ({ default: (props: Props) => {
  observed.page(props);
  return <section aria-label="纠正路由测试">纠正</section>;
} }));

// Load the real route wrapper before permission assertions; keep page mocks and
// no-replay adapter checks intact without timing cold transforms as UI updates.
beforeAll(async () => { await import('./FormalOperationRoutes'); }, 30_000);

const person = '10000000-0000-4000-8000-000000000001';
beforeEach(() => { vi.mocked(api).mockReset(); vi.mocked(apiNoReplay).mockReset(); observed.page.mockClear(); });
afterEach(cleanup);

it.each([
  { roles: ['admin'], read: true, stages: ['headquarters'] },
  { roles: ['provincial_manager'], read: true, stages: [] },
  { roles: ['admin', 'provincial_manager'], read: true, stages: ['headquarters'] },
  { roles: ['admin'], read: false, stages: [] },
  { roles: ['provincial_manager'], read: false, stages: [] },
  { roles: ['technician'], read: true, stages: [] },
])('gates direct URL and navigation for $roles with read=$read', async ({ roles, read, stages }) => {
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
  render(<MemoryRouter initialEntries={['/loss-corrections/20000000-0000-4000-8000-000000000002']}><App /></MemoryRouter>);
  await screen.findByRole('button', { name: '退出登录' });
  if (stages.length) {
    expect((await screen.findByRole('region', { name: '纠正路由测试' })).textContent).toBe('纠正');
    expect(screen.getAllByRole('link', { name: '报损处置' }).every(link => link.getAttribute('href') === '/loss-execution')).toBe(true);
    const props = observed.page.mock.lastCall![0] as Props;
    expect(props.identity).toEqual({ person_id: person, authorization_version: 7 });
    expect(props.rootId).toBe('20000000-0000-4000-8000-000000000002');
    // The real wrapper must bind the no-replay API to its adapter.
    vi.mocked(apiNoReplay).mockRejectedValueOnce(new Error('read probe'));
    await expect(props.adapter.context()).rejects.toThrow('read probe');
    expect(apiNoReplay).toHaveBeenCalledWith('/auth/me', expect.objectContaining({ cache: 'no-store' }));
  } else {
    await screen.findByText('审批测试人员，欢迎进入 RSC 个人仓');
    expect(screen.queryByRole('link', { name: '报损处置' })).toBeNull();
    expect(observed.page).not.toHaveBeenCalled();
    expect(apiNoReplay).not.toHaveBeenCalled();
  }
});
