// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import App from './App';
import { api } from './api';
import type { AccessContext, AuthenticatedUser } from './types';
import example from './test-fixtures/overview-approved.json';

vi.mock('./api', async original => ({ ...await original<typeof import('./api')>(), api: vi.fn() }));
beforeEach(() => { vi.mocked(api).mockReset(); });
afterEach(cleanup);

it.each([
  { role: 'admin', read: true, visible: true },
  { role: 'provincial_manager', read: true, visible: true },
  { role: 'admin', read: false, visible: false },
  { role: 'provincial_manager', read: false, visible: false },
  { role: 'technician', read: true, visible: false },
])('gates overview navigation and direct URL for $role / read=$read', async ({ role, read, visible }) => {
  const personId = '10000000-0000-4000-8000-000000000001';
  const identity: AuthenticatedUser = {
    person_id: personId, name: '统计测试人员', mobile: '13800000000',
    role_codes: [role], organization_name: '测试区域', account_status: 'active', employment_status: 'active',
    access_mode: 'active', authorization_version: 7,
  };
  const access: AccessContext = {
    person_id: personId, role_codes: [role], account_status: 'active', employment_status: 'active',
    authorization_version: 7, access_mode: 'active', assignments: [],
    permissions: read ? [{ resource: 'material_request', action: 'read', field_code: '' }] : [],
  };
  vi.mocked(api).mockImplementation(async path => {
    if (path === '/auth/me') return identity;
    if (path === '/access/context') return access;
    if (path === '/v1/reports/material-requests') return example;
    throw new Error(`Unexpected request: ${path}`);
  });
  render(<MemoryRouter initialEntries={['/reports/material-requests']}><App /></MemoryRouter>);
  await screen.findByRole('button', { name: '退出登录' });
  if (visible) {
    await screen.findByText('1 单申请');
    expect(screen.getAllByRole('link', { name: '需求履约概览' }).every(link => link.getAttribute('href') === '/reports/material-requests')).toBe(true);
  } else {
    await screen.findByText('统计测试人员，欢迎进入 RSC 个人仓');
    expect(screen.queryByRole('link', { name: '需求履约概览' })).toBeNull();
    expect(vi.mocked(api).mock.calls.some(([path]) => path.startsWith('/v1/reports/material-requests'))).toBe(false);
  }
});
