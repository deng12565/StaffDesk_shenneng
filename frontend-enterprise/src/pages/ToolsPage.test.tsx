// @vitest-environment jsdom

import { cleanup, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

import { api } from '../api/client';
import type { EnterpriseAuthUser } from '../auth';
import { I18nProvider } from '../i18n';
import type { AgentProfileRead, MCPServerRead, MCPToolInventoryRead, ToolRead } from '../types';
import ToolsPage, { McpServerEditPage } from './ToolsPage';

vi.mock('../api/client', () => ({
  TENANT_ID: 'tenant_demo',
  api: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
  },
}));

vi.mock('@/components/AppHeader', () => ({
  default: ({ title }: { title?: string }) => <header>{title}</header>,
}));

vi.mock('@/components/ResourceImportDialog', () => ({
  ResourceImportDialog: () => null,
}));

vi.mock('@/components/ui/app-toast', () => ({
  notify: {
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
  },
}));

const admin: EnterpriseAuthUser = {
  id: 'user_admin',
  tenant_id: 'tenant_demo',
  username: 'admin',
  role: 'admin',
};

const server: MCPServerRead = {
  id: 'server_finance',
  tenant_id: 'tenant_demo',
  name: 'dataapi_finance',
  display_name: 'Finance Market Data',
  description: 'Stock quotes, financial indicators, and announcements',
  bucket: 'Finance',
  connection: {
    transport: 'streamable_http',
    url: 'https://example.test/mcp',
    headers: {},
    args: [],
    env: {},
  },
  enabled: true,
  available_tool_count: 29,
  tool_count: 29,
  created_at: '2026-08-05T00:00:00Z',
  updated_at: '2026-08-05T00:00:00Z',
};

const cachedInventory: MCPToolInventoryRead = {
  cache_available: true,
  available_count: 2,
  imported_count: 1,
  current_scope_count: 1,
  current_scope_is_overall: false,
  tools: [
    {
      name: 'get_stock_basic_info',
      description: 'Look up stock names, listing dates, and industries by stock code.',
      input_schema: { properties: { stock_code: { type: 'string' } } },
      output_schema: {},
      imported: true,
      tool_id: 'tool_stock',
      enabled: true,
      in_current_scope: true,
    },
    {
      name: 'list_announcements',
      description: 'Find company announcements by stock code and date range.',
      input_schema: { properties: { stock_code: { type: 'string' }, start_date: { type: 'string' } } },
      output_schema: {},
      imported: false,
      in_current_scope: false,
    },
  ],
};

const employeeAgent: AgentProfileRead = {
  id: 'agent_employee',
  tenant_id: 'tenant_demo',
  name: 'Finance Employee',
  is_overall: false,
  status: 'active',
  metadata: {},
  resources: [],
  created_at: '2026-08-05T00:00:00Z',
  updated_at: '2026-08-05T00:00:00Z',
};

const overallAgent: AgentProfileRead = {
  ...employeeAgent,
  id: 'agent_overall',
  name: 'Overall Agent',
  is_overall: true,
};

function makeTool(overrides: Partial<ToolRead> & Pick<ToolRead, 'id' | 'name'>): ToolRead {
  return {
    tenant_id: 'tenant_demo',
    display_name: overrides.name,
    description: '',
    bucket: 'Finance',
    tool_type: 'http',
    method: 'POST',
    url: '/api/mock',
    headers: {},
    auth: {},
    mcp_config: {},
    input_schema: {},
    output_schema: {},
    allowed_skills: [],
    enabled: true,
    metadata: {},
    created_at: '2026-08-05T00:00:00Z',
    updated_at: '2026-08-05T00:00:00Z',
    ...overrides,
  };
}

function makeMcpTool(index: number, serverId = server.id): ToolRead {
  return makeTool({
    id: `tool_mcp_${index}`,
    name: `${server.name}.finance_tool_${index}`,
    display_name: `finance_tool_${index}`,
    description: index === 11 ? 'Special company announcement lookup' : `Finance tool description ${index}`,
    tool_type: 'mcp',
    url: `mcp://${server.name}/finance_tool_${index}`,
    mcp_server_id: serverId,
  });
}

function mockToolsPageGets(
  toolProvider: () => ToolRead[],
  serverRows: MCPServerRead[] = [server],
  agent: AgentProfileRead = employeeAgent,
) {
  vi.mocked(api.get).mockImplementation(async (path: string) => {
    if (path.startsWith('/api/enterprise/agents?')) return [agent];
    if (path.startsWith('/api/enterprise/tools?')) return toolProvider();
    if (path.startsWith('/api/enterprise/mcp-servers?')) return serverRows;
    throw new Error(`Unexpected GET ${path}`);
  });
}

function renderToolsPage() {
  return render(
    <I18nProvider>
      <MemoryRouter><ToolsPage currentUser={admin} /></MemoryRouter>
    </I18nProvider>,
  );
}

function renderEditor() {
  return render(
    <I18nProvider>
      <MemoryRouter initialEntries={['/enterprise/tools/mcp/server_finance/edit']}>
        <Routes>
          <Route
            path="/enterprise/tools/mcp/:serverId/edit"
            element={<McpServerEditPage currentUser={admin} />}
          />
        </Routes>
      </MemoryRouter>
    </I18nProvider>,
  );
}

function mockEditorGets(inventoryProvider: () => MCPToolInventoryRead) {
  vi.mocked(api.get).mockImplementation(async (path: string) => {
    if (path.includes('/tool-inventory?')) return inventoryProvider();
    if (path.includes('/api/enterprise/mcp-servers/server_finance?')) return server;
    throw new Error(`Unexpected GET ${path}`);
  });
}

describe('MCP tool counts and inventory', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.localStorage.clear();
    window.localStorage.setItem('ultrarag_enterprise_agent_scope', 'agent_employee');
  });

  afterEach(() => cleanup());

  it('shows the discovered total even when the current employee has no MCP tools', async () => {
    vi.mocked(api.get).mockImplementation(async (path: string) => {
      if (path.startsWith('/api/enterprise/agents?')) {
        return [{
          id: 'agent_employee',
          tenant_id: 'tenant_demo',
          name: '财务',
          is_overall: false,
          status: 'active',
          metadata: {},
          resources: [],
          created_at: '2026-08-05T00:00:00Z',
          updated_at: '2026-08-05T00:00:00Z',
        }];
      }
      if (path.startsWith('/api/enterprise/tools?')) {
        return [{
          id: 'tool_http',
          tenant_id: 'tenant_demo',
          name: 'finance.http_tool',
          display_name: 'Finance HTTP Tool',
          description: 'Regular HTTP tool',
          bucket: 'Finance',
          tool_type: 'http',
          method: 'POST',
          url: '/api/mock',
          headers: {},
          auth: {},
          mcp_config: {},
          input_schema: {},
          output_schema: {},
          allowed_skills: [],
          enabled: true,
          metadata: {},
          created_at: '2026-08-05T00:00:00Z',
          updated_at: '2026-08-05T00:00:00Z',
        }];
      }
      if (path.startsWith('/api/enterprise/mcp-servers?')) return [server];
      throw new Error(`Unexpected GET ${path}`);
    });

    render(
      <I18nProvider>
        <MemoryRouter><ToolsPage currentUser={admin} /></MemoryRouter>
      </I18nProvider>,
    );

    expect((await screen.findAllByText(/已发现 29 个/)).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/当前员工已添加 0 个/).length).toBeGreaterThan(0);
  });

  it('renders cached tools without automatic discovery and preserves them after refresh failure', async () => {
    const user = userEvent.setup();
    mockEditorGets(() => cachedInventory);
    vi.mocked(api.post).mockResolvedValue({
      success: false,
      tools: [],
      error: { code: 'MCP_DISCOVER_ERROR', message: 'provider unavailable' },
    });

    renderEditor();

    expect(await screen.findByText(/已发现 2 个/)).toBeTruthy();
    expect(api.post).not.toHaveBeenCalled();

    await user.click(screen.getByRole('button', { name: /自定义选择/ }));
    expect(await screen.findByText('get_stock_basic_info')).toBeTruthy();
    await user.click(screen.getByRole('tab', { name: '未添加 1' }));
    expect(screen.getByText('list_announcements')).toBeTruthy();
    expect(screen.queryByText('get_stock_basic_info')).toBeNull();
    await user.click(screen.getByRole('tab', { name: '已添加 1' }));
    expect(screen.getByText('get_stock_basic_info')).toBeTruthy();
    expect(screen.queryByText('list_announcements')).toBeNull();
    await user.click(screen.getByRole('tab', { name: '全部 2' }));
    await user.type(screen.getByRole('textbox', { name: '搜索 MCP 工具' }), 'announcements');
    const list = screen.getByRole('list', { name: 'MCP 工具列表' });
    expect(within(list).getByText('list_announcements')).toBeTruthy();
    expect(within(list).queryByText('get_stock_basic_info')).toBeNull();

    await user.click(screen.getByRole('button', { name: '刷新工具' }));
    expect((await screen.findByRole('alert')).textContent).toContain('刷新失败，仍显示上次结果');
    expect(screen.getByText('list_announcements')).toBeTruthy();
  });

  it('automatically discovers exactly once when no cache exists', async () => {
    let inventoryReads = 0;
    const emptyInventory: MCPToolInventoryRead = {
      cache_available: false,
      available_count: null,
      imported_count: 0,
      current_scope_count: 0,
      current_scope_is_overall: false,
      tools: [],
    };
    mockEditorGets(() => {
      inventoryReads += 1;
      return inventoryReads === 1 ? emptyInventory : cachedInventory;
    });
    vi.mocked(api.post).mockResolvedValue({ success: true, tools: cachedInventory.tools });

    renderEditor();

    await waitFor(() => expect(api.post).toHaveBeenCalledTimes(1));
    expect(vi.mocked(api.post).mock.calls[0][0]).toContain('/discover');
    expect(await screen.findByText(/已发现 2 个/)).toBeTruthy();
    expect(inventoryReads).toBe(2);
  });

  it('adds selected tools and refreshes local inventory without another discovery call', async () => {
    const user = userEvent.setup();
    let inventoryReads = 0;
    const updatedInventory: MCPToolInventoryRead = {
      ...cachedInventory,
      imported_count: 2,
      current_scope_count: 2,
      tools: cachedInventory.tools.map((tool) => ({
        ...tool,
        imported: true,
        in_current_scope: true,
      })),
    };
    mockEditorGets(() => {
      inventoryReads += 1;
      return inventoryReads === 1 ? cachedInventory : updatedInventory;
    });
    vi.mocked(api.post).mockResolvedValue({ success: true, imported: ['list_announcements'], updated: [], removed: [] });

    renderEditor();
    await screen.findByText(/已发现 2 个/);
    await user.click(screen.getByRole('button', { name: /自定义选择/ }));
    await user.click(screen.getByRole('checkbox', { name: /选择 list_announcements/ }));
    await user.click(screen.getByRole('button', { name: /添加所选（1）/ }));

    await waitFor(() => expect(screen.getAllByText(/当前员工已添加 2 个/).length).toBeGreaterThan(0));
    const postPaths = vi.mocked(api.post).mock.calls.map(([path]) => String(path));
    expect(postPaths.filter((path) => path.includes('/sync'))).toHaveLength(1);
    expect(postPaths.filter((path) => path.includes('/discover'))).toHaveLength(0);
  });

  it('imports the whole cached toolset through sync without rediscovery', async () => {
    const user = userEvent.setup();
    mockEditorGets(() => cachedInventory);
    vi.mocked(api.post).mockResolvedValue({
      success: true,
      imported: ['list_announcements'],
      updated: ['get_stock_basic_info'],
      removed: [],
    });

    renderEditor();
    await screen.findByText(/已发现 2 个/);
    await user.click(screen.getByRole('button', { name: '导入全部' }));

    await waitFor(() => expect(api.post).toHaveBeenCalledTimes(1));
    const [path, body] = vi.mocked(api.post).mock.calls[0];
    expect(String(path)).toContain('/sync');
    expect(body).toMatchObject({ tenant_id: 'tenant_demo', tool_names: null });
    expect(String(path)).not.toContain('/discover');
  });

  it('groups MCP children while preserving real statistics and orphan tools', async () => {
    const tools = [
      ...Array.from({ length: 12 }, (_, index) => makeMcpTool(index + 1)),
      makeTool({ id: 'tool_http', name: 'finance.http_tool', display_name: 'Finance HTTP Tool' }),
      makeMcpTool(99, 'missing_server'),
    ];
    mockToolsPageGets(() => tools);

    renderToolsPage();

    const viewTools = await screen.findByRole('button', { name: '查看已添加工具（12）' });
    const standaloneTable = screen.getByRole('table', { name: '工具列表' });
    expect(within(standaloneTable).getByText('Finance HTTP Tool')).toBeTruthy();
    expect(within(standaloneTable).getByText('finance_tool_99')).toBeTruthy();
    expect(within(standaloneTable).queryByText('finance_tool_1')).toBeNull();
    expect(within(screen.getByLabelText('工具统计')).getAllByText('14')).toHaveLength(2);

    await userEvent.click(viewTools);
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText('finance_tool_1')).toBeTruthy();
    expect(within(dialog).queryByText('finance_tool_11')).toBeNull();
    await userEvent.click(within(dialog).getByRole('button', { name: '下一页' }));
    expect(within(dialog).getByText('finance_tool_11')).toBeTruthy();
    expect(api.post).not.toHaveBeenCalled();
  });

  it('locates a toolset by child search and carries the query into the modal', async () => {
    const secondServer: MCPServerRead = {
      ...server,
      id: 'server_news',
      name: 'city_news',
      display_name: 'City News',
      description: 'Regional news tools',
      bucket: 'News',
      tool_count: 1,
    };
    const tools = [makeMcpTool(1), makeMcpTool(11), makeMcpTool(1, secondServer.id)];
    mockToolsPageGets(() => tools, [server, secondServer]);
    const user = userEvent.setup();

    renderToolsPage();
    const search = await screen.findByPlaceholderText('搜索工具集、工具名称、描述或分桶');
    await user.type(search, 'announcement');

    const serverTable = screen.getByRole('table', { name: 'MCP 服务器列表' });
    expect(within(serverTable).getByText('Finance Market Data')).toBeTruthy();
    expect(within(serverTable).queryByText('City News')).toBeNull();
    await user.click(screen.getByRole('button', { name: '查看匹配工具（1）' }));
    const dialog = await screen.findByRole('dialog');
    const dialogSearch = within(dialog).getByRole('textbox', { name: '搜索工具集内工具' }) as HTMLInputElement;
    expect(dialogSearch.value).toBe('announcement');
    expect(within(dialog).getByText('finance_tool_11')).toBeTruthy();
    expect(within(dialog).queryByText('finance_tool_1')).toBeNull();
    expect(api.post).not.toHaveBeenCalled();
  });

  it('keeps the MCP modal open and refreshes it after removing the last employee tool', async () => {
    let tools = [makeMcpTool(1)];
    mockToolsPageGets(() => tools);
    vi.mocked(api.delete).mockImplementation(async () => {
      tools = [];
      return { status: 'hidden' };
    });
    const user = userEvent.setup();

    renderToolsPage();
    await user.click(await screen.findByRole('button', { name: '查看已添加工具（1）' }));
    const dialog = await screen.findByRole('dialog');
    await user.click(within(dialog).getByRole('button', { name: '工具操作' }));
    await user.click(await screen.findByRole('menuitem', { name: '移除' }));
    const confirmation = await screen.findByRole('alertdialog');
    await user.click(within(confirmation).getByRole('button', { name: '移除' }));

    await waitFor(() => expect(api.delete).toHaveBeenCalledTimes(1));
    expect(await within(dialog).findByText('当前范围暂无该工具集的工具')).toBeTruthy();
    expect(screen.getByRole('dialog')).toBeTruthy();
  });

  it('groups MCP children in the overall gallery without exposing per-tool deletion', async () => {
    window.localStorage.setItem('ultrarag_enterprise_agent_scope', overallAgent.id);
    mockToolsPageGets(() => [makeMcpTool(1), makeMcpTool(2)], [server], overallAgent);
    const user = userEvent.setup();

    renderToolsPage();
    await user.click(await screen.findByRole('button', { name: '查看已导入工具（2）' }));
    const dialog = await screen.findByRole('dialog');
    await user.click(within(dialog).getAllByRole('button', { name: '工具操作' })[0]);

    expect(await screen.findByRole('menuitem', { name: '测试' })).toBeTruthy();
    expect(screen.queryByRole('menuitem', { name: '删除' })).toBeNull();
  });
});
