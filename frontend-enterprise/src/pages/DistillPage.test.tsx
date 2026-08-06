// @vitest-environment jsdom

import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';

import { I18nProvider } from '../i18n';
import type { SkillActionCatalogRead, ToolRead } from '../types';
import DistillPage, { ActionCombobox, buildActionOptions } from './DistillPage';

const apiGetMock = vi.hoisted(() => vi.fn());
const streamPostMock = vi.hoisted(() => vi.fn());

vi.mock('../api/client', () => ({
  api: {
    get: apiGetMock,
    post: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
    postWithSignal: vi.fn(),
  },
  ApiError: class ApiError extends Error {
    status = 500;
  },
  streamGet: vi.fn(),
  streamPost: streamPostMock,
  TENANT_ID: 'tenant_demo',
}));

vi.mock('@/components/AppHeader', () => ({
  default: () => null,
}));

vi.mock('@/components/ModelConfigDropdown', () => ({
  ModelConfigDropdown: () => null,
}));

function mcpLeaf(index: number): ToolRead {
  return {
    id: `tool_${index}`,
    tenant_id: 'tenant_demo',
    name: `finance.tool_${String(index).padStart(2, '0')}`,
    display_name: `工具 ${index}`,
    description: `金融工具 ${index}`,
    bucket: '金融',
    tool_type: 'mcp',
    method: 'POST',
    url: `mcp://finance/tool_${index}`,
    headers: {},
    auth: {},
    mcp_config: {},
    input_schema: { type: 'object' },
    output_schema: {},
    allowed_skills: [],
    mcp_server_id: 'server_finance',
    enabled: true,
    created_at: '2026-08-05T00:00:00Z',
    updated_at: '2026-08-05T00:00:00Z',
  };
}

const catalog: SkillActionCatalogRead = {
  controls: [
    {
      value: 'ask_user',
      label: '询问用户',
      description: '收集缺少的信息',
      kind: 'control',
      tool_count: 0,
    },
  ],
  mcp_toolsets: [
    {
      value: 'call_mcp:server_finance',
      label: '使用 MCP：金融数据',
      description: '股票和公告查询',
      kind: 'mcp_toolset',
      mcp_server_id: 'server_finance',
      tool_count: 29,
    },
  ],
  http_tools: [
    {
      value: 'call_tool:internal.lookup',
      label: '调用工具：内部查询',
      description: '查询内部数据',
      kind: 'http_tool',
      tool_id: 'tool_http',
      tool_name: 'internal.lookup',
      tool_count: 0,
    },
  ],
};

describe('SOP action picker', () => {
  it('collapses 29 MCP leaves into one grouped option and preserves legacy actions', async () => {
    const tools = Array.from({ length: 29 }, (_, index) => mcpLeaf(index));
    const options = buildActionOptions(
      catalog,
      tools,
      { 'call_mcp:server_finance': '使用 MCP：金融数据：股票和公告查询' },
      {},
      [{ allowed_actions: ['clarify_user', 'call_mcp:missing_server'] }],
    );

    expect(options.filter((option) => option.value === 'call_mcp:server_finance')).toHaveLength(1);
    expect(options.some((option) => option.value.startsWith('call_tool:finance.'))).toBe(false);
    expect(options.find((option) => option.value === 'clarify_user')).toMatchObject({
      group: '旧版动作',
      legacy: true,
    });
    expect(options.find((option) => option.value === 'call_mcp:missing_server')).toMatchObject({
      group: '旧版动作',
      legacy: true,
    });

    const onSelect = vi.fn();
    render(<ActionCombobox options={options} onSelect={onSelect} />);

    expect(await screen.findByText('流程控制')).toBeTruthy();
    expect(screen.getByText('MCP 工具集')).toBeTruthy();
    expect(screen.getByText('独立工具')).toBeTruthy();
    expect(screen.getByText('旧版动作')).toBeTruthy();
    expect(screen.getAllByText('使用 MCP：金融数据')).toHaveLength(1);

    const user = userEvent.setup();
    await user.type(screen.getByPlaceholderText('选择一个动作'), '金融数据');
    await waitFor(() => {
      expect(screen.queryByText('调用工具：内部查询')).toBeNull();
      expect(screen.getByText('使用 MCP：金融数据')).toBeTruthy();
    });
    await user.click(screen.getByText('使用 MCP：金融数据'));
    expect(onSelect).toHaveBeenCalledWith('call_mcp:server_finance');
  });
});

describe('SOP draft streaming', () => {
  it('keeps the current preview during chunk reset and replaces it on completion', async () => {
    window.localStorage.clear();
    apiGetMock.mockImplementation((url: string) => {
      if (url.includes('/action-catalog')) {
        return Promise.resolve({ controls: [], mcp_toolsets: [], http_tools: [] });
      }
      return Promise.resolve([]);
    });

    let continueToReset: (() => void) | undefined;
    let continueToComplete: (() => void) | undefined;
    const waitBeforeReset = new Promise<void>((resolve) => {
      continueToReset = resolve;
    });
    const waitBeforeComplete = new Promise<void>((resolve) => {
      continueToComplete = resolve;
    });
    const initialDraft = streamingSkill('Initial Wi-Fi');
    const finalDraft = streamingSkill('Final Wi-Fi');

    streamPostMock.mockImplementation(
      async (
        _url: string,
        _payload: Record<string, unknown>,
        onEvent: (item: { event: string; data: Record<string, unknown> }) => void,
      ) => {
        onEvent({
          event: 'chunk',
          data: { content: JSON.stringify({ draft_skill: initialDraft, warnings: [] }) },
        });
        await waitBeforeReset;
        onEvent({ event: 'chunk_reset', data: {} });
        await waitBeforeComplete;
        onEvent({
          event: 'complete',
          data: { draft_skill: finalDraft, warnings: [], tool_suggestions: [] },
        });
      },
    );

    render(
      <I18nProvider>
        <MemoryRouter>
          <DistillPage searchParamsOverride={new URLSearchParams('mode=create&workspace_id=stream-reset-test')} />
        </MemoryRouter>
      </I18nProvider>,
    );

    const user = userEvent.setup();
    await user.type(
      await screen.findByPlaceholderText('输入或粘贴需要整理的 SOP 流程说明'),
      'Create a visitor Wi-Fi registration flow',
    );
    await user.click(screen.getByRole('button', { name: '发送' }));
    expect(await screen.findByDisplayValue('Initial Wi-Fi')).toBeTruthy();

    await act(async () => {
      continueToReset?.();
      await Promise.resolve();
    });
    expect(screen.getByDisplayValue('Initial Wi-Fi')).toBeTruthy();

    await act(async () => {
      continueToComplete?.();
      await Promise.resolve();
    });
    expect(await screen.findByDisplayValue('Final Wi-Fi')).toBeTruthy();
  });
});

function streamingSkill(name: string) {
  return {
    skill_id: 'visitor_wifi',
    name,
    version: '1.0.0',
    business_domain: 'it',
    description: 'Visitor Wi-Fi registration',
    trigger_intents: ['visitor_wifi_request'],
    user_utterance_examples: ['Register visitor Wi-Fi'],
    goal: ['Register visitor details'],
    required_info: [],
    slot_filling_policy: {},
    response_rules: ['Return a clear result'],
    nodes: [
      {
        node_id: 'reply_result',
        type: 'response',
        name: 'Return result',
        instruction: 'Return the registration result.',
        optional: false,
        condition: null,
        expected_user_info: [],
        allowed_actions: ['answer_user'],
        knowledge_scope: {},
        retry_policy: {},
        metadata: {},
      },
    ],
    edges: [],
    start_node_id: 'reply_result',
    terminal_node_ids: ['reply_result'],
    interruption_policy: {},
  };
}
