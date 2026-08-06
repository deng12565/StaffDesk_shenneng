import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import {
  ArrowLeft,
  CheckCircle2,
  FileText,
  KeyRound,
  Mail,
  Play,
  RefreshCw,
  RotateCcw,
  Save,
  ShieldCheck,
} from 'lucide-react';

import AppHeader from '@/components/AppHeader';
import { Button as UIButton } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogTitle,
  Input,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  Switch,
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from '@/components/ui';
import { notify } from '@/components/ui/app-toast';
import { api, TENANT_ID } from '../api/client';
import type { EnterpriseAuthUser } from '../auth';
import type { AgentProfileRead, ChannelBindingRead, ModelConfigRead } from '../types';

type Inbox = {
  id: string;
  email_address: string;
  imap_host: string;
  imap_port: number;
  mailbox_name: string;
  status: string;
  has_credentials: boolean;
  last_tested_at?: string;
  last_error_code?: string;
};

type DigestConfig = {
  id: string;
  agent_id: string;
  inbox_binding_id: string;
  model_config_id: string;
  feishu_binding_id: string;
  recipient_open_id: string;
  timezone: string;
  snapshot_time: string;
  earliest_delivery_time: string;
  misfire_deadline_time: string;
  raw_retention_days: number;
  result_retention_days: number;
  model_privacy_verified: boolean;
  status: 'active' | 'disabled';
  scheduled_task_id?: string;
};

type DigestBatch = {
  id: string;
  status: string;
  snapshot_at: string;
  new_email_count: number;
  scored_count: number;
  review_count: number;
  failed_count: number;
  error_code?: string;
  report?: DigestReport;
};

type RankedCandidate = {
  application_id: string;
  candidate_display_name?: string;
  applied_job: string;
  batch_rank: number;
  recommendation_index: number;
  dimension_scores: Record<string, number>;
  match_evidence: Record<string, string[]>;
  recommendation_reasons: string[];
  risks: string[];
  role_profile_id: string;
  role_profile_version: number;
};

type DigestReport = {
  batch_id: string;
  report_date: string;
  window: string;
  new_email_count: number;
  scored_count: number;
  review_count: number;
  ranked_candidates: RankedCandidate[];
  needs_review: Array<{
    application_id: string;
    candidate_display_name?: string;
    error_code?: string;
  }>;
  disclaimer: string;
};

type RoleProfile = {
  id: string;
  display_name: string;
  standard_role_key: string;
  explicit_level: string;
  specialization: string;
  version: number;
  warnings: string[];
  aliases: Array<{ raw_title: string; confidence: number }>;
  core_capabilities: Array<{ id: string; name: string; is_critical: boolean }>;
};

type Capabilities = {
  word: { available: boolean; version?: string; error_code?: string };
  seven_zip: { available: boolean; version?: string; formats: string[]; error_code?: string };
};

type ConfigForm = {
  agent_id: string;
  inbox_binding_id: string;
  model_config_id: string;
  feishu_binding_id: string;
  recipient_open_id: string;
  timezone: string;
  snapshot_time: string;
  earliest_delivery_time: string;
  misfire_deadline_time: string;
  raw_retention_days: number;
  result_retention_days: number;
  status: 'active' | 'disabled';
};

const EMPTY_FORM: ConfigForm = {
  agent_id: '',
  inbox_binding_id: '',
  model_config_id: '',
  feishu_binding_id: '',
  recipient_open_id: '',
  timezone: 'Asia/Shanghai',
  snapshot_time: '07:00',
  earliest_delivery_time: '08:00',
  misfire_deadline_time: '10:00',
  raw_retention_days: 7,
  result_retention_days: 90,
  status: 'disabled',
};

const STATUS_LABELS: Record<string, string> = {
  active: '运行中',
  disabled: '已停用',
  pending: '待配置',
  delivered: '已送达',
  waiting_delivery: '待投递',
  no_new: '无新增',
  partial_success: '部分成功',
  failed: '失败',
  missed: '已错过',
  delivery_failed: '投递失败',
};

export default function RecruitingPage({
  currentUser,
  onLogout,
}: {
  currentUser?: EnterpriseAuthUser;
  onLogout?: () => void;
}) {
  const navigate = useNavigate();
  const { batchId } = useParams();
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [inboxes, setInboxes] = useState<Inbox[]>([]);
  const [configs, setConfigs] = useState<DigestConfig[]>([]);
  const [batches, setBatches] = useState<DigestBatch[]>([]);
  const [profiles, setProfiles] = useState<RoleProfile[]>([]);
  const [agents, setAgents] = useState<AgentProfileRead[]>([]);
  const [models, setModels] = useState<ModelConfigRead[]>([]);
  const [channels, setChannels] = useState<ChannelBindingRead[]>([]);
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null);
  const [form, setForm] = useState(EMPTY_FORM);
  const [passwordOpen, setPasswordOpen] = useState(false);
  const [password, setPassword] = useState('');
  const [report, setReport] = useState<DigestBatch | null>(null);

  const config = configs[0];
  const inbox = inboxes[0];
  const feishuChannels = useMemo(
    () => channels.filter((item) => item.channel === 'feishu' && item.status === 'active'),
    [channels],
  );

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [nextInboxes, nextConfigs, nextBatches, nextProfiles, nextAgents, nextModels, nextChannels, nextCapabilities] = await Promise.all([
        api.get<Inbox[]>(`/api/enterprise/email-inboxes?tenant_id=${TENANT_ID}`),
        api.get<DigestConfig[]>(`/api/enterprise/recruiting-digest-configs?tenant_id=${TENANT_ID}`),
        api.get<DigestBatch[]>(`/api/enterprise/recruiting-digest-batches?tenant_id=${TENANT_ID}&limit=100`),
        api.get<RoleProfile[]>(`/api/enterprise/recruiting-role-profiles?tenant_id=${TENANT_ID}`),
        api.get<AgentProfileRead[]>(`/api/enterprise/agents?tenant_id=${TENANT_ID}`),
        api.get<ModelConfigRead[]>(`/api/enterprise/model-configs?tenant_id=${TENANT_ID}`),
        api.get<ChannelBindingRead[]>(`/api/enterprise/channels?tenant_id=${TENANT_ID}`),
        api.get<Capabilities>(`/api/enterprise/recruiting-capabilities?tenant_id=${TENANT_ID}`),
      ]);
      setInboxes(nextInboxes);
      setConfigs(nextConfigs);
      setBatches(nextBatches);
      setProfiles(nextProfiles);
      setAgents(nextAgents.filter((item) => !item.is_overall && item.status === 'active'));
      setModels(nextModels.filter((item) => item.enabled));
      setChannels(nextChannels);
      setCapabilities(nextCapabilities);
      const activeConfig = nextConfigs[0];
      if (activeConfig) {
        setForm({
          agent_id: activeConfig.agent_id,
          inbox_binding_id: activeConfig.inbox_binding_id,
          model_config_id: activeConfig.model_config_id,
          feishu_binding_id: activeConfig.feishu_binding_id,
          recipient_open_id: activeConfig.recipient_open_id,
          timezone: activeConfig.timezone,
          snapshot_time: activeConfig.snapshot_time,
          earliest_delivery_time: activeConfig.earliest_delivery_time,
          misfire_deadline_time: activeConfig.misfire_deadline_time,
          raw_retention_days: activeConfig.raw_retention_days,
          result_retention_days: activeConfig.result_retention_days,
          status: activeConfig.status,
        });
      } else {
        setForm((current) => ({
          ...current,
          agent_id: current.agent_id || nextAgents.find((item) => !item.is_overall)?.id || '',
          inbox_binding_id: current.inbox_binding_id || nextInboxes[0]?.id || '',
          model_config_id: current.model_config_id || nextModels.find((item) => item.enabled)?.id || '',
          feishu_binding_id: current.feishu_binding_id || nextChannels.find((item) => item.channel === 'feishu' && item.status === 'active')?.id || '',
        }));
      }
    } catch (error) {
      notify.error(error instanceof Error ? error.message : '加载招聘日报失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!batchId) {
      setReport(null);
      return;
    }
    void api
      .get<DigestBatch>(`/api/enterprise/recruiting-digest-batches/${batchId}?tenant_id=${TENANT_ID}`)
      .then(setReport)
      .catch((error) => notify.error(error instanceof Error ? error.message : '加载完整报告失败'));
  }, [batchId]);

  async function createInbox() {
    try {
      const created = await api.post<Inbox>('/api/enterprise/email-inboxes', { tenant_id: TENANT_ID });
      setInboxes([created]);
      setForm((current) => ({ ...current, inbox_binding_id: created.id }));
      setPasswordOpen(true);
    } catch (error) {
      notify.error(error instanceof Error ? error.message : '创建邮箱绑定失败');
    }
  }

  async function savePassword() {
    if (!inbox || !password) return;
    setSaving(true);
    try {
      const updated = await api.put<Inbox>(`/api/enterprise/email-inboxes/${inbox.id}/credentials`, {
        tenant_id: TENANT_ID,
        password,
      });
      setInboxes([updated]);
      setPassword('');
      setPasswordOpen(false);
      notify.success('邮箱专用密码已加密保存');
    } catch (error) {
      notify.error(error instanceof Error ? error.message : '保存专用密码失败');
    } finally {
      setSaving(false);
    }
  }

  async function testInbox() {
    if (!inbox) return;
    try {
      const updated = await api.post<Inbox>(`/api/enterprise/email-inboxes/${inbox.id}/test`, { tenant_id: TENANT_ID });
      setInboxes([updated]);
      notify.success('只读邮箱连接正常');
    } catch (error) {
      notify.error(error instanceof Error ? error.message : '邮箱连接测试失败');
      void load();
    }
  }

  async function saveConfig() {
    if (!form.agent_id || !form.inbox_binding_id || !form.model_config_id || !form.feishu_binding_id || !form.recipient_open_id.trim()) {
      notify.error('请完整填写员工、邮箱、模型、飞书绑定和接收人 open_id');
      return;
    }
    setSaving(true);
    try {
      const body = { tenant_id: TENANT_ID, ...form, recipient_open_id: form.recipient_open_id.trim() };
      const saved = config
        ? await api.patch<DigestConfig>(`/api/enterprise/recruiting-digest-configs/${config.id}`, body)
        : await api.post<DigestConfig>('/api/enterprise/recruiting-digest-configs', body);
      setConfigs([saved]);
      notify.success('招聘日报配置已保存');
    } catch (error) {
      notify.error(error instanceof Error ? error.message : '保存招聘日报配置失败');
    } finally {
      setSaving(false);
    }
  }

  async function setPrivacyGate(verified: boolean) {
    if (!config) return;
    try {
      const updated = await api.patch<DigestConfig>(`/api/enterprise/recruiting-digest-configs/${config.id}`, {
        tenant_id: TENANT_ID,
        model_privacy_verified: verified,
      });
      setConfigs([updated]);
      notify.success(verified ? '模型隐私门禁已确认' : '模型隐私门禁已撤销');
    } catch (error) {
      notify.error(error instanceof Error ? error.message : '更新模型隐私门禁失败');
    }
  }

  async function runNow() {
    if (!config?.scheduled_task_id) return;
    try {
      await api.post(`/api/enterprise/scheduled-tasks/${config.scheduled_task_id}/run-now?tenant_id=${TENANT_ID}`);
      notify.success('招聘日报任务已启动');
      window.setTimeout(() => void load(), 1200);
    } catch (error) {
      notify.error(error instanceof Error ? error.message : '启动招聘日报失败');
    }
  }

  async function retryDelivery(batch: DigestBatch) {
    try {
      await api.post(`/api/enterprise/recruiting-digest-batches/${batch.id}/retry-delivery`, { tenant_id: TENANT_ID });
      notify.success('投递已重新进入队列');
      void load();
    } catch (error) {
      notify.error(error instanceof Error ? error.message : '重试投递失败');
    }
  }

  async function regenerateProfile(profile: RoleProfile) {
    if (!form.model_config_id) return;
    try {
      await api.post(`/api/enterprise/recruiting-role-profiles/${profile.id}/regenerate`, {
        tenant_id: TENANT_ID,
        model_config_id: form.model_config_id,
      });
      notify.success('已生成新的岗位画像版本');
      void load();
    } catch (error) {
      notify.error(error instanceof Error ? error.message : '重新生成岗位画像失败');
    }
  }

  if (batchId) {
    return (
      <div className="flex min-h-full flex-col bg-[#f7f8fa]">
        <AppHeader onLogout={onLogout} userName={currentUser?.username} title="招聘日报" />
        <main className="mx-auto w-full max-w-[1180px] px-5 py-6">
          <UIButton variant="ghost" className="mb-4 gap-2" onClick={() => navigate('/enterprise/recruiting')}>
            <ArrowLeft className="size-4" /> 返回招聘日报
          </UIButton>
          {report?.report ? <ReportDetail batch={report} /> : <LoadingLine text="正在加载完整报告" />}
        </main>
      </div>
    );
  }

  return (
    <div className="flex min-h-full flex-col bg-[#f7f8fa]">
      <AppHeader onLogout={onLogout} userName={currentUser?.username} title="招聘日报" />
      <main className="mx-auto w-full max-w-[1180px] px-5 py-6">
        <div className="mb-5 flex flex-wrap items-center justify-between gap-3 border-b border-[#e3e7f1] pb-5">
          <div>
            <h1 className="text-[20px] font-semibold text-[#18181a]">飞书邮箱招聘日报</h1>
            <p className="mt-1 text-[13px] text-[#687089]">{inbox?.email_address || 'hr@dlang.ai'} · 07:00 快照 · 08:00 最早投递</p>
          </div>
          <div className="flex items-center gap-2">
            <UIButton variant="outline" size="sm" className="gap-2" onClick={() => void load()}>
              <RefreshCw className="size-4" /> 刷新
            </UIButton>
            <UIButton size="sm" className="gap-2" disabled={!config || config.status !== 'active'} onClick={() => void runNow()}>
              <Play className="size-4" /> 立即运行
            </UIButton>
          </div>
        </div>

        {loading ? (
          <LoadingLine text="正在加载招聘日报配置" />
        ) : (
          <Tabs defaultValue="config" className="w-full">
            <TabsList className="mb-5">
              <TabsTrigger value="config">配置</TabsTrigger>
              <TabsTrigger value="batches">历史批次 {batches.length}</TabsTrigger>
              <TabsTrigger value="roles">岗位画像 {profiles.length}</TabsTrigger>
            </TabsList>

            <TabsContent value="config" className="space-y-5">
              <section className="border-b border-[#e3e7f1] bg-white px-5 py-5">
                <SectionTitle icon={Mail} title="邮箱连接" status={inbox?.status} />
                {inbox ? (
                  <div className="mt-4 grid gap-4 md:grid-cols-[1fr_auto] md:items-end">
                    <div className="grid gap-3 sm:grid-cols-4">
                      <ReadOnlyField label="邮箱" value={inbox.email_address} />
                      <ReadOnlyField label="IMAP" value={`${inbox.imap_host}:${inbox.imap_port}`} />
                      <ReadOnlyField label="文件夹" value={inbox.mailbox_name} />
                      <ReadOnlyField label="凭据" value={inbox.has_credentials ? '已配置' : '未配置'} />
                    </div>
                    <div className="flex gap-2">
                      <UIButton variant="outline" size="sm" className="gap-2" onClick={() => setPasswordOpen(true)}>
                        <KeyRound className="size-4" /> 轮换密码
                      </UIButton>
                      <UIButton variant="outline" size="sm" className="gap-2" disabled={!inbox.has_credentials} onClick={() => void testInbox()}>
                        <ShieldCheck className="size-4" /> 只读测试
                      </UIButton>
                    </div>
                  </div>
                ) : (
                  <UIButton size="sm" className="mt-4 gap-2" onClick={() => void createInbox()}>
                    <Mail className="size-4" /> 创建固定邮箱绑定
                  </UIButton>
                )}
              </section>

              <section className="border-b border-[#e3e7f1] bg-white px-5 py-5">
                <SectionTitle icon={CheckCircle2} title="本机处理能力" />
                <div className="mt-4 grid gap-3 sm:grid-cols-2">
                  <CapabilityRow label="Microsoft Word" item={capabilities?.word} />
                  <CapabilityRow
                    label="7-Zip ZIP / RAR / 7z"
                    item={capabilities?.seven_zip}
                    detail={capabilities?.seven_zip.formats?.join(', ')}
                  />
                </div>
              </section>

              <section className="bg-white px-5 py-5">
                <SectionTitle icon={Save} title="日报配置" status={config?.status} />
                <div className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                  <SelectField label="招聘 HR 员工" value={form.agent_id} onChange={(value) => setForm({ ...form, agent_id: value })} options={agents.map((item) => ({ value: item.id, label: item.name }))} />
                  <SelectField label="邮箱绑定" value={form.inbox_binding_id} onChange={(value) => setForm({ ...form, inbox_binding_id: value })} options={inboxes.map((item) => ({ value: item.id, label: item.email_address }))} />
                  <SelectField label="模型" value={form.model_config_id} onChange={(value) => setForm({ ...form, model_config_id: value })} options={models.map((item) => ({ value: item.id, label: `${item.name} · ${item.model}` }))} />
                  <SelectField label="飞书绑定" value={form.feishu_binding_id} onChange={(value) => setForm({ ...form, feishu_binding_id: value })} options={feishuChannels.map((item) => ({ value: item.id, label: item.bot_name || item.app_id || item.id }))} />
                  <label className="flex min-w-0 flex-col gap-2 text-[12px] text-[#687089]">
                    接收人 open_id
                    <Input className="w-full min-w-0" value={form.recipient_open_id} onChange={(event) => setForm({ ...form, recipient_open_id: event.target.value })} placeholder="ou_xxx" />
                  </label>
                  <label className="flex min-w-0 flex-col gap-2 text-[12px] text-[#687089]">
                    时区
                    <Input className="w-full min-w-0" value={form.timezone} onChange={(event) => setForm({ ...form, timezone: event.target.value })} />
                  </label>
                  <TimeField label="快照时间" value={form.snapshot_time} onChange={(value) => setForm({ ...form, snapshot_time: value })} />
                  <TimeField label="最早投递" value={form.earliest_delivery_time} onChange={(value) => setForm({ ...form, earliest_delivery_time: value })} />
                  <TimeField label="补跑截止" value={form.misfire_deadline_time} onChange={(value) => setForm({ ...form, misfire_deadline_time: value })} />
                </div>
                <div className="mt-5 flex flex-wrap items-center justify-between gap-4 border-t border-[#eef0f4] pt-4">
                  <div className="flex flex-wrap items-center gap-5">
                    <ToggleRow label="启用日报" checked={form.status === 'active'} onChange={(checked) => setForm({ ...form, status: checked ? 'active' : 'disabled' })} />
                    <ToggleRow label="模型隐私门禁" checked={Boolean(config?.model_privacy_verified)} disabled={!config} onChange={(checked) => void setPrivacyGate(checked)} />
                  </div>
                  <UIButton className="gap-2" disabled={saving} onClick={() => void saveConfig()}>
                    <Save className="size-4" /> {saving ? '保存中' : '保存配置'}
                  </UIButton>
                </div>
              </section>
            </TabsContent>

            <TabsContent value="batches">
              <section className="overflow-hidden border border-[#e3e7f1] bg-white">
                <div className="grid grid-cols-[minmax(180px,1fr)_100px_80px_80px_80px_150px] gap-3 border-b border-[#e3e7f1] bg-[#f7f8fa] px-4 py-3 text-[11px] text-[#687089]">
                  <span>批次</span><span>状态</span><span>邮件</span><span>已评分</span><span>待确认</span><span>操作</span>
                </div>
                {batches.length ? batches.map((batch) => (
                  <div key={batch.id} className="grid grid-cols-[minmax(180px,1fr)_100px_80px_80px_80px_150px] items-center gap-3 border-b border-[#eef0f4] px-4 py-3 text-[12px] last:border-b-0">
                    <button className="truncate text-left text-[#1a71ff] hover:underline" onClick={() => navigate(`/recruiting/digests/${batch.id}`)}>{formatDate(batch.snapshot_at)} · {batch.id.slice(-8)}</button>
                    <Status value={batch.status} />
                    <span>{batch.new_email_count}</span><span>{batch.scored_count}</span><span>{batch.review_count}</span>
                    <div className="flex gap-1">
                      <UIButton variant="ghost" size="sm" className="gap-1" onClick={() => navigate(`/recruiting/digests/${batch.id}`)}><FileText className="size-4" /> 报告</UIButton>
                      {batch.status === 'delivery_failed' && <UIButton variant="ghost" size="sm" title="重试投递" onClick={() => void retryDelivery(batch)}><RotateCcw className="size-4" /></UIButton>}
                    </div>
                  </div>
                )) : <EmptyLine text="暂无招聘日报批次" />}
              </section>
            </TabsContent>

            <TabsContent value="roles">
              <section className="divide-y divide-[#eef0f4] border border-[#e3e7f1] bg-white">
                {profiles.length ? profiles.map((profile) => (
                  <div key={profile.id} className="grid gap-4 px-5 py-4 lg:grid-cols-[1fr_220px_auto] lg:items-center">
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2"><strong className="text-[14px]">{profile.display_name}</strong><Status value={`v${profile.version}`} /></div>
                      <div className="mt-1 text-[12px] text-[#687089]">{profile.standard_role_key}</div>
                      <div className="mt-2 flex flex-wrap gap-1">{profile.aliases.map((alias) => <span key={alias.raw_title} className="rounded bg-[#f2f4f7] px-2 py-1 text-[11px] text-[#596179]">{alias.raw_title}</span>)}</div>
                    </div>
                    <div className="text-[12px] text-[#687089]">核心能力 {profile.core_capabilities.length} 项 · 关键 {profile.core_capabilities.filter((item) => item.is_critical).length} 项</div>
                    <UIButton variant="outline" size="sm" className="gap-2" onClick={() => void regenerateProfile(profile)}><RefreshCw className="size-4" /> 生成新版本</UIButton>
                  </div>
                )) : <EmptyLine text="暂无岗位画像" />}
              </section>
            </TabsContent>
          </Tabs>
        )}
      </main>

      <Dialog open={passwordOpen} onOpenChange={(open) => { setPasswordOpen(open); if (!open) setPassword(''); }}>
        <DialogContent className="max-w-[420px]">
          <DialogTitle>写入飞书邮箱专用密码</DialogTitle>
          <Input type="password" autoComplete="new-password" value={password} onChange={(event) => setPassword(event.target.value)} placeholder="专用密码" />
          <div className="flex justify-end gap-2">
            <UIButton variant="outline" onClick={() => setPasswordOpen(false)}>取消</UIButton>
            <UIButton disabled={!password || saving} onClick={() => void savePassword()}>加密保存</UIButton>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function ReportDetail({ batch }: { batch: DigestBatch }) {
  const report = batch.report!;
  return (
    <section className="bg-white px-5 py-5">
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-[#e3e7f1] pb-4">
        <div><h1 className="text-[20px] font-semibold">招聘 HR 日报｜{report.report_date}</h1><p className="mt-1 text-[12px] text-[#687089]">统计窗口：{report.window}</p></div>
        <Status value={batch.status} />
      </div>
      <div className="grid grid-cols-3 border-b border-[#e3e7f1] py-4 text-center"><Metric label="新增邮件" value={report.new_email_count} /><Metric label="可评分" value={report.scored_count} /><Metric label="待确认" value={report.review_count} /></div>
      <div className="divide-y divide-[#eef0f4]">
        {report.ranked_candidates.map((candidate) => (
          <article key={candidate.application_id} className="py-5">
            <div className="flex flex-wrap items-center gap-3"><span className="flex size-7 items-center justify-center rounded bg-[#18181a] text-[12px] text-white">{candidate.batch_rank}</span><strong>{candidate.candidate_display_name || '候选人记录'}</strong><span className="text-[13px] text-[#687089]">{candidate.applied_job}</span><span className="ml-auto text-[18px] font-semibold text-[#1a71ff]">{candidate.recommendation_index.toFixed(1)}<small className="text-[11px] font-normal">/10</small></span></div>
            <div className="mt-4 grid gap-3 sm:grid-cols-4">{Object.entries(candidate.dimension_scores).map(([key, value]) => <ReadOnlyField key={key} label={dimensionLabel(key)} value={value.toFixed(1)} />)}</div>
            <div className="mt-4 grid gap-4 md:grid-cols-2"><EvidenceBlock title="推荐理由" values={candidate.recommendation_reasons} /><EvidenceBlock title="主要风险/缺口" values={candidate.risks} /></div>
          </article>
        ))}
      </div>
      {report.needs_review.length > 0 && <div className="border-t border-[#e3e7f1] pt-4"><h2 className="text-[14px] font-medium">待人工确认</h2>{report.needs_review.map((item) => <div key={item.application_id} className="mt-2 flex justify-between bg-[#fff7e8] px-3 py-2 text-[12px]"><span>{item.candidate_display_name || item.application_id}</span><span>{item.error_code || 'NEEDS_REVIEW'}</span></div>)}</div>}
      <p className="mt-5 border-t border-[#e3e7f1] pt-4 text-[11px] leading-5 text-[#687089]">{report.disclaimer}</p>
    </section>
  );
}

function SectionTitle({ icon: Icon, title, status }: { icon: typeof Mail; title: string; status?: string }) {
  return <div className="flex items-center gap-2"><Icon className="size-4 text-[#596179]" /><h2 className="text-[14px] font-medium">{title}</h2>{status && <Status value={status} />}</div>;
}

function Status({ value }: { value: string }) {
  const bad = ['failed', 'delivery_failed', 'missed', 'error', 'auth_required'].includes(value);
  const good = ['active', 'delivered'].includes(value);
  return <span className={`inline-flex w-fit shrink-0 whitespace-nowrap rounded px-2 py-1 text-[10px] ${bad ? 'bg-[#fff0f0] text-[#bb2525]' : good ? 'bg-[#eaf8ef] text-[#237a42]' : 'bg-[#eef3ff] text-[#315fa8]'}`}>{STATUS_LABELS[value] || value}</span>;
}

function ReadOnlyField({ label, value }: { label: string; value: string }) {
  return <div className="min-w-0"><div className="text-[10px] text-[#8a91a5]">{label}</div><div className="mt-1 truncate text-[12px] text-[#303645]">{value || '-'}</div></div>;
}

function SelectField({ label, value, onChange, options }: { label: string; value: string; onChange: (value: string) => void; options: Array<{ value: string; label: string }> }) {
  return <label className="flex min-w-0 flex-col gap-2 text-[12px] text-[#687089]">{label}<Select value={value} onValueChange={onChange}><SelectTrigger className="w-full min-w-0 [&>span]:truncate"><SelectValue placeholder={`选择${label}`} /></SelectTrigger><SelectContent>{options.map((option) => <SelectItem key={option.value} value={option.value}>{option.label}</SelectItem>)}</SelectContent></Select></label>;
}

function TimeField({ label, value, onChange }: { label: string; value: string; onChange: (value: string) => void }) {
  return <label className="flex min-w-0 flex-col gap-2 text-[12px] text-[#687089]">{label}<Input className="w-full min-w-0" type="time" value={value} onChange={(event) => onChange(event.target.value)} /></label>;
}

function ToggleRow({ label, checked, onChange, disabled }: { label: string; checked: boolean; onChange: (checked: boolean) => void; disabled?: boolean }) {
  return <label className="flex items-center gap-2 text-[12px] text-[#464c5e]"><Switch checked={checked} disabled={disabled} onCheckedChange={onChange} />{label}</label>;
}

function CapabilityRow({ label, item, detail }: { label: string; item?: { available: boolean; version?: string; error_code?: string }; detail?: string }) {
  return <div className="flex items-center justify-between gap-3 border border-[#e3e7f1] px-3 py-3"><div className="min-w-0"><div className="text-[12px] font-medium">{label}</div><div className="mt-1 break-words text-[10px] text-[#8a91a5]">{item?.version || item?.error_code || '未探测'}{detail ? ` · ${detail}` : ''}</div></div><Status value={item?.available ? 'active' : 'error'} /></div>;
}

function Metric({ label, value }: { label: string; value: number }) {
  return <div><div className="text-[20px] font-semibold">{value}</div><div className="text-[10px] text-[#8a91a5]">{label}</div></div>;
}

function EvidenceBlock({ title, values }: { title: string; values: string[] }) {
  return <div><h3 className="text-[11px] text-[#8a91a5]">{title}</h3><ul className="mt-2 space-y-1 text-[12px] leading-5 text-[#464c5e]">{values.length ? values.map((value, index) => <li key={`${index}-${value}`}>· {value}</li>) : <li>未知</li>}</ul></div>;
}

function EmptyLine({ text }: { text: string }) { return <div className="px-5 py-10 text-center text-[12px] text-[#8a91a5]">{text}</div>; }
function LoadingLine({ text }: { text: string }) { return <div className="flex items-center justify-center gap-2 py-16 text-[12px] text-[#687089]"><RefreshCw className="size-4 animate-spin" />{text}</div>; }
function formatDate(value: string) { const date = new Date(value.endsWith('Z') ? value : `${value}Z`); return Number.isNaN(date.getTime()) ? '-' : date.toLocaleString('zh-CN', { hour12: false }); }
function dimensionLabel(value: string) { return ({ relevant_work_experience: '工作经历', relevant_project_experience: '项目经验', relevant_internship_experience: '实习经历', other_supporting_evidence: '其他证据' } as Record<string, string>)[value] || value; }
