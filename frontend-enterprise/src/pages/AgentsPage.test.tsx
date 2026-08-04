// @vitest-environment jsdom

import { act, cleanup, render, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { api } from '../api/client';
import { I18nProvider } from '../i18n';
import type { AgentProfileRead } from '../types';
import AgentsPage from './AgentsPage';

vi.mock('../api/client', () => ({
  TENANT_ID: 'tenant_demo',
  api: {
    delete: vi.fn(),
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
  },
}));

const createdAgent: AgentProfileRead = {
  id: 'agent-created',
  tenant_id: 'tenant_demo',
  name: 'Created Employee',
  description: 'Created during the test',
  is_overall: false,
  status: 'active',
  metadata: {
    owner_user_id: 'user-admin',
    role_name: 'Operations',
  },
  resources: [],
  created_at: '2026-08-04T00:00:00Z',
  updated_at: '2026-08-04T00:00:00Z',
};

describe('AgentsPage refresh contract', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    window.localStorage.clear();
  });

  afterEach(() => {
    cleanup();
  });

  it('reloads the employee roster after the shared refresh event', async () => {
    vi.mocked(api.get)
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([createdAgent]);

    render(
      <I18nProvider>
        <MemoryRouter>
          <AgentsPage
            currentUser={{
              id: 'user-admin',
              tenant_id: 'tenant_demo',
              username: 'admin',
              role: 'admin',
            }}
            isAdmin
          />
        </MemoryRouter>
      </I18nProvider>,
    );

    await waitFor(() => expect(api.get).toHaveBeenCalledTimes(1));

    act(() => {
      window.dispatchEvent(new Event('ultrarag-enterprise-agent-scope-refresh'));
    });

    await waitFor(() => expect(api.get).toHaveBeenCalledTimes(2));
  });
});
