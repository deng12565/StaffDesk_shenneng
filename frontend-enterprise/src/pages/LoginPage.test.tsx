// @vitest-environment jsdom

import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { api } from '../api/client';
import { ENTERPRISE_AUTH_STORAGE_KEY, type EnterpriseAuthSession } from '../auth';
import { I18nProvider } from '../i18n';
import LoginPage from './LoginPage';

vi.mock('../api/client', () => ({
  TENANT_ID: 'tenant_demo',
  api: {
    post: vi.fn(),
  },
}));

const session: EnterpriseAuthSession = {
  token: 'token-demo',
  user: {
    id: 'user-admin',
    tenant_id: 'tenant_demo',
    username: 'admin',
    role: 'admin',
  },
};

function renderLogin(onLogin = vi.fn()) {
  render(
    <I18nProvider>
      <LoginPage onLogin={onLogin} />
    </I18nProvider>,
  );
  return { onLogin };
}

describe('LoginPage', () => {
  beforeEach(() => {
    vi.mocked(api.post).mockReset();
    window.localStorage.clear();
  });

  afterEach(() => {
    cleanup();
  });

  it('renders the Xiaoshen brand and visible credential form', () => {
    renderLogin();

    expect(screen.getByRole('heading', { name: '小申数字员工' })).toBeTruthy();
    expect(screen.getByRole('textbox', { name: '账号' })).toBeTruthy();
    expect(screen.getByLabelText('密码')).toBeTruthy();
    expect(screen.getByText('申能集团内部工作入口')).toBeTruthy();
    expect(screen.getAllByText('运行中')).toHaveLength(6);
    expect(screen.queryByText('StaffDeck')).toBeNull();
  });

  it('shows accessible field errors without calling the API', async () => {
    const user = userEvent.setup();
    renderLogin();

    await user.click(screen.getByRole('button', { name: '登录' }));

    expect(screen.getByRole('textbox', { name: '账号' }).getAttribute('aria-invalid')).toBe('true');
    expect(screen.getByLabelText('密码').getAttribute('aria-invalid')).toBe('true');
    expect(screen.getAllByText('请输入账号').length).toBeGreaterThan(0);
    expect(screen.getAllByText('请输入密码').length).toBeGreaterThan(0);
    expect(api.post).not.toHaveBeenCalled();
  });

  it('toggles password visibility', async () => {
    const user = userEvent.setup();
    renderLogin();
    const passwordInput = screen.getByLabelText('密码') as HTMLInputElement;

    await user.type(passwordInput, 'secret');
    expect(passwordInput.type).toBe('password');

    await user.click(screen.getByRole('button', { name: '显示密码' }));
    expect(passwordInput.type).toBe('text');
    expect(screen.getByRole('button', { name: '隐藏密码' })).toBeTruthy();
  });

  it('submits once on Enter and stores the returned session', async () => {
    const user = userEvent.setup();
    const onLogin = vi.fn();
    vi.mocked(api.post).mockResolvedValue(session);
    renderLogin(onLogin);

    await user.type(screen.getByRole('textbox', { name: '账号' }), ' admin ');
    await user.type(screen.getByLabelText('密码'), ' admin ');
    await user.keyboard('{Enter}');

    await waitFor(() => expect(api.post).toHaveBeenCalledTimes(1));
    expect(api.post).toHaveBeenCalledWith('/api/auth/login', {
      tenant_id: 'tenant_demo',
      username: 'admin',
      password: 'admin',
    });
    expect(onLogin).toHaveBeenCalledWith(session);
    expect(JSON.parse(window.localStorage.getItem(ENTERPRISE_AUTH_STORAGE_KEY) || '{}')).toEqual(session);
  });

  it('shows a form-level error and preserves credentials after rejection', async () => {
    const user = userEvent.setup();
    vi.mocked(api.post).mockRejectedValue(new Error('Unauthorized'));
    renderLogin();

    const usernameInput = screen.getByRole('textbox', { name: '账号' }) as HTMLInputElement;
    const passwordInput = screen.getByLabelText('密码') as HTMLInputElement;
    await user.type(usernameInput, 'admin');
    await user.type(passwordInput, 'wrong-password');
    await user.click(screen.getByRole('button', { name: '登录' }));

    expect(await screen.findByText('账号或密码不正确，请重试')).toBeTruthy();
    expect(usernameInput.value).toBe('admin');
    expect(passwordInput.value).toBe('wrong-password');
    expect(screen.getByRole('button', { name: '登录' }).hasAttribute('disabled')).toBe(false);
  });
});
