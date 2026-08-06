// @vitest-environment jsdom

import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import type { SkillActionCatalogRead, ToolRead } from '../types';
import { ActionCombobox, buildActionOptions } from './DistillPage';

vi.mock('@/components/AppHeader', () => ({
  default: () => null,
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
