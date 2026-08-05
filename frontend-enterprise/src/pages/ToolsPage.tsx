import { ApiOutlined, CheckOutlined, ExperimentOutlined, ToolOutlined } from '../icons';
import type { ReactNode } from 'react';
import { useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { Copy, Eye, FlaskConical, Users } from 'lucide-react';
import { pinyin } from 'pinyin-pro';

import { api, TENANT_ID } from '../api/client';
import { isEnterpriseAdmin, type EnterpriseAuthUser } from '../auth';
import AppHeader from '@/components/AppHeader';
import { ConfirmDialog } from '@/components/ConfirmDialog';
import { DataTable, type DataTableColumn } from '@/components/DataTable';
import { Paginator } from '@/components/Paginator';
import { ResourceImportDialog } from '@/components/ResourceImportDialog';
import { StatCard } from '@/components/StatCard';
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
  Checkbox,
  Dialog,
  DialogContent,
  DialogTitle,
  Input,
  Select as UISelect,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  Switch,
  Tabs,
  TabsList,
  TabsTrigger,
  Textarea,
} from '@/components/ui';
import { Button as UIButton } from '@/components/ui/button';
import { notify } from '@/components/ui/app-toast';
import { cn } from '@/lib/utils';
import {
  MENU_CONTENT_CLASS,
  MENU_ITEM_CLASS,
  MENU_ITEM_DANGER_CLASS,
  MOBILE_CARD_CLASS,
  SELECT_TRIGGER_CLASS,
  formatDateTime,
} from '@/lib/enterprise-ui';
import CodeBlock from '../components/CodeBlock';
import IconAdd from '../assets/icons/add.svg?react';
import IconArrowRight from '../assets/icons/arrow-right.svg?react';
import IconBriefcase from '../assets/icons/cap-briefcase.svg?react';
import IconChevronDown from '../assets/icons/chevron-down.svg?react';
import IconClear from '../assets/icons/field-clear.svg?react';
import IconEdit from '../assets/icons/edit.svg?react';
import IconMore from '../assets/icons/more.svg?react';
import IconRefresh from '../assets/icons/refresh.svg?react';
import IconSearch from '../assets/icons/search.svg?react';
import IconTool from '../assets/icons/plaza-tool.svg?react';
import IconTrash from '../assets/icons/trash.svg?react';
import {
  canManageEmployeeAgent,
  openGalleryAgentId,
  openGalleryImportSourceOptions,
  resourceCreatorName,
  visibleEmployeeAgents,
} from '../employee';
import { useClientPagination } from '../hooks/useClientPagination';
import { StatusBadge } from './scheduled-tasks/StatusBadge';
import type {
  AgentProfileRead,
  ToolRead,
  MCPServerRead,
  MCPServerConnection,
  MCPDiscoverResponse,
  MCPSyncResponse,
  MCPTransport,
  MCPDiscoveredTool,
  MCPToolInventoryRead,
} from '../types';

type ToolPageProps = {
  currentUser?: EnterpriseAuthUser;
  onLogout?: () => void;
};

const ENTERPRISE_AGENT_STORAGE_KEY = 'ultrarag_enterprise_agent_scope';
const TOOL_PAGE_SIZE = 10;
const TOOL_FORM_INITIAL_VALUES = {
  tool_type: 'http',
  method: 'POST',
  enabled: true,
  bucket: '未分桶',
  headers: '{}',
  auth: '{}',
  mcp_config: '{}',
  input_schema: '{}',
  output_schema: '{}',
};

type ToolFormValues = typeof TOOL_FORM_INITIAL_VALUES & {
  name?: string;
  display_name?: string;
  description?: string;
  allowed_skills?: string;
  url?: string;
};

const TRANSPORT_OPTIONS: { value: MCPTransport; label: string; hint: string }[] = [
  { value: 'streamable_http', label: 'Streamable HTTP', hint: '通过 HTTP(S) 连接远程 MCP Server' },
  { value: 'sse', label: 'SSE', hint: '通过 Server-Sent Events 连接远程 MCP Server' },
  { value: 'stdio', label: 'Stdio（本地命令）', hint: '启动本地进程并通过标准输入输出通信' },
  { value: 'builtin', label: '内置 Demo', hint: '使用内置的 builtin.demo MCP，仅用于演示' },
];

export default function ToolsPage({ currentUser, onLogout }: ToolPageProps = {}) {
  const [rows, setRows] = useState<ToolRead[]>([]);
  const [agentId, setAgentId] = useState(() => window.localStorage.getItem(ENTERPRISE_AGENT_STORAGE_KEY) || '');
  const [isOverallAgent, setIsOverallAgent] = useState(true);
  const [agentScopeLoaded, setAgentScopeLoaded] = useState(false);
  const [bucketFilter, setBucketFilter] = useState('__all__');
  const [searchText, setSearchText] = useState('');
  const [loading, setLoading] = useState(false);
  const [agents, setAgents] = useState<AgentProfileRead[]>([]);
  const [importOpen, setImportOpen] = useState(false);
  const [importMode, setImportMode] = useState<'plaza' | 'employee'>('plaza');
  const [importTargetAgentId, setImportTargetAgentId] = useState('');
  const [importSourceAgentId, setImportSourceAgentId] = useState('');
  const [importSourceTools, setImportSourceTools] = useState<ToolRead[]>([]);
  const [importSelectedToolIds, setImportSelectedToolIds] = useState<string[]>([]);
  const [importLoading, setImportLoading] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<ToolRead | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [servers, setServers] = useState<MCPServerRead[]>([]);
  const [serverDeleteTarget, setServerDeleteTarget] = useState<MCPServerRead | null>(null);
  const [deletingServer, setDeletingServer] = useState(false);
  const [selectedMcpServerId, setSelectedMcpServerId] = useState('');
  const [mcpToolSearch, setMcpToolSearch] = useState('');
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();

  const pageTitle = isOverallAgent ? '工具广场' : '工具';
  const currentAgent = useMemo(() => agents.find((item) => item.id === agentId), [agents, agentId]);
  const canManageCurrentScope = currentAgent
    ? canManageEmployeeAgent(currentAgent, currentUser)
    : isEnterpriseAdmin(currentUser) && isOverallAgent;
  const canOpenCreateMenu = canManageCurrentScope;

  const agentQuery = agentId ? `&agent_id=${encodeURIComponent(agentId)}` : '';
  const load = () => {
    if (!agentScopeLoaded) {
      setRows([]);
      return Promise.resolve();
    }
    setLoading(true);
    return Promise.all([
      api.get<ToolRead[]>(`/api/enterprise/tools?tenant_id=${TENANT_ID}${agentQuery}`),
      api
        .get<MCPServerRead[]>(`/api/enterprise/mcp-servers?tenant_id=${TENANT_ID}`)
        .catch(() => [] as MCPServerRead[]),
    ])
      .then(([toolRows, serverRows]) => {
        setRows(toolRows);
        setServers(serverRows);
      })
      .catch((error) => notify.error(error instanceof Error ? error.message : '加载工具失败'))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    if (!agentScopeLoaded) return;
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agentQuery, agentScopeLoaded]);

  useEffect(() => {
    const loadAgentScope = async () => {
      try {
        const agents = await api.get<AgentProfileRead[]>(`/api/enterprise/agents?tenant_id=${TENANT_ID}`);
        setAgents(agents);
        const exactSelectedAgent = agents.find((agent) => agent.id === agentId) || null;
        const selectedAgent = exactSelectedAgent || agents.find((agent) => agent.is_overall) || null;
        if (agentId && !exactSelectedAgent) {
          setAgentId(selectedAgent?.id || '');
        }
        setIsOverallAgent(Boolean(selectedAgent?.is_overall));
        setAgentScopeLoaded(true);
      } catch {
        setIsOverallAgent(true);
        setAgentScopeLoaded(true);
      }
    };
    void loadAgentScope();
  }, [agentId]);

  useEffect(() => {
    const onScopeChange = (event: Event) => {
      const nextAgentId = (event as CustomEvent<{ agentId?: string }>).detail?.agentId || window.localStorage.getItem(ENTERPRISE_AGENT_STORAGE_KEY) || '';
      setAgentId(nextAgentId);
    };
    window.addEventListener('ultrarag-enterprise-agent-scope-change', onScopeChange);
    return () => window.removeEventListener('ultrarag-enterprise-agent-scope-change', onScopeChange);
  }, []);

  useEffect(() => {
    if (searchParams.get('add') !== 'plaza') return;
    if (!agentScopeLoaded) return;
    const resourceId = searchParams.get('resourceId') || undefined;
    void openImportTools('plaza', resourceId);
    const next = new URLSearchParams(searchParams);
    next.delete('add');
    next.delete('resourceId');
    setSearchParams(next, { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agentScopeLoaded, isOverallAgent, searchParams, setSearchParams]);

  const visibleRows = useMemo(() => (isOverallAgent ? rows : rows.filter((row) => row.enabled)), [isOverallAgent, rows]);
  const bucketStats = useMemo(() => buildBucketStats(visibleRows), [visibleRows]);
  const bucketSelectOptions = useMemo(
    () => [
      { value: '__all__', label: '全部分桶' },
      ...bucketStats.map((item) => ({ value: item.bucket, label: `${item.bucket} (${item.total})` })),
    ],
    [bucketStats],
  );
  const serverIds = useMemo(() => new Set(servers.map((server) => server.id)), [servers]);
  const mcpToolsByServerId = useMemo(() => {
    const grouped = new Map<string, ToolRead[]>();
    for (const row of rows) {
      if (row.tool_type !== 'mcp' || !row.mcp_server_id || !serverIds.has(row.mcp_server_id)) continue;
      const group = grouped.get(row.mcp_server_id) || [];
      group.push(row);
      grouped.set(row.mcp_server_id, group);
    }
    return grouped;
  }, [rows, serverIds]);
  const standaloneRows = useMemo(
    () => visibleRows.filter((row) => (
      row.tool_type !== 'mcp'
      || !row.mcp_server_id
      || !serverIds.has(row.mcp_server_id)
    )),
    [serverIds, visibleRows],
  );
  const searchKey = searchText.trim().toLowerCase();
  const serverChildSearchMatchCounts = useMemo(() => {
    const counts = new Map<string, number>();
    if (!searchKey) return counts;
    for (const server of servers) {
      const matchingChildren = (mcpToolsByServerId.get(server.id) || [])
        .filter((tool) => toolMatchesSearch(tool, searchKey));
      if (matchingChildren.length) counts.set(server.id, matchingChildren.length);
    }
    return counts;
  }, [mcpToolsByServerId, searchKey, servers]);
  const visibleServers = useMemo(() => servers.filter((server) => {
    const children = mcpToolsByServerId.get(server.id) || [];
    const bucketMatch = bucketFilter === '__all__'
      || (server.bucket || '未分桶') === bucketFilter
      || children.some((tool) => (tool.bucket || '未分桶') === bucketFilter);
    if (!bucketMatch) return false;
    if (!searchKey) return true;
    return serverMatchesSearch(server, searchKey)
      || (serverChildSearchMatchCounts.get(server.id) || 0) > 0;
  }), [bucketFilter, mcpToolsByServerId, searchKey, serverChildSearchMatchCounts, servers]);
  const filteredStandaloneRows = useMemo(() => {
    return standaloneRows.filter((row) => {
      const bucketMatch = bucketFilter === '__all__' || (row.bucket || '未分桶') === bucketFilter;
      if (!bucketMatch) return false;
      return !searchKey || toolMatchesSearch(row, searchKey);
    });
  }, [bucketFilter, searchKey, standaloneRows]);

  const pagination = useClientPagination(
    filteredStandaloneRows,
    TOOL_PAGE_SIZE,
    `${searchText}|${bucketFilter}|${isOverallAgent}`,
  );

  const stats = useMemo(
    () => ({
      total: visibleRows.length,
      enabled: visibleRows.filter((row) => row.enabled).length,
      buckets: bucketStats.length,
    }),
    [visibleRows, bucketStats],
  );

  // MCP Server 是租户级资产；员工范围只改变“已添加”数量与移除行为。
  const agentServerToolCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const row of rows) {
      if (!row.mcp_server_id) continue;
      counts.set(row.mcp_server_id, (counts.get(row.mcp_server_id) || 0) + 1);
    }
    return counts;
  }, [rows]);
  const serverScopeToolCount = (row: MCPServerRead) =>
    isOverallAgent ? row.tool_count : agentServerToolCounts.get(row.id) || 0;
  const selectedMcpServer = servers.find((server) => server.id === selectedMcpServerId) || null;
  const selectedMcpTools = selectedMcpServer
    ? mcpToolsByServerId.get(selectedMcpServer.id) || []
    : [];
  const mcpToolSearchKey = mcpToolSearch.trim().toLowerCase();
  const filteredSelectedMcpTools = selectedMcpTools.filter(
    (tool) => !mcpToolSearchKey || toolMatchesSearch(tool, mcpToolSearchKey),
  );
  const mcpToolPagination = useClientPagination(
    filteredSelectedMcpTools,
    TOOL_PAGE_SIZE,
    `${selectedMcpServerId}|${mcpToolSearch}`,
  );

  function openMcpTools(server: MCPServerRead) {
    const matchedChildren = serverChildSearchMatchCounts.get(server.id) || 0;
    setSelectedMcpServerId(server.id);
    setMcpToolSearch(matchedChildren > 0 ? searchText : '');
  }

  const renderServerToolCounts = (row: MCPServerRead) => (
    <span className="flex flex-col gap-[2px] leading-[18px]">
      <span className="font-medium text-[#18181a]">
        {row.available_tool_count == null ? '尚未发现' : `已发现 ${row.available_tool_count} 个`}
      </span>
      <span className="text-[11px] text-[#858b9c]">
        {isOverallAgent
          ? `系统已导入 ${row.tool_count} 个`
          : `当前员工已添加 ${agentServerToolCounts.get(row.id) || 0} 个`}
      </span>
      {(mcpToolsByServerId.get(row.id)?.length || 0) > 0 && (
        <button
          type="button"
          onClick={() => openMcpTools(row)}
          className="mt-[3px] flex w-fit items-center gap-[4px] whitespace-nowrap text-[11px] font-medium text-[#1a71ff] hover:text-[#4a8dff]"
        >
          <Eye className="size-[12px] shrink-0" />
          {serverChildSearchMatchCounts.get(row.id)
            ? `查看匹配工具（${serverChildSearchMatchCounts.get(row.id)}）`
            : `${isOverallAgent ? '查看已导入工具' : '查看已添加工具'}（${mcpToolsByServerId.get(row.id)?.length || 0}）`}
        </button>
      )}
    </span>
  );

  async function confirmDelete() {
    const row = deleteTarget;
    if (!row) return;
    setDeleting(true);
    try {
      const agentSuffix = agentId ? `&agent_id=${encodeURIComponent(agentId)}` : '';
      await api.delete(`/api/enterprise/tools/${row.id}?tenant_id=${TENANT_ID}${agentSuffix}`);
      notify.success(isOverallAgent ? '已删除工具' : '已从当前员工移除');
      setDeleteTarget(null);
      await load();
    } catch (error) {
      notify.error(error instanceof Error ? error.message : isOverallAgent ? '删除失败' : '移除失败');
    } finally {
      setDeleting(false);
    }
  }

  function handleCreateAction(key: string) {
    if (key === 'blank') {
      navigate('/enterprise/tools/new');
      return;
    }
    if (key === 'mcp') {
      navigate('/enterprise/tools/mcp/new');
      return;
    }
    if (key === 'plaza') {
      void openImportTools('plaza');
      return;
    }
    if (key === 'employee') {
      void openImportTools('employee');
    }
  }

  async function confirmDeleteServer() {
    const row = serverDeleteTarget;
    if (!row || deletingServer) return;
    setDeletingServer(true);
    try {
      await api.delete(
        `/api/enterprise/mcp-servers/${row.id}?tenant_id=${TENANT_ID}${agentQuery}&remove_tools=true`,
      );
      notify.success(isOverallAgent ? '已删除' : '已从当前员工移除');
      setServerDeleteTarget(null);
      void load();
    } catch (error) {
      notify.error(error instanceof Error ? error.message : isOverallAgent ? '删除失败' : '移除失败');
    } finally {
      setDeletingServer(false);
    }
  }

  async function openImportTools(mode: 'plaza' | 'employee' = 'plaza', selectedResourceId?: string) {
    try {
      const agentRows = agents.length
        ? agents
        : await api.get<AgentProfileRead[]>(`/api/enterprise/agents?tenant_id=${TENANT_ID}`);
      setAgents(agentRows);
      setImportMode(mode);
      const targetCandidates = importTargetCandidates(agentRows);
      const nextTargetAgentId =
        targetCandidates.find((item) => item.id === agentId)?.id
        || targetCandidates[0]?.id
        || '';
      if (!nextTargetAgentId) {
        notify.warning('请先创建或选择一个数字员工，再复制工具');
        return;
      }
      setImportTargetAgentId(nextTargetAgentId);
      const firstSource = mode === 'plaza'
        ? openGalleryAgentId(agentRows)
        : visibleEmployeeAgents(agentRows, currentUser, { activeOnly: true, excludeAgentId: nextTargetAgentId })[0]?.id || '';
      setImportSourceAgentId(firstSource);
      setImportSelectedToolIds([]);
      setImportOpen(true);
      if (firstSource) {
        const sourceRows = await loadImportSourceTools(firstSource);
        if (selectedResourceId && sourceRows.some((item) => item.id === selectedResourceId)) {
          setImportSelectedToolIds([selectedResourceId]);
        }
      } else {
        setImportSourceTools([]);
      }
    } catch (error) {
      notify.error(error instanceof Error ? error.message : '加载员工失败');
    }
  }

  async function loadImportSourceTools(sourceAgentId: string): Promise<ToolRead[]> {
    setImportSourceTools([]);
    setImportSelectedToolIds([]);
    if (!sourceAgentId) return [];
    try {
      const sourceRows = await api.get<ToolRead[]>(
        `/api/enterprise/tools?tenant_id=${TENANT_ID}&agent_id=${encodeURIComponent(sourceAgentId)}`,
      );
      const enabledRows = sourceRows.filter((item) => item.enabled);
      setImportSourceTools(enabledRows);
      return enabledRows;
    } catch (error) {
      notify.error(error instanceof Error ? error.message : '加载来源工具失败');
      return [];
    }
  }

  async function submitImportTools() {
    const targetAgentId = importTargetAgentId || (!isOverallAgent ? agentId : '');
    if (!targetAgentId) {
      notify.warning('请选择要复制到的数字员工');
      return;
    }
    if (!importSourceAgentId) {
      notify.warning(importMode === 'plaza' ? '请选择开放广场' : '请选择复制来源员工');
      return;
    }
    if (importSelectedToolIds.length === 0) {
      notify.warning('请选择要复制的工具');
      return;
    }
    setImportLoading(true);
    try {
      const result = await api.post<{ imported: Array<Record<string, unknown>>; missing: Array<Record<string, unknown>> }>(
        `/api/enterprise/agents/${targetAgentId}/resources/import`,
        {
          tenant_id: TENANT_ID,
          source_agent_id: importSourceAgentId,
          resource_type: 'tool',
          resource_ids: importSelectedToolIds,
        },
      );
      const importedCount = result.imported?.length || 0;
      const missingCount = result.missing?.length || 0;
      notify.success(`已复制 ${importedCount} 个工具${missingCount ? `，${missingCount} 个未复制` : ''}`);
      setImportOpen(false);
      if (targetAgentId !== agentId) {
        window.localStorage.setItem(ENTERPRISE_AGENT_STORAGE_KEY, targetAgentId);
        window.dispatchEvent(new CustomEvent('ultrarag-enterprise-agent-scope-change', { detail: { agentId: targetAgentId } }));
        setAgentId(targetAgentId);
      } else {
        await load();
      }
    } catch (error) {
      notify.error(error instanceof Error ? error.message : '复制工具失败');
    } finally {
      setImportLoading(false);
    }
  }

  function importTargetCandidates(agentRows: AgentProfileRead[] = agents): AgentProfileRead[] {
    return agentRows.filter((item) => (
      !item.is_overall
      && item.status === 'active'
      && canManageEmployeeAgent(item, currentUser)
    ));
  }

  function handleImportTargetChange(nextTargetAgentId: string) {
    setImportTargetAgentId(nextTargetAgentId);
    if (importMode !== 'employee' || importSourceAgentId !== nextTargetAgentId) return;
    const nextSource = visibleEmployeeAgents(agents, currentUser, {
      activeOnly: true,
      excludeAgentId: nextTargetAgentId,
    })[0]?.id || '';
    setImportSourceAgentId(nextSource);
    void loadImportSourceTools(nextSource);
  }

  function renderActions(row: ToolRead) {
    const isMcpChild = row.tool_type === 'mcp' && Boolean(row.mcp_server_id);
    const canRemoveFromScope = canManageCurrentScope && (!isMcpChild || !isOverallAgent);
    return (
      <DropdownMenu>
        <DropdownMenuTrigger
          aria-label="工具操作"
          className="ml-auto grid size-7 place-items-center rounded-[8px] text-[#1a71ff] transition-colors outline-none hover:bg-black/5 hover:text-[#4a8dff] focus-visible:bg-black/5"
        >
          <IconMore className="size-3.5" />
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className={MENU_CONTENT_CLASS}>
          {canManageCurrentScope && (
            <DropdownMenuItem
              className={MENU_ITEM_CLASS}
              onSelect={() => navigate(
                isMcpChild
                  ? `/enterprise/tools/mcp/${row.mcp_server_id}/edit`
                  : `/enterprise/tools/${row.id}/edit`,
              )}
            >
              <IconEdit />
              {isMcpChild ? '编辑工具集' : '编辑'}
            </DropdownMenuItem>
          )}
          <DropdownMenuItem className={MENU_ITEM_CLASS} onSelect={() => navigate(`/enterprise/tools/${row.id}/test`)}>
            <FlaskConical />
            测试
          </DropdownMenuItem>
          {canRemoveFromScope && (
            <>
              <DropdownMenuSeparator className="my-[2px] bg-[#eef0f4]" />
              <DropdownMenuItem
                variant="destructive"
                className={MENU_ITEM_DANGER_CLASS}
                onSelect={() => setDeleteTarget(row)}
              >
                <IconTrash />
                {isOverallAgent ? '删除' : '移除'}
              </DropdownMenuItem>
            </>
          )}
        </DropdownMenuContent>
      </DropdownMenu>
    );
  }

  const columns: DataTableColumn<ToolRead>[] = [
    {
      key: 'name',
      title: '工具名称',
      width: 200,
      className: 'text-[#18181a]',
      render: (row) => (
        <div className="flex min-w-0 flex-col gap-[2px]">
          <span className="truncate font-medium leading-[18px] text-[#18181a]" title={row.display_name || row.name}>
            {row.display_name || row.name}
          </span>
          <span className="truncate text-[#858b9c]" title={row.name}>
            {row.name}
          </span>
        </div>
      ),
    },
    {
      key: 'bucket',
      title: '分桶',
      width: 130,
      render: (row) => <StatusBadge tone="gray">{row.bucket || '未分桶'}</StatusBadge>,
    },
    {
      key: 'type',
      title: '类型',
      width: 90,
      render: (row) => (
        <StatusBadge tone={row.tool_type === 'mcp' ? 'blue' : 'gray'}>{row.tool_type === 'mcp' ? 'MCP' : 'HTTP'}</StatusBadge>
      ),
    },
    {
      key: 'creator',
      title: '创建者',
      width: 120,
      render: (row) => (
        <span className="block truncate text-[#858b9c]" title={resourceCreatorName(row)}>
          {resourceCreatorName(row) || '-'}
        </span>
      ),
    },
    { key: 'method', title: 'Method', width: 96, render: (row) => row.method },
    {
      key: 'url',
      title: 'URL',
      className: 'whitespace-normal',
      render: (row) => <span className="line-clamp-1 wrap-break-word text-[#858b9c]">{row.url}</span>,
    },
    {
      key: 'enabled',
      title: '启用',
      width: 90,
      render: (row) => (
        <StatusBadge tone={row.enabled ? 'green' : 'gray'}>{row.enabled ? '已启用' : '已停用'}</StatusBadge>
      ),
    },
    {
      key: 'actions',
      title: '操作',
      width: 70,
      align: 'right',
      render: (row) => renderActions(row),
    },
  ];

  const serverColumns: DataTableColumn<MCPServerRead>[] = [
    {
      key: 'name',
      title: '名称',
      width: 220,
      render: (row) => (
        <div className="flex min-w-0 flex-col gap-[4px]">
          <span className="flex w-full min-w-0 items-center gap-[6px]">
            <span className="min-w-0 flex-1 truncate font-medium leading-[18px] text-[#18181a]" title={row.display_name || row.name}>
              {row.display_name || row.name}
            </span>
            <span className="shrink-0">
              <StatusBadge tone="blue">工具集</StatusBadge>
            </span>
          </span>
          <span className="truncate text-[#858b9c]" title={row.name}>
            {row.name}
          </span>
        </div>
      ),
    },
    {
      key: 'transport',
      title: '连接方式',
      width: 140,
      render: (row) => <StatusBadge tone="gray">{transportLabel(row.connection.transport)}</StatusBadge>,
    },
    {
      key: 'endpoint',
      title: '端点',
      className: 'whitespace-normal',
      render: (row) => (
        <span className="line-clamp-1 wrap-break-word text-[#858b9c]">{serverEndpoint(row.connection)}</span>
      ),
    },
    {
      key: 'tool_count',
      title: '工具数',
      width: 170,
      render: (row) => renderServerToolCounts(row),
    },
    {
      key: 'enabled',
      title: '启用',
      width: 90,
      render: (row) => (
        <StatusBadge tone={row.enabled ? 'green' : 'gray'}>{row.enabled ? '已启用' : '已停用'}</StatusBadge>
      ),
    },
    {
      key: 'actions',
      title: '操作',
      width: 224,
      align: 'right',
      className: 'whitespace-nowrap',
      render: (row) => (
        <div className="flex items-center justify-end gap-[8px]">
          <UIButton
            variant="outline"
            size="sm"
            onClick={() => navigate(`/enterprise/tools/mcp/${row.id}/edit`)}
            disabled={!canManageCurrentScope}
            className={RETURN_BUTTON_CLASS}
          >
            <IconRefresh className="size-[14px] shrink-0" />
            发现/同步
          </UIButton>
          {canManageCurrentScope && (isOverallAgent || agentServerToolCounts.has(row.id)) && (
            <UIButton
              variant="outline"
              size="sm"
              onClick={() => setServerDeleteTarget(row)}
              className={cn(RETURN_BUTTON_CLASS, 'text-[#e5484d] hover:text-[#e5484d]')}
            >
              {isOverallAgent ? '删除' : '移除'}
            </UIButton>
          )}
        </div>
      ),
    },
  ];

  const renderMobileCard = (row: ToolRead) => (
    <article className={MOBILE_CARD_CLASS} key={row.id}>
      <div className="flex min-w-0 items-start justify-between gap-[10px]">
        <div className="min-w-0">
          <strong className="block truncate text-[14px] font-semibold text-[#18181a]">
            {row.display_name || row.name}
          </strong>
          <span className="mt-[2px] block truncate text-[12px] text-[#858b9c]">{row.name}</span>
          <span className="mt-[2px] block truncate text-[12px] text-[#858b9c]">创建者：{resourceCreatorName(row) || '-'}</span>
        </div>
        {renderActions(row)}
      </div>
      <div className="mt-[8px] flex flex-wrap items-center gap-[6px]">
        <StatusBadge tone="gray">{row.bucket || '未分桶'}</StatusBadge>
        <StatusBadge tone={row.tool_type === 'mcp' ? 'blue' : 'gray'}>{row.tool_type === 'mcp' ? 'MCP' : 'HTTP'}</StatusBadge>
        <StatusBadge tone={row.enabled ? 'green' : 'gray'}>{row.enabled ? '已启用' : '已停用'}</StatusBadge>
      </div>
      <p className="mt-[8px] line-clamp-1 wrap-break-word text-[12px] text-[#858b9c]">
        {row.method} · {row.url}
      </p>
    </article>
  );

  const listEmptyText = isOverallAgent
    ? canManageCurrentScope ? '暂无独立工具，点击「新增」创建一个吧' : '暂无独立工具'
    : '当前员工暂无独立工具';

  return (
    <div className="min-h-full box-border px-[48px] pt-[32px] pb-[43px] max-[900px]:px-[16px]">
      <AppHeader onLogout={onLogout} userName={currentUser?.username} title={pageTitle} />

      <div className="mt-[20px] mb-[16px] flex items-center justify-end gap-[12px]">
        <UIButton
          variant="outline"
          onClick={() => void load()}
          disabled={loading}
          className="h-[34px] gap-[4px] rounded-[10px] border-[0.5px] border-[#e3e7f1] bg-white px-[20px] text-[12px] font-normal text-[#757f9c] hover:border-[#cbd3e6] hover:bg-white hover:text-[#18181a]"
        >
          <IconRefresh className={cn('size-[14px]', loading && 'animate-spin')} />
          刷新
        </UIButton>
        {canOpenCreateMenu && (
          <DropdownMenu>
            <DropdownMenuTrigger data-guide-target="tools-create" className="flex h-[34px] items-center gap-[4px] rounded-[10px] bg-[#18181a] px-[20px] text-[12px] font-normal text-white outline-none transition-colors hover:bg-[#303030]">
              <IconAdd className="size-[14px]" />
              新增
              <IconChevronDown className="size-[12px]" />
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className={MENU_CONTENT_CLASS}>
              {canManageCurrentScope && (
                <DropdownMenuItem className={MENU_ITEM_CLASS} onSelect={() => handleCreateAction('blank')}>
                  <IconAdd />
                  新建空白工具
                </DropdownMenuItem>
              )}
              {!isOverallAgent && (
                <DropdownMenuItem className={MENU_ITEM_CLASS} onSelect={() => handleCreateAction('plaza')}>
                  <IconTool className="size-[14px]" />
                  从广场复制
                </DropdownMenuItem>
              )}
              {!isOverallAgent && (
                <DropdownMenuItem className={MENU_ITEM_CLASS} onSelect={() => handleCreateAction('employee')}>
                  <FlaskConical />
                  从数字员工复制
                </DropdownMenuItem>
              )}
            </DropdownMenuContent>
          </DropdownMenu>
        )}
      </div>

      <div className="flex flex-col gap-[24px] rounded-[20px_20px_0_0] bg-white p-[18px_18px_24px_18px] shadow-[0_-4px_16px_0_rgba(0,0,0,0.05)]">
        <div className="flex flex-wrap items-stretch gap-[20px]" aria-label="工具统计">
          <StatCard label="工具总数" value={stats.total} className="basis-[220px]" />
          <StatCard label="已启用" value={stats.enabled} tone="green" className="basis-[220px]" />
          <StatCard label="分桶" value={stats.buckets} className="basis-[220px]" />
        </div>

        <div className="flex flex-wrap items-center gap-[16px]" aria-label="工具筛选">
          <label className="flex h-[34px] w-[300px] items-center gap-[8px] overflow-hidden rounded-[10px] border-[0.5px] border-[#e3e7f1] bg-white px-[12px] transition-colors focus-within:border-[#18181a] max-[900px]:w-full">
            <IconSearch className="size-[14px] shrink-0 text-[#858b9c]" />
            <input
              autoComplete="off"
              data-1p-ignore="true"
              data-lpignore="true"
              data-bwignore="true"
              value={searchText}
              placeholder="搜索工具集、工具名称、描述或分桶"
              onChange={(event) => setSearchText(event.target.value)}
              className="h-full min-w-0 flex-1 bg-transparent text-[12px] text-[#17191f] outline-none placeholder:text-[#c0c6d4]"
            />
            {searchText && (
              <button
                type="button"
                aria-label="清除搜索"
                onClick={() => setSearchText('')}
                className="grid size-[16px] shrink-0 place-items-center text-[#c0c6d4] hover:text-[#858b9c]"
              >
                <IconClear className="size-[14px]" />
              </button>
            )}
          </label>
          <UISelect value={bucketFilter} onValueChange={setBucketFilter}>
            <SelectTrigger className={cn(SELECT_TRIGGER_CLASS, 'w-[180px]')} aria-label="分桶筛选">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {bucketSelectOptions.map((item) => (
                <SelectItem key={item.value} value={item.value}>
                  {item.label}
                </SelectItem>
              ))}
            </SelectContent>
          </UISelect>
        </div>

        {servers.length > 0 && (
          <div className="flex flex-col gap-[18px]">
            <div className="flex items-center gap-[6px] px-[12px] text-[#757f9c]">
              <ApiOutlined className="size-[14px] shrink-0" />
              <span className="text-[14px] font-normal leading-none">MCP 服务器（工具集）</span>
            </div>
            <div className="hidden md:block">
              <DataTable
                aria-label="MCP 服务器列表"
                columns={serverColumns}
                data={visibleServers}
                rowKey={(row) => row.id}
                loading={loading}
                emptyText="没有符合条件的 MCP 工具集"
              />
            </div>
            <div className="grid gap-[10px] md:hidden">
              {visibleServers.length ? visibleServers.map((row) => (
                <article className={MOBILE_CARD_CLASS} key={row.id}>
                  <div className="flex min-w-0 items-start justify-between gap-[10px]">
                    <div className="min-w-0">
                      <strong className="block truncate text-[14px] font-semibold text-[#18181a]">
                        {row.display_name || row.name}
                      </strong>
                      <span className="mt-[2px] block truncate text-[12px] text-[#858b9c]">{row.name}</span>
                    </div>
                    <span className="shrink-0">
                      <StatusBadge tone="blue">工具集</StatusBadge>
                    </span>
                  </div>
                  <div className="mt-[8px] flex flex-wrap items-center gap-[6px]">
                    <StatusBadge tone="gray">{transportLabel(row.connection.transport)}</StatusBadge>
                    <StatusBadge tone={row.enabled ? 'green' : 'gray'}>{row.enabled ? '已启用' : '已停用'}</StatusBadge>
                    <StatusBadge tone="gray">
                      {row.available_tool_count == null ? '尚未发现' : `已发现 ${row.available_tool_count} 个`}
                    </StatusBadge>
                    <StatusBadge tone="gray">
                      {isOverallAgent
                        ? `系统已导入 ${row.tool_count} 个`
                        : `当前员工已添加 ${agentServerToolCounts.get(row.id) || 0} 个`}
                    </StatusBadge>
                  </div>
                  <p className="mt-[8px] line-clamp-1 wrap-break-word text-[12px] text-[#858b9c]">
                    {serverEndpoint(row.connection)}
                  </p>
                  <div className="mt-[10px] flex flex-wrap items-center gap-[8px]">
                    {(mcpToolsByServerId.get(row.id)?.length || 0) > 0 && (
                      <UIButton
                        variant="outline"
                        size="sm"
                        onClick={() => openMcpTools(row)}
                        className={RETURN_BUTTON_CLASS}
                      >
                        <Eye className="size-[14px] shrink-0" />
                        {serverChildSearchMatchCounts.get(row.id)
                          ? `匹配工具（${serverChildSearchMatchCounts.get(row.id)}）`
                          : `查看工具（${mcpToolsByServerId.get(row.id)?.length || 0}）`}
                      </UIButton>
                    )}
                    <UIButton
                      variant="outline"
                      size="sm"
                      onClick={() => navigate(`/enterprise/tools/mcp/${row.id}/edit`)}
                      className={RETURN_BUTTON_CLASS}
                    >
                      <IconRefresh className="size-[14px] shrink-0" />
                      发现/同步
                    </UIButton>
                    {canManageCurrentScope && (isOverallAgent || agentServerToolCounts.has(row.id)) && (
                      <UIButton
                        variant="outline"
                        size="sm"
                        onClick={() => setServerDeleteTarget(row)}
                        className={cn(RETURN_BUTTON_CLASS, 'text-[#e5484d] hover:text-[#e5484d]')}
                      >
                        {isOverallAgent ? '删除' : '移除'}
                      </UIButton>
                    )}
                  </div>
                </article>
              )) : (
                <div className="py-[32px] text-center text-[13px] text-[#858b9c]">
                  没有符合条件的 MCP 工具集
                </div>
              )}
            </div>
          </div>
        )}

        <div className="flex flex-col gap-[18px]">
          <div className="flex items-center gap-[6px] px-[12px] text-[#757f9c]">
            <IconBriefcase className="size-[14px] shrink-0" />
            <span className="text-[14px] font-normal leading-none">独立工具</span>
          </div>

          <div className="grid gap-[10px] md:hidden">
            {filteredStandaloneRows.length ? (
              pagination.pagedItems.map(renderMobileCard)
            ) : (
              <div className="py-[40px] text-center text-[13px] text-[#858b9c]">{listEmptyText}</div>
            )}
          </div>

          <div className="hidden md:block">
            <DataTable
              aria-label="工具列表"
              columns={columns}
              data={pagination.pagedItems}
              rowKey={(row) => row.id}
              loading={loading}
              emptyText={listEmptyText}
            />
          </div>

          {filteredStandaloneRows.length > 0 && (
            <Paginator
              aria-label="工具分页"
              className="mt-0 mb-[6px]"
              page={pagination.page}
              pageCount={pagination.pageCount}
              onChange={pagination.setPage}
            />
          )}
        </div>
      </div>

      <Dialog
        open={Boolean(selectedMcpServer)}
        onOpenChange={(open) => {
          if (open) return;
          setSelectedMcpServerId('');
          setMcpToolSearch('');
        }}
      >
        <DialogContent
          aria-describedby={undefined}
          className="flex max-h-[calc(100dvh-32px)] w-[calc(100%-32px)] flex-col gap-0 overflow-hidden rounded-[14px] p-0 sm:max-w-[760px]"
        >
          <div className="border-b border-[#eceef1] px-[20px] py-[16px] pr-[52px]">
            <div className="flex min-w-0 items-center gap-[8px]">
              <ApiOutlined className="size-[15px] shrink-0 text-[#757f9c]" />
              <DialogTitle className="min-w-0 truncate text-[15px] font-semibold leading-[20px] text-[#18181a]">
                {selectedMcpServer?.display_name || selectedMcpServer?.name || 'MCP 工具集'}
              </DialogTitle>
              <StatusBadge tone="blue">{selectedMcpTools.length} 个工具</StatusBadge>
            </div>
            <p className="mt-[6px] line-clamp-2 wrap-break-word text-[12px] leading-[1.6] text-[#858b9c]">
              {selectedMcpServer?.description || '暂无工具集描述'}
            </p>
          </div>

          <div className="flex min-h-0 flex-1 flex-col gap-[12px] px-[20px] py-[16px]">
            <label className="flex h-[34px] w-full items-center gap-[8px] overflow-hidden rounded-[10px] border-[0.5px] border-[#e3e7f1] bg-white px-[12px] transition-colors focus-within:border-[#18181a]">
              <IconSearch className="size-[14px] shrink-0 text-[#858b9c]" />
              <input
                autoComplete="off"
                value={mcpToolSearch}
                aria-label="搜索工具集内工具"
                placeholder="搜索工具名称或描述"
                onChange={(event) => setMcpToolSearch(event.target.value)}
                className="h-full min-w-0 flex-1 bg-transparent text-[12px] text-[#17191f] outline-none placeholder:text-[#c0c6d4]"
              />
              {mcpToolSearch && (
                <button
                  type="button"
                  aria-label="清除工具集搜索"
                  onClick={() => setMcpToolSearch('')}
                  className="grid size-[16px] shrink-0 place-items-center text-[#c0c6d4] hover:text-[#858b9c]"
                >
                  <IconClear className="size-[14px]" />
                </button>
              )}
            </label>

            <div className="min-h-0 flex-1 overflow-y-auto border-y border-[#eceef1]" role="list" aria-label="工具集内工具">
              {mcpToolPagination.pagedItems.length ? mcpToolPagination.pagedItems.map((tool) => (
                <div
                  key={tool.id}
                  role="listitem"
                  className="flex min-w-0 items-start gap-[10px] border-b border-[#eceef1] px-[2px] py-[12px] last:border-b-0"
                >
                  <div className="min-w-0 flex-1">
                    <div className="flex min-w-0 flex-wrap items-center gap-[6px]">
                      <strong className="min-w-0 wrap-break-word text-[13px] font-medium text-[#18181a]">
                        {tool.display_name || tool.name}
                      </strong>
                      <StatusBadge tone={tool.enabled ? 'green' : 'gray'}>
                        {tool.enabled ? '已启用' : '已停用'}
                      </StatusBadge>
                    </div>
                    <span className="mt-[2px] block truncate text-[11px] text-[#858b9c]" title={tool.name}>
                      {tool.name}
                    </span>
                    <p className="mt-[5px] line-clamp-2 wrap-break-word text-[12px] leading-[1.6] text-[#667085]">
                      {tool.description || '暂无描述'}
                    </p>
                  </div>
                  <div className="shrink-0">{renderActions(tool)}</div>
                </div>
              )) : (
                <div className="grid min-h-[180px] place-items-center px-[16px] text-center text-[13px] text-[#858b9c]">
                  {selectedMcpTools.length ? '没有符合条件的工具' : '当前范围暂无该工具集的工具'}
                </div>
              )}
            </div>

            {filteredSelectedMcpTools.length > 0 && (
              <Paginator
                aria-label="工具集工具分页"
                className="my-0"
                page={mcpToolPagination.page}
                pageCount={mcpToolPagination.pageCount}
                onChange={mcpToolPagination.setPage}
              />
            )}
          </div>
        </DialogContent>
      </Dialog>

      <ResourceImportDialog
        open={importOpen}
        loading={importLoading}
        icon={<IconTool className="size-[14px] shrink-0" />}
        title={importMode === 'plaza' ? '从广场复制工具' : '从数字员工复制工具'}
        targetLabel="复制到"
        targetPlaceholder="选择目标员工"
        targets={importTargetCandidates().map((item) => ({ value: item.id, label: item.name }))}
        targetId={importTargetAgentId}
        sourcePlaceholder={importMode === 'plaza' ? '选择开放广场' : '选择复制来源'}
        sources={importMode === 'plaza'
          ? openGalleryImportSourceOptions(agents, '开放广场')
          : visibleEmployeeAgents(agents, currentUser, { activeOnly: true, excludeAgentId: importTargetAgentId })
            .map((item) => ({ value: item.id, label: item.name }))}
        sourceId={importSourceAgentId}
        itemsLabel="选择工具"
        items={importSourceTools.map((item) => ({
          id: item.id,
          label: (
            <>
              {item.display_name || item.name}
              <span className="text-[#858b9c]"> · {item.name}</span>
            </>
          ),
        }))}
        selectedIds={importSelectedToolIds}
        emptyText="没有可复制的工具"
        note={
          importMode === 'plaza'
            ? '从开放广场复制可用工具；复制后会成为当前员工的本地工具绑定。'
            : '从数字员工复制可用工具；不可见内容不会出现在列表。'
        }
        onTargetChange={handleImportTargetChange}
        onSourceChange={(value) => {
          setImportSourceAgentId(value);
          void loadImportSourceTools(value);
        }}
        onSelectedChange={setImportSelectedToolIds}
        onClose={() => setImportOpen(false)}
        onSubmit={() => void submitImportTools()}
      />

      <ConfirmDialog
        open={Boolean(deleteTarget)}
        onOpenChange={(open) => !open && setDeleteTarget(null)}
        loading={deleting}
        title={deleteTarget ? `${isOverallAgent ? '删除' : '移除'}工具「${deleteTarget.display_name || deleteTarget.name}」？` : ''}
        description={
          isOverallAgent
            ? '删除后，引用该工具的技能将无法继续调用它，操作不可撤销。'
            : '从当前员工移除后，工具广场中的原始工具不会被删除。'
        }
        confirmText={isOverallAgent ? '删除' : '移除'}
        onConfirm={() => void confirmDelete()}
      />

      <ConfirmDialog
        open={Boolean(serverDeleteTarget)}
        onOpenChange={(open) => {
          if (!open) setServerDeleteTarget(null);
        }}
        loading={deletingServer}
        title={
          serverDeleteTarget
            ? `${isOverallAgent ? '删除' : '移除'} MCP 服务器「${serverDeleteTarget.display_name || serverDeleteTarget.name}」？`
            : ''
        }
        description={
          isOverallAgent
            ? `其下 ${serverDeleteTarget ? serverScopeToolCount(serverDeleteTarget) : 0} 个已导入工具将一并删除，操作不可撤销。`
            : `将从当前员工移除该工具集的 ${serverDeleteTarget ? serverScopeToolCount(serverDeleteTarget) : 0} 个工具，工具集本身和其他员工不受影响。`
        }
        confirmText={isOverallAgent ? '删除' : '移除'}
        onConfirm={() => void confirmDeleteServer()}
      />
    </div>
  );
}

export function ToolNewPage(props: ToolPageProps = {}) {
  return <ToolEditorPage mode="new" {...props} />;
}

export function ToolEditPage(props: ToolPageProps = {}) {
  return <ToolEditorPage mode="edit" {...props} />;
}

export function McpServerNewPage(props: ToolPageProps = {}) {
  return <McpServerEditorPage mode="new" {...props} />;
}

export function McpServerEditPage(props: ToolPageProps = {}) {
  return <McpServerEditorPage mode="edit" {...props} />;
}

/**
 * 新建工具时顶部的类型切换条：HTTP 工具 / MCP 服务器。
 * 点击即跳转到对应的新建页，体验上像同一个「新建工具」流程里的分支。
 */
function ToolTypeSwitcher({ active }: { active: 'http' | 'mcp' }) {
  const navigate = useNavigate();
  const options: { value: 'http' | 'mcp'; label: string; hint: string; to: string }[] = [
    { value: 'http', label: 'HTTP 工具', hint: '配置单个 HTTP 接口作为工具', to: '/enterprise/tools/new' },
    { value: 'mcp', label: 'MCP 服务器', hint: '连接 MCP Server，自动发现并同步其工具集', to: '/enterprise/tools/mcp/new' },
  ];
  return (
    <div className="mb-[16px] flex flex-col gap-[8px]">
      <span className={FIELD_LABEL_CLASS}>工具类型</span>
      <div className="flex flex-wrap gap-[10px]">
        {options.map((option) => {
          const isActive = option.value === active;
          return (
            <button
              key={option.value}
              type="button"
              onClick={() => {
                if (!isActive) navigate(option.to);
              }}
              className={cn(
                'relative flex min-w-[200px] flex-1 items-start gap-[10px] rounded-[12px] border px-[16px] py-[12px] text-left transition-all',
                isActive
                  ? 'border-[#18181a] bg-[#18181a] shadow-[0_4px_12px_0_rgba(24,24,26,0.18)]'
                  : 'border-[#e3e7f1] bg-white hover:border-[#cbd3e6] hover:bg-[#fafbfc]',
              )}
              aria-pressed={isActive}
            >
              <span
                className={cn(
                  'flex size-[28px] shrink-0 items-center justify-center rounded-[8px]',
                  isActive ? 'bg-white/15 text-white' : 'bg-[#f2f3f7] text-[#757f9c]',
                )}
              >
                {option.value === 'mcp' ? <ApiOutlined className="size-[15px] shrink-0" /> : <IconTool className="size-[15px] shrink-0" />}
              </span>
              <span className="flex min-w-0 flex-1 flex-col gap-[2px]">
                <span className={cn('text-[13px] font-semibold', isActive ? 'text-white' : 'text-[#18181a]')}>
                  {option.label}
                </span>
                <span className={cn('text-[12px] leading-[1.5]', isActive ? 'text-white/70' : 'text-[#858b9c]')}>
                  {option.hint}
                </span>
              </span>
              {isActive && (
                <span className="absolute top-[10px] right-[10px] flex size-[16px] shrink-0 items-center justify-center rounded-full bg-white text-[#18181a]">
                  <CheckOutlined className="size-[10px] shrink-0" />
                </span>
              )}
            </button>
          );
        })}
      </div>
    </div>
  );
}

function ToolEditorPage({ mode, currentUser, onLogout }: { mode: 'new' | 'edit' } & ToolPageProps) {
  const [values, setValues] = useState<ToolFormValues>({ ...TOOL_FORM_INITIAL_VALUES });
  const [tool, setTool] = useState<ToolRead | null>(null);
  const [loading, setLoading] = useState(false);
  const [bucketOptions, setBucketOptions] = useState<{ value: string; label: string }[]>([{ value: '未分桶', label: '未分桶' }]);
  const navigate = useNavigate();
  const { toolId } = useParams();
  const isEdit = mode === 'edit';

  const setField = <K extends keyof ToolFormValues>(name: K, value: ToolFormValues[K]) =>
    setValues((prev) => ({ ...prev, [name]: value }));

  useEffect(() => {
    void loadBucketOptions().then(setBucketOptions);
  }, []);

  useEffect(() => {
    if (!isEdit) {
      setValues({ ...TOOL_FORM_INITIAL_VALUES });
      setTool(null);
      return;
    }
    if (!toolId) return;
    setLoading(true);
    const agentQuery = currentAgentQuery();
    api
      .get<ToolRead>(`/api/enterprise/tools/${toolId}?tenant_id=${TENANT_ID}${agentQuery}`)
      .then((row) => {
        setTool(row);
        setValues(toolToFormValues(row));
      })
      .catch((error) => notify.error(error instanceof Error ? error.message : '加载工具失败'))
      .finally(() => setLoading(false));
  }, [isEdit, toolId]);

  async function save() {
    if (!String(values.name || '').trim()) {
      notify.error('请填写工具名称');
      return;
    }
    if (!String(values.url || '').trim()) {
      notify.error('请填写 URL');
      return;
    }
    const payload = buildToolPayload(values);
    if (!payload) return;
    setLoading(true);
    try {
      const agentQuery = currentAgentQuery();
      const saved = isEdit && toolId
        ? await api.put<ToolRead>(`/api/enterprise/tools/${toolId}${agentQuery ? `?${agentQuery.slice(1)}` : ''}`, payload)
        : await api.post<ToolRead>(`/api/enterprise/tools${agentQuery ? `?${agentQuery.slice(1)}` : ''}`, payload);
      notify.success('已保存');
      setTool(saved);
      setValues(toolToFormValues(saved));
      if (!isEdit) {
        navigate(`/enterprise/tools/${saved.id}/edit`, { replace: true });
      }
    } catch (error) {
      notify.error(error instanceof Error ? error.message : '保存失败');
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-h-full box-border px-[48px] pt-[32px] pb-[43px] max-[900px]:px-[16px]" aria-busy={loading}>
      <AppHeader
        onLogout={onLogout}
        userName={currentUser?.username}
        title={isEdit ? '编辑工具' : '新建工具'}
        description={
          isEdit
            ? '修改工具定义，并在右侧验证当前配置或已保存版本。'
            : '选择工具类型并填写定义，可先用右侧探测区测试请求与返回结构。'
        }
      />
      <div className="mt-[20px] mb-[16px] flex flex-wrap justify-end gap-[16px]">
        <UIButton variant="outline" onClick={() => navigate('/enterprise/tools')} className={RETURN_BUTTON_CLASS}>
          <IconArrowRight className="size-3.5 rotate-180" />
          返回工具
        </UIButton>
        {isEdit && tool && (
          <UIButton
            variant="outline"
            onClick={() => navigate(`/enterprise/tools/${tool.id}/test`)}
            className={RETURN_BUTTON_CLASS}
          >
            <ExperimentOutlined />
            打开测试页
          </UIButton>
        )}
        <UIButton disabled={loading} onClick={() => void save()} className={PRIMARY_BUTTON_CLASS}>
          保存
        </UIButton>
      </div>
      {!isEdit && <ToolTypeSwitcher active="http" />}
      <div className="grid grid-cols-1 items-start gap-[20px] xl:grid-cols-2">
        <SectionCard title="工具定义" loading={loading && isEdit && !tool}>
          <ToolFormFields values={values} setField={setField} bucketOptions={bucketOptions} lockName={isEdit} />
        </SectionCard>
        <div className="flex w-full flex-col gap-[20px]">
          <ToolProbeCard values={values} />
          {isEdit && tool && <SavedToolTestCard tool={tool} />}
        </div>
      </div>
    </div>
  );
}

const CARD_CLASS =
  'rounded-[14px] border border-[#eceef1] bg-white';
const CARD_TITLE_CLASS = 'text-[14px] font-medium text-[#18181a]';
const FIELD_LABEL_CLASS = 'text-[13px] font-medium text-[#18181a]';
const SUBSECTION_TITLE_CLASS = 'text-[13px] font-medium text-[#18181a]';
const HINT_CLASS = 'text-[12px] leading-[1.55] text-[#858b9c]';
const MONO_INPUT_CLASS = 'font-mono text-[12px] leading-[1.65]';
const RETURN_BUTTON_CLASS =
  'h-8 gap-1 rounded-[10px] border-[0.5px] border-[#e3e7f1] bg-white px-5 text-[12px] font-normal text-[#757f9c] hover:border-[#cbd3e6] hover:bg-white hover:text-[#18181a]';
const PRIMARY_BUTTON_CLASS =
  'h-8 gap-1 rounded-[10px] bg-[#18181a] px-5 text-[12px] font-normal text-white hover:bg-[#303030]';

function SectionCard({
  title,
  extra,
  loading,
  children,
  className,
  bodyClassName,
}: {
  title?: ReactNode;
  extra?: ReactNode;
  loading?: boolean;
  children?: ReactNode;
  className?: string;
  bodyClassName?: string;
}) {
  return (
    <section className={cn(CARD_CLASS, 'overflow-hidden', className)}>
      {(title || extra) && (
        <div className="flex min-h-[54px] items-center justify-between gap-[12px] border-b border-[#eceef1] px-[20px] py-[10px]">
          <div className={cn('min-w-0', CARD_TITLE_CLASS)}>{title}</div>
          {extra ? <div className="shrink-0">{extra}</div> : null}
        </div>
      )}
      <div className={cn('p-[20px]', bodyClassName)}>
        {loading ? (
          <div className="py-[24px] text-center text-[13px] text-[#858b9c]">加载中…</div>
        ) : (
          children
        )}
      </div>
    </section>
  );
}

function Field({
  label,
  htmlFor,
  hint,
  children,
}: {
  label: string;
  htmlFor?: string;
  hint?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-[6px]">
      <label htmlFor={htmlFor} className={FIELD_LABEL_CLASS}>
        {label}
      </label>
      {children}
      {hint ? <span className={HINT_CLASS}>{hint}</span> : null}
    </div>
  );
}

export function ToolTestPage({ currentUser, onLogout }: ToolPageProps = {}) {
  const [tool, setTool] = useState<ToolRead | null>(null);
  const [loading, setLoading] = useState(false);
  const navigate = useNavigate();
  const { toolId } = useParams();

  useEffect(() => {
    if (!toolId) return;
    setLoading(true);
    const agentQuery = currentAgentQuery();
    api
      .get<ToolRead>(`/api/enterprise/tools/${toolId}?tenant_id=${TENANT_ID}${agentQuery}`)
      .then(setTool)
      .catch((error) => notify.error(error instanceof Error ? error.message : '加载工具失败'))
      .finally(() => setLoading(false));
  }, [toolId]);

  return (
    <div className="min-h-full box-border px-[48px] pt-[32px] pb-[43px] max-[900px]:px-[16px]" aria-busy={loading}>
      <AppHeader
        onLogout={onLogout}
        userName={currentUser?.username}
        title="工具测试"
        description="用测试参数直接调用已保存工具，检查员工后续调用时的实际返回。"
      />
      <div className="mt-[20px] mb-[16px] flex flex-wrap justify-end gap-[16px]">
        <UIButton variant="outline" onClick={() => navigate('/enterprise/tools')} className={RETURN_BUTTON_CLASS}>
          <IconArrowRight className="size-3.5 rotate-180" />
          返回工具
        </UIButton>
        {tool && (
          <UIButton
            variant="outline"
            onClick={() => navigate(`/enterprise/tools/${tool.id}/edit`)}
            className={RETURN_BUTTON_CLASS}
          >
            <IconEdit className="size-3.5" />
            编辑工具
          </UIButton>
        )}
      </div>
      <div className="grid grid-cols-1 items-start gap-[20px] xl:grid-cols-[minmax(0,1fr)_minmax(360px,440px)]">
        <SectionCard title="工具信息" loading={loading && !tool} bodyClassName="flex flex-col gap-[16px]">
          {tool && (
            <>
              <div className="grid grid-cols-[58px_minmax(0,1fr)] items-start gap-[16px] rounded-[14px] border border-[#eceef1] bg-[#fafbfc] p-[16px]">
                <div className="grid size-[58px] place-items-center rounded-[16px] border border-[#eceef1] bg-white text-[24px] text-[#18181a]">
                  <ToolOutlined />
                </div>
                <div className="min-w-0">
                  <span className="text-[12px] font-semibold text-[#1a71ff]">{tool.bucket || '未分桶'}</span>
                  <h4 className="my-[4px] text-[18px] font-semibold wrap-break-word text-[#18181a]">
                    {tool.display_name || tool.name}
                  </h4>
                  <p className="mb-[10px] text-[13px] leading-[1.65] wrap-break-word text-[#858b9c]">
                    {tool.description || '暂无描述'}
                  </p>
                  <div className="flex flex-wrap items-center gap-[6px]">
                    <StatusBadge tone={tool.tool_type === 'mcp' ? 'blue' : 'gray'}>{toolTypeLabel(tool)}</StatusBadge>
                    <StatusBadge tone={tool.enabled ? 'green' : 'gray'}>{tool.enabled ? '已启用' : '已停用'}</StatusBadge>
                    <StatusBadge tone="gray">{tool.method}</StatusBadge>
                  </div>
                </div>
              </div>

              <div className="grid grid-cols-2 gap-[10px] md:grid-cols-4">
                {[
                  { label: '工具 ID', value: tool.name },
                  { label: '输入字段', value: schemaPropertyCount(tool.input_schema) },
                  { label: '输出字段', value: schemaPropertyCount(tool.output_schema) },
                  { label: '最近更新', value: formatDateTime(tool.updated_at) },
                ].map((item) => (
                  <div
                    key={item.label}
                    className="flex min-h-[78px] flex-col gap-[8px] rounded-[12px] border border-[#eceef1] bg-white px-[14px] py-[13px]"
                  >
                    <span className="text-[12px] font-semibold text-[#858b9c]">{item.label}</span>
                    <strong className="text-[14px] leading-[1.35] wrap-break-word text-[#18181a]">
                      {item.value}
                    </strong>
                  </div>
                ))}
              </div>

              <div className="flex flex-col gap-[8px] rounded-[12px] border border-[#eceef1] bg-[#fafbfc] px-[16px] py-[14px]">
                <span className="text-[12px] font-semibold text-[#858b9c]">调用地址</span>
                <code className="block font-mono text-[13px] leading-[1.6] wrap-break-word text-[#18181a]">
                  {tool.method} {tool.url}
                </code>
              </div>

              <div className="grid grid-cols-1 gap-[12px] md:grid-cols-2">
                <div className="flex flex-col gap-[10px]">
                  <span className={SUBSECTION_TITLE_CLASS}>Input Schema</span>
                  <CodeBlock className="max-h-[340px] whitespace-pre-wrap wrap-break-word" code={formatJson(tool.input_schema)} language="json" />
                </div>
                <div className="flex flex-col gap-[10px]">
                  <span className={SUBSECTION_TITLE_CLASS}>Output Schema</span>
                  <CodeBlock className="max-h-[340px] whitespace-pre-wrap wrap-break-word" code={formatJson(tool.output_schema)} language="json" />
                </div>
              </div>
            </>
          )}
        </SectionCard>
        {tool && <SavedToolTestCard tool={tool} standalone />}
      </div>
    </div>
  );
}

type McpFormValues = {
  name: string;
  display_name: string;
  description: string;
  bucket: string;
  transport: MCPTransport;
  url: string;
  headers: string;
  command: string;
  args: string;
  env: string;
  cwd: string;
  enabled: boolean;
};

const MCP_FORM_INITIAL_VALUES: McpFormValues = {
  name: '',
  display_name: '',
  description: '',
  bucket: 'MCP 工具',
  transport: 'streamable_http',
  url: '',
  headers: '{}',
  command: '',
  args: '',
  env: '{}',
  cwd: '',
  enabled: true,
};

type DiscoveredRow = MCPDiscoverResponse['tools'][number] & { selected: boolean };
type ToolInventoryFilter = 'all' | 'unadded' | 'added';

function McpServerEditorPage({ mode, currentUser, onLogout }: { mode: 'new' | 'edit' } & ToolPageProps) {
  const [values, setValues] = useState<McpFormValues>({ ...MCP_FORM_INITIAL_VALUES });
  const [server, setServer] = useState<MCPServerRead | null>(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [discovering, setDiscovering] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [discovered, setDiscovered] = useState<DiscoveredRow[]>([]);
  const [inventory, setInventory] = useState<MCPToolInventoryRead | null>(null);
  const [toolSearch, setToolSearch] = useState('');
  const [toolFilter, setToolFilter] = useState<ToolInventoryFilter>('all');
  const [discoveryError, setDiscoveryError] = useState('');
  const navigate = useNavigate();
  const { serverId } = useParams();
  const isEdit = mode === 'edit';

  const setField = <K extends keyof McpFormValues>(name: K, value: McpFormValues[K]) =>
    setValues((prev) => ({ ...prev, [name]: value }));

  useEffect(() => {
    if (!isEdit) {
      setValues({ ...MCP_FORM_INITIAL_VALUES });
      setServer(null);
      setDiscovered([]);
      setInventory(null);
      setDiscoveryError('');
      return;
    }
    if (!serverId) return;
    let active = true;
    const loadEditor = async () => {
      setLoading(true);
      try {
        const row = await api.get<MCPServerRead>(
          `/api/enterprise/mcp-servers/${serverId}?tenant_id=${TENANT_ID}`,
        );
        if (!active) return;
        setServer(row);
        setValues(serverToFormValues(row));
        const cached = await fetchInventory(row.id);
        if (!active) return;
        applyInventory(cached);
        setLoading(false);
        if (!cached.cache_available) {
          void discoverConnection(row, row.connection, false);
        }
      } catch (error) {
        if (active) {
          notify.error(error instanceof Error ? error.message : '加载 MCP 服务器失败');
        }
      } finally {
        if (active) setLoading(false);
      }
    };
    void loadEditor();
    return () => {
      active = false;
    };
    // The editor is reloaded only when its route identity changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isEdit, serverId]);

  const transportOption = TRANSPORT_OPTIONS.find((item) => item.value === values.transport);
  const isRemote = values.transport === 'streamable_http' || values.transport === 'sse';
  const isStdio = values.transport === 'stdio';

  function buildConnection(): MCPServerConnection | null {
    let headers: Record<string, string>;
    let env: Record<string, string>;
    try {
      headers = parseJson<Record<string, string>>(values.headers, {});
      env = parseJson<Record<string, string>>(values.env, {});
    } catch {
      notify.error('Headers 或 Env 不是合法 JSON');
      return null;
    }
    const args = parseArgs(values.args);
    if (isStdio) {
      return {
        transport: values.transport,
        url: null,
        headers,
        command: String(values.command || '').trim() || null,
        args,
        env,
        cwd: String(values.cwd || '').trim() || null,
      };
    }
    return {
      transport: values.transport,
      url: String(values.url || '').trim() || null,
      headers,
      command: null,
      args,
      env,
      cwd: null,
    };
  }

  function buildPayload(): { payload: Record<string, unknown>; connection: MCPServerConnection } | null {
    const connection = buildConnection();
    if (!connection) return null;
    return {
      connection,
      payload: {
        tenant_id: TENANT_ID,
        name: String(values.name || '').trim(),
        display_name: values.display_name,
        description: values.description,
        bucket: values.bucket || 'MCP 工具',
        connection,
        enabled: values.enabled,
      },
    };
  }

  async function save() {
    if (!String(values.name || '').trim()) {
      notify.error('请填写 MCP 服务器名称');
      return;
    }
    const built = buildPayload();
    if (!built) return;
    setSaving(true);
    try {
      const saved = isEdit && serverId
        ? await api.put<MCPServerRead>(`/api/enterprise/mcp-servers/${serverId}`, built.payload)
        : await api.post<MCPServerRead>('/api/enterprise/mcp-servers', built.payload);
      notify.success('已保存');
      setServer(saved);
      setValues(serverToFormValues(saved));
      if (!isEdit) {
        navigate(`/enterprise/tools/mcp/${saved.id}/edit`, { replace: true });
      }
    } catch (error) {
      notify.error(error instanceof Error ? error.message : '保存失败');
    } finally {
      setSaving(false);
    }
  }

  async function fetchInventory(targetServerId: string): Promise<MCPToolInventoryRead> {
    return api.get<MCPToolInventoryRead>(
      `/api/enterprise/mcp-servers/${targetServerId}/tool-inventory?tenant_id=${TENANT_ID}${currentAgentQuery()}`,
    );
  }

  function applyInventory(next: MCPToolInventoryRead) {
    setInventory(next);
    setDiscovered(next.tools.map((tool) => ({ ...tool, selected: false })));
  }

  async function discoverConnection(
    targetServer: MCPServerRead | null,
    connection: MCPServerConnection,
    hadCache: boolean,
  ) {
    setDiscovering(true);
    setDiscoveryError('');
    try {
      const response = targetServer
        ? await api.post<MCPDiscoverResponse>(`/api/enterprise/mcp-servers/${targetServer.id}/discover`, {
            tenant_id: TENANT_ID,
            connection,
          })
        : await api.post<MCPDiscoverResponse>('/api/enterprise/mcp-servers/discover', {
            tenant_id: TENANT_ID,
            connection,
          });
      if (!response.success) {
        const message = response.error?.message || '发现工具失败';
        setDiscoveryError(hadCache ? `刷新失败，仍显示上次结果：${message}` : message);
        notify.error(message);
        return;
      }
      if (targetServer) {
        applyInventory(await fetchInventory(targetServer.id));
      } else {
        setDiscovered(response.tools.map((tool) => ({ ...tool, selected: false })));
      }
      notify.success(`发现 ${response.tools.length} 个工具`);
    } catch (error) {
      const message = error instanceof Error ? error.message : '发现工具失败';
      setDiscoveryError(hadCache ? `刷新失败，仍显示上次结果：${message}` : message);
      notify.error(message);
    } finally {
      setDiscovering(false);
    }
  }

  async function discover() {
    // 发现只调用远端 tools/list 并展示结果，不会让员工立刻获得这些工具。
    // 未保存时用于验证草稿连接；已保存时后端还会标记哪些工具已经导入。
    const built = buildPayload();
    if (!built) return;
    await discoverConnection(server, built.connection, Boolean(inventory?.cache_available));
  }

  async function syncTools(toolNames: string[] | null) {
    // 同步会把选中的远端定义持久化为本地 Tool，并按当前 agent 范围建立绑定；
    // 后续模型看到和调用的是这些 Tool 行，而不是直接读取 MCP Server 配置。
    if (!server) {
      notify.warning('请先保存 MCP 服务器，再同步工具');
      return;
    }
    if (Array.isArray(toolNames) && toolNames.length === 0) {
      notify.warning('请至少选择一个要导入的工具');
      return;
    }
    setSyncing(true);
    try {
      const agentQuery = currentAgentQuery();
      const response = await api.post<MCPSyncResponse>(
        `/api/enterprise/mcp-servers/${server.id}/sync${agentQuery ? `?${agentQuery.slice(1)}` : ''}`,
        {
          tenant_id: TENANT_ID,
          tool_names: toolNames,
        },
      );
      if (!response.success) {
        notify.error(response.error?.message || '同步失败');
        return;
      }
      notify.success(`同步完成：新增 ${response.imported.length}，更新 ${response.updated.length}`);
      try {
        const [refreshed, refreshedInventory] = await Promise.all([
          api.get<MCPServerRead>(`/api/enterprise/mcp-servers/${server.id}?tenant_id=${TENANT_ID}`),
          fetchInventory(server.id),
        ]);
        setServer(refreshed);
        applyInventory(refreshedInventory);
        setDiscoveryError('');
      } catch {
        // ignore refresh failure
      }
    } catch (error) {
      notify.error(error instanceof Error ? error.message : '同步失败');
    } finally {
      setSyncing(false);
    }
  }

  const searchKey = toolSearch.trim().toLowerCase();
  const filteredDiscovered = discovered.filter((tool) => {
    if (toolFilter === 'added' && !tool.in_current_scope) return false;
    if (toolFilter === 'unadded' && tool.in_current_scope) return false;
    if (!searchKey) return true;
    return `${tool.name}\n${tool.description}`.toLowerCase().includes(searchKey);
  });
  const selectedCount = discovered.filter((tool) => tool.selected && !tool.in_current_scope).length;
  const availableCount = inventory?.available_count ?? (discovered.length ? discovered.length : null);
  const scopeDisplayCount = inventory?.current_scope_is_overall
    ? inventory.imported_count
    : inventory?.current_scope_count || 0;
  const scopeCountLabel = inventory?.current_scope_is_overall ? '系统已导入' : '当前员工已添加';
  const allAddedToCurrentScope = Boolean(
    inventory
      && inventory.available_count != null
      && inventory.available_count > 0
      && discovered.every((tool) => tool.in_current_scope),
  );

  function setToolSelected(name: string, selected: boolean) {
    setDiscovered((prev) =>
      prev.map((tool) =>
        tool.name === name && !tool.in_current_scope ? { ...tool, selected } : tool,
      ),
    );
  }

  function selectCurrentResults() {
    const names = new Set(filteredDiscovered.filter((tool) => !tool.in_current_scope).map((tool) => tool.name));
    setDiscovered((prev) => prev.map((tool) => (names.has(tool.name) ? { ...tool, selected: true } : tool)));
  }

  function clearToolSelection() {
    setDiscovered((prev) => prev.map((tool) => ({ ...tool, selected: false })));
  }

  function syncSelected() {
    const names = discovered
      .filter((tool) => tool.selected && !tool.in_current_scope)
      .map((tool) => tool.name);
    return syncTools(names);
  }

  return (
    <div className="min-h-full box-border px-[48px] pt-[32px] pb-[43px] max-[900px]:px-[16px]" aria-busy={loading}>
      <AppHeader
        onLogout={onLogout}
        userName={currentUser?.username}
        title={isEdit ? '编辑 MCP 服务器' : '新建工具'}
        description="配置 MCP Server 连接后，可发现其提供的工具并同步为工具集。"
      />
      <div className="mt-[20px] mb-[16px] flex flex-wrap justify-end gap-[16px]">
        <UIButton variant="outline" onClick={() => navigate('/enterprise/tools')} className={RETURN_BUTTON_CLASS}>
          <IconArrowRight className="size-3.5 rotate-180" />
          返回工具
        </UIButton>
        <UIButton disabled={saving} onClick={() => void save()} className={PRIMARY_BUTTON_CLASS}>
          保存
        </UIButton>
      </div>
      {!isEdit && <ToolTypeSwitcher active="mcp" />}
      <div className="grid grid-cols-1 items-start gap-[20px] xl:grid-cols-2">
        <SectionCard title="连接配置" loading={loading && isEdit && !server}>
          <div className="flex flex-col gap-[16px]">
            <div className="grid grid-cols-1 gap-[16px] sm:grid-cols-2">
              <Field
                label="名称"
                htmlFor="mcp-name"
                hint={isEdit ? '保存后不可修改名称。' : '作为唯一标识，仅支持字母/数字/下划线；中文将自动转拼音，最长 15 字符。'}
              >
                <Input
                  id="mcp-name"
                  placeholder="my_mcp_server"
                  disabled={isEdit}
                  value={values.name}
                  onChange={(event) => setField('name', sanitizeMcpName(event.target.value))}
                />
              </Field>
              <Field label="展示名称" htmlFor="mcp-display-name">
                <Input
                  id="mcp-display-name"
                  placeholder="我的工具集"
                  value={values.display_name}
                  onChange={(event) => setField('display_name', event.target.value)}
                />
              </Field>
            </div>

            <Field label="描述" htmlFor="mcp-description">
              <Textarea
                id="mcp-description"
                rows={2}
                placeholder="简单说明这个工具集的用途"
                value={values.description}
                onChange={(event) => setField('description', event.target.value)}
              />
            </Field>

            <Field label="分桶" htmlFor="mcp-bucket">
              <Input
                id="mcp-bucket"
                placeholder="MCP 工具"
                value={values.bucket}
                onChange={(event) => setField('bucket', event.target.value)}
              />
            </Field>

            <Field label="连接方式" hint={transportOption?.hint}>
              <UISelect
                value={values.transport}
                onValueChange={(value) => setField('transport', value as MCPTransport)}
              >
                <SelectTrigger className={cn(SELECT_TRIGGER_CLASS, 'w-full')}>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {TRANSPORT_OPTIONS.map((item) => (
                    <SelectItem key={item.value} value={item.value}>
                      {item.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </UISelect>
            </Field>

            {isRemote && (
              <>
                <Field label="URL" htmlFor="mcp-url">
                  <Input
                    id="mcp-url"
                    placeholder="https://example.com/mcp"
                    value={values.url}
                    onChange={(event) => setField('url', event.target.value)}
                  />
                </Field>
                <Field label="Headers JSON" htmlFor="mcp-headers">
                  <Textarea
                    id="mcp-headers"
                    rows={4}
                    className={MONO_INPUT_CLASS}
                    value={values.headers}
                    onChange={(event) => setField('headers', event.target.value)}
                  />
                </Field>
              </>
            )}

            {isStdio && (
              <>
                <Field label="Command" htmlFor="mcp-command">
                  <Input
                    id="mcp-command"
                    placeholder="python"
                    value={values.command}
                    onChange={(event) => setField('command', event.target.value)}
                  />
                </Field>
                <Field label="Args" htmlFor="mcp-args" hint="每行一个参数。">
                  <Textarea
                    id="mcp-args"
                    rows={4}
                    className={MONO_INPUT_CLASS}
                    placeholder={'-m\nmy_mcp.server\n--port\n8000'}
                    value={values.args}
                    onChange={(event) => setField('args', event.target.value)}
                  />
                </Field>
                <Field label="Env JSON" htmlFor="mcp-env">
                  <Textarea
                    id="mcp-env"
                    rows={4}
                    className={MONO_INPUT_CLASS}
                    value={values.env}
                    onChange={(event) => setField('env', event.target.value)}
                  />
                </Field>
                <Field label="工作目录（cwd）" htmlFor="mcp-cwd">
                  <Input
                    id="mcp-cwd"
                    placeholder="/path/to/workdir"
                    value={values.cwd}
                    onChange={(event) => setField('cwd', event.target.value)}
                  />
                </Field>
              </>
            )}

            <div className="flex items-center justify-between rounded-[12px] border border-[#eceef1] bg-[#fafbfc] px-[14px] py-[12px]">
              <div className="flex flex-col gap-[2px]">
                <span className={FIELD_LABEL_CLASS}>启用工具集</span>
                <span className={HINT_CLASS}>停用后其下工具将无法被员工调用。</span>
              </div>
              <Switch checked={values.enabled} onCheckedChange={(next) => setField('enabled', next)} />
            </div>
          </div>
        </SectionCard>

        <SectionCard
          title="MCP 工具集"
          bodyClassName="flex flex-col gap-[16px]"
          extra={(
            <UIButton
              variant="outline"
              disabled={discovering}
              onClick={() => void discover()}
              className={RETURN_BUTTON_CLASS}
            >
              <IconRefresh className={cn('size-[14px] shrink-0', discovering && 'animate-spin')} />
              刷新工具
            </UIButton>
          )}
        >
          <div className="flex flex-col gap-[14px] border-y border-[#eceef1] bg-[#fafbfc] px-[2px] py-[16px] sm:flex-row sm:items-center sm:justify-between">
            <div className="min-w-0 flex-1 px-[12px]">
              <strong className="block truncate text-[14px] font-medium text-[#18181a]">
                {values.display_name || values.name || '未命名 MCP 工具集'}
              </strong>
              <p className="mt-[4px] line-clamp-2 wrap-break-word text-[12px] leading-[1.6] text-[#858b9c]">
                {values.description || '暂无工具集描述'}
              </p>
              <div className="mt-[10px] flex flex-wrap gap-[6px]">
                <StatusBadge tone={availableCount == null ? 'gray' : 'blue'}>
                  {availableCount == null ? '尚未发现' : `已发现 ${availableCount} 个`}
                </StatusBadge>
                <StatusBadge tone={scopeDisplayCount > 0 ? 'green' : 'gray'}>
                  {scopeCountLabel} {scopeDisplayCount} 个
                </StatusBadge>
              </div>
            </div>
            <div className="flex shrink-0 items-center px-[12px]">
              <UIButton
                disabled={!server || syncing || discovering || !availableCount || allAddedToCurrentScope}
                onClick={() => void syncTools(null)}
                className={PRIMARY_BUTTON_CLASS}
              >
                {allAddedToCurrentScope ? '已全部添加' : '导入全部'}
              </UIButton>
            </div>
          </div>

          {discoveryError && (
            <div role="alert" className="border-l-2 border-[#e5484d] bg-[#fff7f7] px-[12px] py-[9px] text-[12px] leading-[1.55] text-[#b4232a]">
              {discoveryError}
            </div>
          )}

          {discovering && !discovered.length && (
            <div className="py-[24px] text-center text-[13px] text-[#858b9c]">正在发现工具…</div>
          )}

          <Accordion type="single" collapsible>
            <AccordionItem value="custom-tools" className="border-y border-[#eceef1]">
              <AccordionTrigger className="px-[12px] hover:no-underline">
                <span className="flex min-w-0 flex-col gap-[2px]">
                  <span>自定义选择</span>
                  <span className="text-[11px] font-normal text-[#858b9c]">
                    按名称、用途或添加状态筛选工具
                  </span>
                </span>
              </AccordionTrigger>
              <AccordionContent className="px-[12px] pb-[12px]">
                <div className="flex flex-col gap-[12px]">
                  <div className="flex flex-col gap-[10px] lg:flex-row lg:items-center lg:justify-between">
                    <div className="relative min-w-0 flex-1">
                      <IconSearch className="pointer-events-none absolute left-[10px] top-1/2 size-[14px] -translate-y-1/2 text-[#858b9c]" />
                      <Input
                        aria-label="搜索 MCP 工具"
                        className="pl-[32px]"
                        placeholder="搜索工具名称或用途"
                        value={toolSearch}
                        onChange={(event) => setToolSearch(event.target.value)}
                      />
                    </div>
                    <Tabs value={toolFilter} onValueChange={(value) => setToolFilter(value as ToolInventoryFilter)}>
                      <TabsList aria-label="工具添加状态" className="w-full lg:w-auto">
                        <TabsTrigger value="all">全部 {discovered.length}</TabsTrigger>
                        <TabsTrigger value="unadded">
                          未添加 {discovered.filter((tool) => !tool.in_current_scope).length}
                        </TabsTrigger>
                        <TabsTrigger value="added">
                          已添加 {discovered.filter((tool) => tool.in_current_scope).length}
                        </TabsTrigger>
                      </TabsList>
                    </Tabs>
                  </div>

                  <div className="flex flex-wrap items-center justify-between gap-[8px]">
                    <span className={HINT_CLASS}>已选择 {selectedCount} 个待添加工具</span>
                    <div className="flex flex-wrap items-center gap-[8px]">
                      <UIButton
                        variant="outline"
                        size="sm"
                        disabled={!filteredDiscovered.some((tool) => !tool.in_current_scope)}
                        onClick={selectCurrentResults}
                        className={RETURN_BUTTON_CLASS}
                      >
                        全选当前结果
                      </UIButton>
                      <UIButton
                        variant="outline"
                        size="sm"
                        disabled={!selectedCount}
                        onClick={clearToolSelection}
                        className={RETURN_BUTTON_CLASS}
                      >
                        清空
                      </UIButton>
                      <UIButton
                        size="sm"
                        disabled={!server || syncing || !selectedCount}
                        onClick={() => void syncSelected()}
                        className={PRIMARY_BUTTON_CLASS}
                      >
                        添加所选（{selectedCount}）
                      </UIButton>
                    </div>
                  </div>

                  {filteredDiscovered.length ? (
                    <div role="list" aria-label="MCP 工具列表" className="border-t border-[#eceef1]">
                      {filteredDiscovered.map((tool) => {
                        const parameterNames = schemaPropertyNames(tool.input_schema);
                        return (
                          <div
                            role="listitem"
                            key={tool.name}
                            className="grid grid-cols-[24px_minmax(0,1fr)] gap-[10px] border-b border-[#eceef1] py-[12px]"
                          >
                            <Checkbox
                              checked={tool.in_current_scope || tool.selected}
                              disabled={tool.in_current_scope}
                              onCheckedChange={(next) => setToolSelected(tool.name, next === true)}
                              aria-label={tool.in_current_scope ? `${tool.name} 已添加` : `选择 ${tool.name}`}
                              className="mt-[2px]"
                            />
                            <div className="min-w-0">
                              <div className="flex min-w-0 flex-wrap items-center gap-[8px]">
                                <strong className="min-w-0 wrap-break-word text-[13px] font-medium text-[#18181a]">
                                  {tool.name}
                                </strong>
                                <StatusBadge tone={tool.in_current_scope ? 'green' : 'gray'}>
                                  {tool.in_current_scope ? '已添加' : '未添加'}
                                </StatusBadge>
                              </div>
                              <p className="mt-[4px] line-clamp-2 wrap-break-word text-[12px] leading-[1.6] text-[#858b9c]">
                                {shortToolDescription(tool.description)}
                              </p>
                              {(tool.description || parameterNames.length > 0) && (
                                <details className="mt-[6px] text-[12px] text-[#667085]">
                                  <summary className="w-fit cursor-pointer text-[#596579] hover:text-[#18181a]">
                                    查看详情
                                  </summary>
                                  <div className="mt-[6px] flex flex-col gap-[5px] border-l-2 border-[#eceef1] pl-[10px] leading-[1.65]">
                                    <p className="wrap-break-word">{tool.description || '暂无描述'}</p>
                                    <p className="wrap-break-word">
                                      输入参数：{parameterNames.length ? parameterNames.join('、') : '无'}
                                    </p>
                                  </div>
                                </details>
                              )}
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  ) : (
                    <div className="border-y border-dashed border-[#eceef1] py-[28px] text-center text-[13px] text-[#858b9c]">
                      {discovered.length ? '没有符合条件的工具' : '尚未发现工具'}
                    </div>
                  )}
                </div>
              </AccordionContent>
            </AccordionItem>
          </Accordion>

          {!server && (
            <p className={HINT_CLASS}>请先保存 MCP 服务器，才能把发现的工具添加到当前范围。</p>
          )}
        </SectionCard>
      </div>
    </div>
  );
}

function ToolFormFields({
  values,
  setField,
  bucketOptions,
  lockName = false,
}: {
  values: ToolFormValues;
  setField: <K extends keyof ToolFormValues>(name: K, value: ToolFormValues[K]) => void;
  bucketOptions: { value: string; label: string }[];
  lockName?: boolean;
}) {
  return (
    <div className="flex flex-col gap-[16px]">
      <div className="grid grid-cols-1 gap-[16px] sm:grid-cols-2">
        <Field label="工具名称" htmlFor="tool-name">
          <div className="relative">
            <ToolOutlined className="pointer-events-none absolute left-[10px] top-1/2 -translate-y-1/2 text-[#858b9c]" />
            <Input
              id="tool-name"
              className="pl-[30px]"
              placeholder="order_query"
              value={values.name || ''}
              disabled={lockName}
              onChange={(event) => {
                if (lockName) return;
                setField('name', event.target.value);
              }}
            />
          </div>
        </Field>
        <Field label="展示名称" htmlFor="tool-display-name">
          <Input
            id="tool-display-name"
            placeholder="订单查询"
            value={values.display_name || ''}
            onChange={(event) => setField('display_name', event.target.value)}
          />
        </Field>
      </div>

      <div className="grid grid-cols-1 gap-[16px] sm:grid-cols-2">
        <Field label="工具分桶" htmlFor="tool-bucket">
          <Input
            id="tool-bucket"
            list="tool-bucket-options"
            placeholder="选择或输入分桶"
            value={values.bucket || ''}
            onChange={(event) => setField('bucket', event.target.value)}
          />
          <datalist id="tool-bucket-options">
            {bucketOptions.map((item) => (
              <option key={item.value} value={item.value} />
            ))}
          </datalist>
        </Field>
      </div>

      <Field label="描述" htmlFor="tool-description">
        <Textarea
          id="tool-description"
          rows={2}
          placeholder="简单说明这个工具的用途"
          value={values.description || ''}
          onChange={(event) => setField('description', event.target.value)}
        />
      </Field>

      <div className="grid grid-cols-1 gap-[16px] sm:grid-cols-[140px_minmax(0,1fr)]">
        <Field label="HTTP Method">
          <UISelect value={values.method} onValueChange={(value) => setField('method', value)}>
            <SelectTrigger className={cn(SELECT_TRIGGER_CLASS, 'w-full')}>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {['GET', 'POST', 'PUT', 'PATCH', 'DELETE'].map((value) => (
                <SelectItem key={value} value={value}>{value}</SelectItem>
              ))}
            </SelectContent>
          </UISelect>
        </Field>
        <Field label="URL" htmlFor="tool-url">
          <Input
            id="tool-url"
            placeholder="/api/mock/order/query"
            value={values.url || ''}
            onChange={(event) => setField('url', event.target.value)}
          />
        </Field>
      </div>

      <div className="grid grid-cols-1 gap-[16px] sm:grid-cols-2">
        <Field label="Headers JSON" htmlFor="tool-headers">
          <Textarea
            id="tool-headers"
            rows={4}
            className={MONO_INPUT_CLASS}
            value={values.headers}
            onChange={(event) => setField('headers', event.target.value)}
          />
        </Field>
        <Field label="Auth JSON" htmlFor="tool-auth">
          <Textarea
            id="tool-auth"
            rows={4}
            className={MONO_INPUT_CLASS}
            value={values.auth}
            onChange={(event) => setField('auth', event.target.value)}
          />
        </Field>
      </div>

      <div className="grid grid-cols-1 gap-[16px] sm:grid-cols-2">
        <Field label="Input Schema" htmlFor="tool-input-schema">
          <Textarea
            id="tool-input-schema"
            rows={5}
            className={MONO_INPUT_CLASS}
            value={values.input_schema}
            onChange={(event) => setField('input_schema', event.target.value)}
          />
        </Field>
        <Field label="Output Schema" htmlFor="tool-output-schema">
          <Textarea
            id="tool-output-schema"
            rows={5}
            className={MONO_INPUT_CLASS}
            value={values.output_schema}
            onChange={(event) => setField('output_schema', event.target.value)}
          />
        </Field>
      </div>

      <Field label="Allowed Skills" htmlFor="tool-allowed-skills" hint="留空表示所有技能可调用，多个技能用英文逗号分隔。">
        <Input
          id="tool-allowed-skills"
          placeholder="skill_id_1,skill_id_2"
          value={values.allowed_skills || ''}
          onChange={(event) => setField('allowed_skills', event.target.value)}
        />
      </Field>

      <div className="flex items-center justify-between rounded-[12px] border border-[#eceef1] bg-[#fafbfc] px-[14px] py-[12px]">
        <div className="flex flex-col gap-[2px]">
          <span className={FIELD_LABEL_CLASS}>启用工具</span>
          <span className={HINT_CLASS}>停用后员工将无法调用该工具。</span>
        </div>
        <Switch checked={values.enabled} onCheckedChange={(next) => setField('enabled', next)} />
      </div>
    </div>
  );
}

function ToolProbeCard({ values }: { values: ToolFormValues }) {
  const [sampleJson, setSampleJson] = useState('{}');
  const [result, setResult] = useState('');
  const [loading, setLoading] = useState(false);
  const method = values.method || 'POST';
  const isGetMethod = method === 'GET';

  async function probe() {
    if (!String(values.name || '').trim()) {
      notify.error('请填写工具名称');
      return;
    }
    if (!String(values.url || '').trim()) {
      notify.error('请填写 URL');
      return;
    }
    const payload = buildToolPayload(values);
    if (!payload) return;
    let sampleArguments: Record<string, unknown>;
    try {
      sampleArguments = parseJson(sampleJson, {});
    } catch {
      notify.error('测试参数不是合法 JSON');
      return;
    }
    if (
      payload.tool_type === 'http'
      && payload.method !== 'GET'
      && payload.url.includes('?')
      && Object.keys(sampleArguments).length === 0
    ) {
      notify.error('URL 已包含查询参数时请把 HTTP Method 切换为 GET；POST 会把测试参数作为 JSON Body 发送。');
      return;
    }
    setLoading(true);
    try {
      const response = await api.post('/api/enterprise/tools/probe', {
        tenant_id: TENANT_ID,
        name: payload.name,
        display_name: payload.display_name,
        description: payload.description,
        bucket: payload.bucket,
        tool_type: payload.tool_type,
        method: payload.method,
        url: payload.url,
        headers: payload.headers,
        auth: payload.auth,
        mcp_config: payload.mcp_config,
        input_schema: payload.input_schema,
        output_schema: payload.output_schema,
        sample_arguments: sampleArguments,
      });
      setResult(JSON.stringify(response, null, 2));
    } catch (error) {
      notify.error(error instanceof Error ? error.message : '探测失败');
    } finally {
      setLoading(false);
    }
  }

  return (
    <SectionCard
      title="配置探测"
      bodyClassName="flex flex-col gap-[14px]"
      extra={(
        <UIButton variant="outline" disabled={loading} onClick={() => void probe()} className={RETURN_BUTTON_CLASS}>
          <ExperimentOutlined />
          探测
        </UIButton>
      )}
    >
      <p className={HINT_CLASS}>无需保存，直接用当前配置测试连接。</p>
      <div className="flex flex-col gap-[8px]">
        <span className={SUBSECTION_TITLE_CLASS}>
          {isGetMethod ? '测试参数 JSON（拼到 URL Query）' : '测试参数 JSON（作为请求 Body）'}
        </span>
        <p className={HINT_CLASS}>
          {isGetMethod
            ? 'GET 会把这里的字段作为查询参数追加到 URL；参数值填写未编码原文，例如 timezone 用 Asia/Shanghai。'
            : '非 GET 请求会把这里的 JSON 作为请求体发送；仅 URL 查询串不会变成请求 Body。'}
        </p>
        <Textarea
          rows={5}
          className={MONO_INPUT_CLASS}
          value={sampleJson}
          onChange={(event) => setSampleJson(event.target.value)}
        />
      </div>
      <div className="flex flex-col gap-[8px]">
        <span className={SUBSECTION_TITLE_CLASS}>探测结果</span>
        <Textarea rows={8} readOnly className={MONO_INPUT_CLASS} value={result} />
      </div>
    </SectionCard>
  );
}

function SavedToolTestCard({ tool, standalone = false }: { tool: ToolRead; standalone?: boolean }) {
  const [testJson, setTestJson] = useState(() => JSON.stringify(exampleFromSchema(tool.input_schema), null, 2));
  const [testResult, setTestResult] = useState('');
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    setTestJson(JSON.stringify(exampleFromSchema(tool.input_schema), null, 2));
    setTestResult('');
  }, [tool.id, tool.input_schema]);

  async function test() {
    let argumentsJson: Record<string, unknown>;
    try {
      argumentsJson = parseJson(testJson, {});
    } catch {
      notify.error('测试参数不是合法 JSON');
      return;
    }
    setLoading(true);
    try {
      const agentQuery = currentAgentQuery();
      const response = await api.post(`/api/enterprise/tools/${tool.id}/test${agentQuery ? `?${agentQuery.slice(1)}` : ''}`, {
        tenant_id: TENANT_ID,
        arguments: argumentsJson,
      });
      setTestResult(JSON.stringify(response, null, 2));
    } catch (error) {
      notify.error(error instanceof Error ? error.message : '调用失败');
    } finally {
      setLoading(false);
    }
  }

  return (
    <SectionCard
      className={standalone ? undefined : 'xl:sticky xl:top-[18px]'}
      bodyClassName="flex flex-col gap-[16px]"
      title={(
        <span className="inline-flex items-center gap-[8px]">
          <ExperimentOutlined />
          {standalone ? '调用测试' : '已保存工具测试'}
        </span>
      )}
      extra={(
        <UIButton disabled={loading} onClick={() => void test()} className={PRIMARY_BUTTON_CLASS}>
          <ExperimentOutlined />
          调用
        </UIButton>
      )}
    >
      <div className="flex items-start justify-between gap-[12px] rounded-[12px] border border-[#eceef1] bg-[#fafbfc] px-[14px] py-[12px]">
        <span className="min-w-0 flex-1 wrap-break-word text-[13px] leading-[1.65] text-[#858b9c]">
          调用已保存的「{tool.display_name || tool.name}」，用于验证员工实际可用的工具返回。
        </span>
        <span className="shrink-0">
          <StatusBadge tone="gray">{toolTypeLabel(tool)}</StatusBadge>
        </span>
      </div>
      <div className="flex flex-col gap-[10px]">
        <span className={SUBSECTION_TITLE_CLASS}>测试参数</span>
        <Textarea
          rows={8}
          className={MONO_INPUT_CLASS}
          value={testJson}
          onChange={(event) => setTestJson(event.target.value)}
        />
      </div>
      <div className="flex flex-col gap-[10px]">
        <div className="flex items-center justify-between gap-[10px]">
          <span className={SUBSECTION_TITLE_CLASS}>调用结果</span>
          <StatusBadge tone={testResult ? 'green' : 'gray'}>{testResult ? '已返回' : '等待调用'}</StatusBadge>
        </div>
        {testResult ? (
          <CodeBlock className="max-h-[340px] whitespace-pre-wrap wrap-break-word" code={testResult} language="json" />
        ) : (
          <div className="grid min-h-[180px] place-items-center rounded-[12px] border border-dashed border-[#eceef1] p-[20px] text-center text-[13px] text-[#858b9c]">
            点击调用后，这里会显示工具返回、错误信息和原始 data。
          </div>
        )}
      </div>
    </SectionCard>
  );
}

async function loadBucketOptions() {
  const rows = await api.get<ToolRead[]>(`/api/enterprise/tools?tenant_id=${TENANT_ID}${currentAgentQuery()}`);
  return Array.from(new Set(['未分桶', ...rows.map((row) => row.bucket || '未分桶')]))
    .map((value) => ({ value, label: value }));
}

function currentAgentQuery() {
  const agentId = window.localStorage.getItem(ENTERPRISE_AGENT_STORAGE_KEY) || '';
  return agentId ? `&agent_id=${encodeURIComponent(agentId)}` : '';
}

function toolToFormValues(row: ToolRead): ToolFormValues {
  return {
    ...TOOL_FORM_INITIAL_VALUES,
    ...row,
    bucket: row.bucket || '未分桶',
    tool_type: row.tool_type || 'http',
    headers: JSON.stringify(row.headers || {}, null, 2),
    auth: JSON.stringify(row.auth || {}, null, 2),
    mcp_config: JSON.stringify(row.mcp_config || {}, null, 2),
    input_schema: JSON.stringify(row.input_schema || {}, null, 2),
    output_schema: JSON.stringify(row.output_schema || {}, null, 2),
    allowed_skills: (row.allowed_skills || []).join(','),
  };
}

function buildToolPayload(values: ToolFormValues) {
  try {
    return {
      tenant_id: TENANT_ID,
      name: String(values.name || '').trim(),
      display_name: values.display_name,
      description: values.description,
      bucket: values.bucket || '未分桶',
      tool_type: values.tool_type || 'http',
      method: values.method,
      url: String(values.url || '').trim(),
      headers: parseJson(values.headers, {}),
      auth: parseJson(values.auth, {}),
      mcp_config: values.tool_type === 'mcp' ? parseJson(values.mcp_config, {}) : {},
      input_schema: parseJson(values.input_schema, {}),
      output_schema: parseJson(values.output_schema, {}),
      allowed_skills: String(values.allowed_skills || '').split(',').map((item) => item.trim()).filter(Boolean),
      enabled: values.enabled,
    };
  } catch {
    notify.error('JSON 配置格式不正确，请检查 Headers、Auth、Schema 或 MCP Config');
    return null;
  }
}

function buildBucketStats(rows: ToolRead[]) {
  const map = new Map<string, { bucket: string; total: number; enabled: number; disabled: number }>();
  rows.forEach((row) => {
    const bucket = row.bucket || '未分桶';
    const item = map.get(bucket) || { bucket, total: 0, enabled: 0, disabled: 0 };
    item.total += 1;
    if (row.enabled) item.enabled += 1;
    else item.disabled += 1;
    map.set(bucket, item);
  });
  return Array.from(map.values()).sort((a, b) => b.total - a.total || a.bucket.localeCompare(b.bucket));
}

function matchesSearch(values: Array<string | undefined | null>, searchKey: string): boolean {
  return values.some((value) => String(value || '').toLowerCase().includes(searchKey));
}

function toolMatchesSearch(tool: ToolRead, searchKey: string): boolean {
  return matchesSearch([
    tool.name,
    tool.display_name,
    tool.description,
    tool.bucket,
    tool.method,
    tool.url,
    resourceCreatorName(tool),
  ], searchKey);
}

function serverMatchesSearch(server: MCPServerRead, searchKey: string): boolean {
  return matchesSearch([
    server.name,
    server.display_name,
    server.description,
    server.bucket,
    serverEndpoint(server.connection),
    transportLabel(server.connection.transport),
  ], searchKey);
}

function parseJson<T>(value: string, fallback: T): T {
  if (!value) return fallback;
  return JSON.parse(value) as T;
}

function formatJson(value: unknown): string {
  return JSON.stringify(value || {}, null, 2);
}

function schemaPropertyCount(schema: Record<string, unknown>): string {
  const properties = schema.properties && typeof schema.properties === 'object'
    ? schema.properties as Record<string, unknown>
    : {};
  return `${Object.keys(properties).length}`;
}

function schemaPropertyNames(schema: Record<string, unknown>): string[] {
  const properties = schema.properties && typeof schema.properties === 'object'
    ? schema.properties as Record<string, unknown>
    : {};
  return Object.keys(properties);
}

function shortToolDescription(description: string): string {
  const normalized = String(description || '').replace(/\s+/g, ' ').trim();
  if (!normalized) return '暂无描述';
  const sentence = normalized.split(/(?<=[。！？.!?])/u, 1)[0] || normalized;
  return sentence.length > 96 ? `${sentence.slice(0, 96)}…` : sentence;
}

function toolTypeLabel(tool: ToolRead): string {
  return tool.tool_type === 'mcp' ? 'MCP 服务' : 'HTTP 接口';
}

function serverToFormValues(row: MCPServerRead): McpFormValues {
  const connection = row.connection;
  return {
    name: row.name,
    display_name: row.display_name || '',
    description: row.description || '',
    bucket: row.bucket || 'MCP 工具',
    transport: connection.transport,
    url: connection.url || '',
    headers: JSON.stringify(connection.headers || {}, null, 2),
    command: connection.command || '',
    args: (connection.args || []).join('\n'),
    env: JSON.stringify(connection.env || {}, null, 2),
    cwd: connection.cwd || '',
    enabled: row.enabled,
  };
}

function parseArgs(value: string): string[] {
  const text = String(value || '');
  const parts = text.includes('\n') ? text.split('\n') : text.split(/\s+/);
  return parts.map((item) => item.trim()).filter(Boolean);
}

function transportLabel(transport: MCPTransport | string): string {
  return TRANSPORT_OPTIONS.find((item) => item.value === transport)?.label || String(transport);
}

/**
 * 规范化 MCP 服务器名称（唯一标识）：
 * 中文自动转拼音（无声调），只保留字母/数字/下划线，其余转下划线，最长 15 字符。
 */
function sanitizeMcpName(raw: string): string {
  const input = String(raw || '');
  // 含中文时先整体转拼音（不带声调），拼音之间用下划线连接。
  const converted = /[\u4e00-\u9fa5]/.test(input)
    ? pinyin(input, { toneType: 'none', type: 'array', nonZh: 'consecutive' }).join('_')
    : input;
  const normalized = converted
    .replace(/[^a-zA-Z0-9_]+/g, '_')
    .replace(/_+/g, '_')
    .replace(/^_+/, '');
  return normalized.slice(0, 15);
}

function serverEndpoint(connection: MCPServerConnection): string {
  if (connection.transport === 'stdio') return connection.command || '—';
  if (connection.transport === 'builtin') return 'builtin.demo';
  return connection.url || '—';
}

function exampleFromSchema(schema: Record<string, unknown>): Record<string, unknown> {
  const properties = schema.properties && typeof schema.properties === 'object'
    ? schema.properties as Record<string, Record<string, unknown>>
    : {};
  return Object.fromEntries(
    Object.entries(properties).map(([key, value]) => [key, exampleValue(key, value)]),
  );
}

function exampleValue(key: string, schema: Record<string, unknown>): unknown {
  if (schema.default !== undefined) return schema.default;
  if (schema.example !== undefined) return schema.example;
  if (Array.isArray(schema.enum) && schema.enum.length > 0) return schema.enum[0];
  if (schema.type === 'integer') return 1;
  if (schema.type === 'number') return 1;
  if (schema.type === 'boolean') return true;
  if (schema.type === 'array') return [];
  if (schema.type === 'object') return {};
  return `sample_${key}`;
}
