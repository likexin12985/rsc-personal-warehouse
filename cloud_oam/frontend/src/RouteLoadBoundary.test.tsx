// @vitest-environment jsdom
import {cleanup,render,screen} from '@testing-library/react';
import {afterEach,expect,it,vi} from 'vitest';
import RouteLoadBoundary from './RouteLoadBoundary';
afterEach(()=>{cleanup();vi.restoreAllMocks();});
it('keeps a failed route closed and tells users to recover unresolved original requests',()=>{
  vi.spyOn(console,'error').mockImplementation(()=>{});
  function FailedChunk():never{throw new Error('synthetic chunk unavailable');}
  localStorage.setItem('synthetic-saved-original','preserve');
  render(<RouteLoadBoundary><FailedChunk/></RouteLoadBoundary>);
  expect(screen.getByRole('alert').textContent).toContain('页面未能加载');expect(screen.getByRole('button',{name:'重新加载页面'})).toBeTruthy();
  expect(localStorage.getItem('synthetic-saved-original')).toBe('preserve');localStorage.removeItem('synthetic-saved-original');
});
