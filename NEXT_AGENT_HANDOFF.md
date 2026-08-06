# StaffDeck 新对话项目交接

> 最后核对：2026-08-06，工作目录 `E:\TLong\StaffDeck`，分支 `main`。
> 这是新对话的第一份阅读材料，负责建立项目地图和当前上下文；源码、测试和实时数据库仍是最终事实来源。

## 0. 给新对话的直接指令

进入项目后先完成以下动作，再决定是否修改代码：

1. 完整阅读本文。
2. 执行 `git status --short --branch`，保留所有已有修改和未跟踪文件。
3. 按任务阅读本文指向的源码、邻近测试和专项文档，不要只依据本文推断行为。
4. 涉及运行数据时重新检查 SQLite 或 API；本文中的员工、资源数量和绑定关系只是快照。
5. 修改前说明最小改动范围，修改后运行对应的聚焦测试、构建或检查。

禁止把 API Key、渠道凭据、`APP_SECRET`、内部令牌或数据库中的加密密文写入源码、日志、提交信息或本文。

## 1. 一分钟理解 StaffDeck

StaffDeck 是一套企业数字员工运行平台。管理员或员工创建数字员工，并为它绑定模型、SOP、通用技能、知识库和外部工具；用户从 Web 或即时通信渠道发起任务后，后端的 Agent Loop 根据当前员工的可见资源选择能力、执行多步骤任务，并持久化会话、事件、记忆、反馈和审计数据。

它不是单纯聊天页面，也不是单纯 RAG 服务。核心职责有：

- 管理数字员工的身份、岗位、发布状态和资源范围。
- 通过状态机式 SOP 处理需要收集参数、分支和多轮推进的流程。
- 通过 `SKILL.md` 式通用技能生成受控 Runner，执行一次性通用任务。
- 从结构化知识库检索原文证据并生成引用。
- 统一执行 HTTP 工具和 MCP 工具。
- 支持定时任务、长期记忆、反馈分析、人工接管和执行追踪。
- 从 Web、微信、企业微信、飞书和钉钉接入同一套 Agent 运行时。

## 2. 稳定架构

StaffDeck 当前是一个模块化单体：React 前端和 FastAPI API 由一个 Python 进程、一个端口提供；SQLite 是默认持久化层，后台线程承担流式执行、定时任务和渠道收发。

```text
React Web / 桌面壳 / IM 渠道
                |
                v
FastAPI API + 鉴权 + 租户/员工范围
                |
                v
AgentLoop
  |- Router：选择任务和场景 SOP
  |- SkillRuntime：维护 SOP、步骤、槽位和待办任务
  |- StepAgent：决定当前步骤的知识、工具、追问或推进动作
  |- GeneralSkillRunner：执行通用技能 Runner
  |- KnowledgeService：分层检索原文证据
  |- ToolExecutor：统一执行 HTTP / MCP 工具
  `- ResponseGenerator：生成最终回复
                |
                v
SQLite + LLM Provider + 外部 HTTP/MCP 服务
```

后端启动组合根是 `backend/app/main.py`。`backend/single_port_app.py` 在同一个 FastAPI 应用上挂载 `frontend-enterprise/dist`，并为 React Router 提供 SPA fallback。开发入口 `scripts/dev.py` 会先构建前端，再启动单端口应用。

## 3. 仓库地图

| 路径 | 职责 | 优先入口 |
| --- | --- | --- |
| `backend/app/main.py` | FastAPI 生命周期、路由注册、后台服务启停 | `on_startup()`、`on_shutdown()` |
| `backend/single_port_app.py` | 单端口 UI/API、静态资源、SPA fallback | `app.mount(...)`、页面路由 |
| `backend/app/api/` | HTTP 输入、鉴权、租户校验、响应和 SSE | `chat.py`、各资源 API |
| `backend/app/core/` | Agent 编排、路由、步骤执行、反思和回复 | `agent_loop.py` |
| `backend/app/agents/` | 员工资源范围、私有分支和版本 | 绑定与分支服务 |
| `backend/app/skills/` | SOP Schema、编辑、蒸馏和反思 | `skill_schema.py` |
| `backend/app/general_skills/` | 通用技能导入、选择、Runner 和运行环境 | `runner.py` |
| `backend/app/knowledge/` | 文档解析、入库、Bucket、Chunk、OKF 和检索 | `service.py` |
| `backend/app/tools/` | HTTP/MCP 发现、同步与执行 | `tool_executor.py`、`mcp_client.py` |
| `backend/app/llm/` | 模型协议、请求适配、流式和 JSON 输出 | `client.py`、`model_protocols.py` |
| `backend/app/channels/` | 多渠道接入、身份、路由、可靠收发 | `service_intake.py`、`service_outbox.py` |
| `backend/app/scheduled_tasks/` | 计划计算、租约、任务执行 Worker | `worker.py`、`service.py` |
| `backend/app/recruiting/` | 只读邮箱同步、附件隔离、岗位画像、推荐指数和日报 | `service.py`、`domain.py`、`artifacts.py` |
| `backend/app/memory/` | 长期记忆提取与召回 | `service.py` |
| `backend/app/observability/` | AgentEvent 和各类 Span | 事件/追踪服务 |
| `backend/app/security/` | 登录、密码、加密、租户和权限 | 现有 `ensure_*` / `require_*` helper |
| `backend/app/db/` | SQLModel 表、SQLite 初始化、迁移和种子 | `models.py`、`database.py` |
| `frontend-enterprise/src/App.tsx` | 登录恢复、应用壳、角色保护和页面路由 | `Shell`、`App` |
| `frontend-enterprise/src/api/client.ts` | 统一认证请求、上传和 SSE 解析 | `api` client |
| `frontend-enterprise/src/pages/chat/useChatSession.ts` | 聊天状态、队列、流式事件和断流对账 | 主聊天 Hook |
| `frontend-enterprise/src/pages/RecruitingPage.tsx` | 招聘邮箱、隐私门禁、批次报告和岗位画像管理 | `/enterprise/recruiting` |
| `contracts/agent/v1/` | Agent 兼容契约、Schema、黄金样例 | `manifest.json` |
| `backend/tests/` | 后端单元、集成、契约和回归测试 | 与改动模块同名测试 |
| `scripts/` | 跨平台启动、停止和状态检查 | `dev.py` |
| `packaging/` | PyInstaller 与三平台安装包 | `ultrarag.spec`、平台脚本 |

`legacy_*` 模块仍在真实调用链和兼容契约中。不要因为名字含 `legacy` 就删除或绕开，先查调用方和相关测试。

## 4. 一次 Web 消息的真实链路

1. `frontend-enterprise/src/pages/chat/useChatSession.ts` 向 `POST /api/chat/stream` 提交消息、`agent_id`、会话信息和 `client_turn_id`。
2. `backend/app/api/chat.py` 启动使用独立数据库 Session 的工作线程。
3. `AgentLoop.handle_turn_stream()` 创建或恢复会话，写入用户消息，加载模型、员工可见资源、历史上下文和长期记忆。
4. `Router` 决定继续/启动 SOP、澄清，或把请求交给通用能力选择。
5. SOP 路径由 `SkillRuntime` 恢复状态，再由 `StepAgent` 在当前节点允许的动作内选择知识查询、工具调用、追问、推进或人工接管。
6. 非 SOP 路径可以选择通用技能、直接工具、企业知识或普通模型回答。
7. `ResponseGenerator` 结合工具结果、知识引用、员工 Persona 和任务状态生成回复。
8. 消息、会话状态和运行事件先写入 SQLite；HTTP 端按游标轮询 `agent_events` 并转为 SSE。
9. 前端显示增量文本和执行记录；结束或断流后再拉取消息与 Trace 对账。
10. 完成后可异步提取长期记忆，渠道会话还会登记可靠出站投递。

关键点：流式链路不是把模型生成器直接透传给浏览器，而是“执行线程 -> `agent_events` -> SSE relay”。因此事件表是实时展示、断流恢复和审计的共同事实来源。

## 5. 核心领域模型

### 5.1 数字员工和资源范围

`AgentProfile` 是运行时资源隔离中心。员工通过 `AgentResourceBinding`、模型绑定以及技能/知识分支获得可见能力。相同代码面对不同员工会表现不同，原因通常是资源绑定和版本不同，而不是存在多套 Agent 程序。

每次执行都必须重新考虑：`tenant_id`、用户角色、员工创建者/可访问范围、资源归属、发布状态和员工私有分支。不要只按资源 ID 查询后直接返回或执行。

### 5.2 两种技能不要混淆

| 类型 | 数据模型 | 内容 | 适用场景 | 执行者 |
| --- | --- | --- | --- | --- |
| SOP / 场景技能 | `Skill` | 版本化 `content_json` 状态图 | 请假、采购、退款等多轮流程 | Router + SkillRuntime + StepAgent |
| 通用技能 | `GeneralSkill` | `skill_markdown`、随包文件、运行配置 | 一次性分析、转换、脚本任务 | GeneralSkillRunner |

SOP 把运行状态保存到 `ChatSession`，重点字段包括当前技能/步骤、槽位、技能栈、待办任务、等待输入、知识上下文和历史摘要。当前 SOP 动作目录既支持单个 HTTP 工具，也支持以 `call_mcp:<server_id>` 引用整个 MCP 工具集；运行时仍只允许调用当前员工可见、已启用的实际工具。

通用技能会让模型生成 Python 或 Bash Runner，在临时目录恢复技能包后执行，再由模型检查结果并最多尝试修复。运行时、超时、输出大小和网络安装受配置限制；`GENERAL_SKILL_NETWORK_INSTALL` 默认关闭，不能把它当成完整安全沙箱或任意联网环境。

### 5.3 知识系统

当前知识链是结构感知的 PageIndex/OKF 分层检索，不是 embedding + 向量数据库：

```text
KnowledgeBase
  -> KnowledgeDocument（文档卡、章节树）
  -> KnowledgeBucket（结构或任务分桶）
  -> KnowledgeChunk（原文证据）
  -> KnowledgeConcept（OKF 概念与引用关系）
```

查询先用模型或确定性回退选择知识库/Bucket，再以词法命中、章节邻近和结构信息选 Chunk，最后组装带来源的证据。PDF 依赖文本层，没有 OCR；旧 `.doc` 不支持，应先转为 `.docx`。知识检索异常应分别检查员工绑定、知识库状态、文档入库状态、Bucket/Chunk/Concept 数据和模型路由，不要只看页面上是否有文档卡片。

### 5.4 工具和 MCP

数据库中的 `Tool` 是 AgentLoop 可调用的统一工具记录。HTTP 工具保存请求配置；MCP 工具通过 `MCPServer` 发现并同步，`Tool.mcp_server_id` 指向连接级资源。

`ToolExecutor` 负责鉴权后的统一调用、超时、环境变量占位符解析和结构化 `ToolResult`。AgentLoop 不应增加某个 MCP transport 的专用分支。MCP 当前支持 `stdio`、`streamable_http`、`sse` 和内置演示 transport；先以 `tools/list` 成功作为 MCP 可用的最低门槛，再验证员工可见性和真实调用。

仓库还包含 OpenET 的内置 MCP 实现：`backend/app/tools/openet_mcp/`，路由 `/api/mcp/openet`。其凭据只允许从忽略的本地环境配置读取。当前工作区已删除旧 `OPENET_MCP_PRD.md`，这是未提交的用户改动，不要擅自恢复。

### 5.5 模型适配

统一模型层支持三种协议：

- `openai_chat_completions`
- `anthropic_messages`
- `gemini_generate_content`

业务模块只通过 `backend/app/llm/` 调用模型，不直接耦合某个供应商 SDK。模型配置由管理 API 创建和验证，密钥加密入库；运行前要检查配置是否存在、启用、可信且适合运行。OpenAI 兼容代理的 base URL 通常需包含 `/v1`，但具体值必须从当前配置核对，不能从旧交接复制。

### 5.6 渠道、定时任务、记忆和可观测性

- 渠道适配器位于 `backend/app/channels/adapters/`，当前注册微信、企业微信、飞书、钉钉。
- 渠道入站包含去重、持久化、身份解析、员工选择/自动路由和 AgentLoop 调用；出站使用 Outbox、重试和投递记录。
- 定时任务由进程内 Worker 扫描到期任务，使用租约避免重复领取，并复用 AgentLoop 和渠道投递链。
- 长期记忆按租户、用户和员工范围召回；它与 `ChatSession` 中的短期任务状态不是同一层。
- `AgentEvent`、Trace、LLM/知识 Span 和工作记录用于还原每轮执行。调查问题时优先追一条具体 session/turn/event 链。

### 5.7 招聘邮箱日报

招聘日报是独立的类型化定时任务，不创建 `ChatSession`，也不进入通用 `AgentLoop`。`execution_kind=recruiting_digest` 从固定飞书企业邮箱以 IMAP TLS、`EXAMINE`、UID SEARCH 和 `BODY.PEEK` 只读拉取增量邮件；首次运行只建立 UID 基线，不回溯历史邮件。MIME、Word、7-Zip、岗位解析、画像版本、分阶段权重、9.0 门槛和跨岗位稳定排名分别由 `backend/app/recruiting/` 内的职责模块处理。

日报通过 `scheduled_digest` Outbox 向白名单内单个 `open_id` 投递一条不拆分消息；投递重试不得重读邮箱或重跑模型。原始邮件和附件加密保留 7 天，结构化履历、评价、完整报告及对应 Outbox 正文保留 90 天，后台每天清理一次。邮箱密码 API 不回显密文；配置只有在邮箱只读测试通过且模型隐私指纹匹配后才能启用。

## 6. 前端页面入口

主要用户入口：

- 数字员工广场：`http://127.0.0.1:5173/workspace/gallery`
- 聊天：`/workspace/chat` 或 `/workspace/chat/:sessionId`
- 企业管理：`/enterprise/dashboard`
- 招聘日报：`/enterprise/recruiting`；完整报告：`/recruiting/digests/:batchId`
- Swagger：`http://127.0.0.1:5173/docs`
- 健康检查：`http://127.0.0.1:5173/api/health`

管理端包含员工、知识、SOP 蒸馏、通用技能、工具/MCP、定时任务、记忆、反馈、渠道、账号和模型配置等页面。账户与模型配置有管理员前端保护，但安全边界必须由后端再次校验。

## 7. 本地开发和运行

环境要求：Python 3.11+、Node.js 20+、npm。当前机器已有 `backend\.venv`、`backend\.env` 和前端依赖。

```powershell
# 推荐的 Windows 生命周期命令
.\scripts\dev_status.ps1
.\scripts\dev_up.ps1 --detach
.\scripts\dev_down.ps1

# 等价的统一 Python 入口
.\backend\.venv\Scripts\python.exe scripts\dev.py status
.\backend\.venv\Scripts\python.exe scripts\dev.py up --detach
.\backend\.venv\Scripts\python.exe scripts\dev.py down

# 健康检查
curl.exe http://127.0.0.1:5173/api/health
```

本机曾出现 Codex 命令会话结束后普通后台子进程被回收的情况。若 `--detach` 无法持续运行，可使用隐藏窗口启动前台 supervisor：

```powershell
Start-Process -FilePath 'E:\TLong\StaffDeck\backend\.venv\Scripts\python.exe' `
  -ArgumentList 'scripts\dev.py','up' `
  -WorkingDirectory 'E:\TLong\StaffDeck' `
  -RedirectStandardOutput 'E:\TLong\StaffDeck\.dev\logs\launcher.log' `
  -RedirectStandardError 'E:\TLong\StaffDeck\.dev\logs\launcher.err.log' `
  -WindowStyle Hidden
```

默认开发账号是 `admin` / `admin`，仅用于本地首次登录，之后应改密码。不要在生产或对外演示环境沿用。

## 8. 修改和验证方法

先运行与改动最接近的检查，不要以全仓遗留告警掩盖本次结果。

| 改动范围 | 最低验证 |
| --- | --- |
| 后端普通逻辑 | 对应 `backend/tests/test_*.py` |
| Agent 状态、SSE、事件或会话投影 | 相关 Agent/Chat 测试 + `contracts/agent/v1/` 兼容测试 |
| SOP / MCP | `test_skill_editing_and_stats.py`、`test_mcp_servers_api.py` 及相关工具测试 |
| 通用技能 | `test_general_skills.py`、通用技能 Provider/Runner 测试 |
| 知识 | `test_knowledge_base.py`、`test_knowledge_citations.py` 及相关检索测试 |
| 渠道 | 对应渠道、inbox/outbox、身份和路由测试 |
| 招聘日报 | `test_recruiting.py` + 定时任务/飞书 Outbox 回归 + Word/7-Zip 能力探针 |
| 启动脚本 | `backend/tests/test_dev_scripts.py` |
| 修改 Python | 聚焦 Ruff `--select F401,F821` |
| 前端逻辑 | 相关 Vitest |
| 前端类型/页面/构建配置 | `npm --prefix frontend-enterprise run build` |
| 新增界面文案 | 再运行 `npm --prefix frontend-enterprise run i18n:check` |
| 任意文本/代码修改 | `git diff --check` |

命令示例：

```powershell
.\backend\.venv\Scripts\python.exe -m pytest backend\tests\test_general_skills.py -q
.\backend\.venv\Scripts\python.exe -m ruff check backend\app\general_skills\runner.py --select F401,F821
npm --prefix frontend-enterprise test -- GeneralSkillsPage.test.ts
npm --prefix frontend-enterprise run build
git diff --check
```

仓库当前约有 107 个后端测试文件、9 个前端测试文件和 50 个 Agent 契约 fixture。全量 Ruff 存在历史告警；除非任务明确要求，不做顺带全仓清理。

## 9. 2026-08-06 当前工作区快照

这一节会过时，新对话必须现场复核。

- 分支：`main`，跟踪 `origin/main`。
- 当前 HEAD：`e8e1932 优化 General Skill 稳定性与 SOP MCP 工具集`。
- 服务：supervisor 和 app 正在运行，`5173` 正在监听，`/api/health` 正常。
- 本地 SQLite：`backend/skill_agent_loop.db`，WAL 模式，数据库和 WAL 体积较大；运行时不要直接删除或替换。
- 当前数据库大致包含：11 个员工、27 个 SOP、13 个通用技能、473 个知识库、469 个知识文档、17,995 个 Chunk、11,718 个 Concept、5 个 MCP Server、58 个 Tool、34 个 Session、124 条 Message、1 个模型配置。
- 已创建独立“招聘 HR”员工、固定 `hr@dlang.ai` 邮箱绑定、1 个停用的 `RecruitingDigestConfig` 和 1 个暂停的 `recruiting_digest` 定时任务；邮箱无凭据，模型隐私门禁未确认，不能执行真实同步。
- 当前已有未提交用户工作：
  - 修改：`FEISHU_MAIL_RECRUITING_HR_PRD.md`
  - 删除：`OPENET_MCP_PRD.md`
  - 未跟踪：`.playwright-cli/`、`CUSTOMER_DEMO_RUNBOOK.md`、`backend/connector-locks/`、`output/`

不要还原、删除、覆盖或顺带提交这些现有改动。若任务需要触及其中某项，先读取实际 diff 并在现状上继续。

## 10. 当前值得注意的功能线索

- 最新提交增强了 General Skill 的 Runner 生成、语法诊断、执行结果检查、自动反思修复、运行事件和技能包文件处理。
- SOP 编辑/蒸馏现在可以把 MCP Server 作为工具集动作，而不是要求用户逐个选择 MCP 子工具；保存和运行时仍会校验实际工具、员工范围和启用状态。
- `FEISHU_MAIL_RECRUITING_HR_PRD.md` v0.3 的核心代码已实现：招聘数据模型/兼容迁移、管理 API、只读 IMAP、MIME 与附件隔离、Word/7-Zip、画像与确定性评分、类型化调度、单条飞书 Outbox、保留期清理和管理页面。当前本机 Word `16.0`、7-Zip `26.02` 探针通过，聚焦招聘/打包测试 19 项、相关定时任务/飞书/数据库/API 回归 82 项通过，前端生产构建通过。
- 真实验收仍未完成：旧截图密码必须撤销后由用户在页面写入新专用密码；当前模型及中继还需完成候选人数据隐私核验；扫描 PDF/图片在视觉能力探针完成前会进入 `VISION_UNAVAILABLE` 待确认；随后还需投递受控测试邮件、验证真实单条飞书消息并观察至少 3 个正常日报批次。不得把当前状态描述为已上线自动运行。
- `CUSTOMER_DEMO_RUNBOOK.md` 是未跟踪的演示手册。使用前应验证其中 MCP、Skill 和 SOP 示例的服务端、账号和数据仍可用。
- OpenET MCP 实现仍在源码中，但其旧 PRD 当前被用户删除。区分“本地 transport/auth 可用”“上游数据当前可用”“某个数据集支持目标请求”三个层次。

## 11. 常见误区

- 不要把“Router 未命中 SOP”理解成系统没有能力；它还可进入通用技能、直接工具、知识或普通回答。
- 不要把 SOP `Skill` 和 `GeneralSkill` 当成同一种资源。
- 不要把 MCP Server 和同步后的 Tool 行混为一谈；前者是连接，后者才是 AgentLoop 的可调用资源。
- 不要把数据库中的公共资源等同于某员工可见资源；始终追踪绑定和私有分支。
- 不要把知识文档卡片存在等同于检索可用；入库状态、Bucket、Chunk、Concept 和员工绑定都可能影响结果。
- 不要把模型配置探针通过等同于真实 AgentLoop 成功；至少再做一轮真实聊天链验证。
- 不要根据旧文档硬编码员工名、模型名、模型 URL、工具数量或绑定关系。
- 不要直接删除 SQLite 重建。模型配置、员工绑定、知识、会话和运行数据都在其中，先备份并确认任务范围。
- 不要修改 `frontend-enterprise/dist` 作为源代码；修改 `src/` 后重新构建。
- 不要顺带修复全仓 lint、格式化整个项目或清理用户运行产物。

## 12. 文档导航

按任务选读，不需要新对话一次读完全部：

| 文档 | 用途 |
| --- | --- |
| `README.zh.md` | 产品定位、安装和公开快速开始 |
| `项目整体大纲.md` | 2026-08-03 的详细架构与源码阅读地图 |
| `用户手册.md` | 当前机器的启动、数据库、MCP 和日志操作 |
| `CUSTOMER_DEMO_RUNBOOK.md` | 客户演示流程；当前未跟踪，使用前重新验收 |
| `FEISHU_MAIL_RECRUITING_HR_PRD.md` | 招聘邮件日报 v0.3 契约；核心实现已落地，真实数据验收仍受密码/隐私门禁约束 |
| `development-feishu-channel.md` | 飞书渠道接入设计与开发背景 |
| `design-model-api-protocols.md` | 多模型协议设计 |
| `design-qa.md` | 设计与质量检查背景 |
| `contracts/agent/v1/manifest.json` | Agent 兼容契约索引 |
| `.agents/skills/develop-staffdeck/SKILL.md` | 仅在显式调用 `$develop-staffdeck` 时采用的仓库开发规范 |

详细架构文档形成于 2026-08-03，晚于它的功能要以源码和最近提交为准，尤其是 General Skill、SOP MCP 工具集和前端相关页面。

## 13. 新任务的推荐落点

1. 先把用户问题翻译成一个具体业务流，例如“Web 用户发消息后为什么没调用某工具”。
2. 从调用入口开始追：前端 Hook/API -> `api/` -> `AgentLoop` -> 对应能力服务 -> 数据表/外部依赖。
3. 同时检查员工资源范围和当前数据库数据，不只读类定义。
4. 找到最近的聚焦测试，以测试表达的行为作为兼容边界。
5. 只改拥有该职责的模块；HTTP 映射留在 `api/`，领域逻辑回到对应包，模型调用走 `llm/`，持久化字段与迁移同步处理。
6. 用一条真实业务流验证改动，不要只验证孤立函数或页面能打开。

## 14. 本文维护规则

当以下事实变化时更新本文，而不是再创建新的交接文件：

- 入口、目录职责或核心调用链改变。
- 启动方式、端口、数据库位置或验证命令改变。
- 新增关键能力、协议、渠道或重要兼容边界。
- 当前主线工作和未提交状态发生会影响下一对话的变化。

更新时保留“稳定架构”和“日期快照”的区分，删除已失效事实，不累积互相矛盾的补丁式记录。
