# StaffDeck 本地启动交接

## 工作目录

`E:\TLong\StaffDeck`

## 当前状态

- 后端 Python 3.11 虚拟环境：`backend\.venv`
- 后端依赖：已安装 `backend[dev]`
- 前端依赖：已执行 `npm --prefix frontend-enterprise ci`
- 本地配置：`backend\.env` 已创建，使用 SQLite 和随机强 `APP_SECRET`；`OPENET_API_TOKEN` 已由用户本地配置
- 前端已构建：`frontend-enterprise\dist`
- 服务当前由 supervisor 以单端口模式运行于 `http://127.0.0.1:5173`
- 默认模型：`gpt-5.6-sol`，已验证、启用并设为默认
- OpenET MCP：使用同端口 Streamable HTTP；7 个工具已同步并私有绑定给当前名称为“11”的员工（旧交接中为“人事”）
- 默认账号：`admin` / `admin`，首次登录后应修改密码

不要修改或删除用户已有的未跟踪目录 `.idea\`。

## 常用命令

```powershell
# 状态
.\backend\.venv\Scripts\python.exe scripts\dev.py status

# 启动
.\backend\.venv\Scripts\python.exe scripts\dev.py up --detach

# 若 Codex 宿主回收上述后台子进程，使用已验证的隐藏前台 supervisor
Start-Process -FilePath 'E:\TLong\StaffDeck\backend\.venv\Scripts\python.exe' `
  -ArgumentList 'scripts\dev.py','up' `
  -WorkingDirectory 'E:\TLong\StaffDeck' `
  -RedirectStandardOutput 'E:\TLong\StaffDeck\.dev\logs\launcher.log' `
  -RedirectStandardError 'E:\TLong\StaffDeck\.dev\logs\launcher.err.log' `
  -WindowStyle Hidden

# 停止
.\backend\.venv\Scripts\python.exe scripts\dev.py down

# 健康检查
curl.exe http://127.0.0.1:5173/api/health
```

## 入口与验证

- 员工广场：`http://127.0.0.1:5173/workspace/gallery`
- 管理端：`http://127.0.0.1:5173/enterprise/dashboard`
- Swagger：`http://127.0.0.1:5173/docs`
- 健康接口已验证返回：`{"status":"ok","app":"StaffDeck"}`
- 已通过真实浏览器验证 `admin/admin` 登录、员工广场和管理端，控制台无错误
- 前端 TypeScript 与 Vite 构建通过
- 模型内置验证通过：普通文本、流式输出、JSON 均成功
- 真实 AgentLoop 会话返回精确结果 `model-live-ok`，会话 ID：`session_4897022a69ef4852`

## 模型配置

- 名称：`Codex System Model (gpt-5.6-sol)`
- 协议：`openai_chat_completions`
- Base URL：`https://api.404not.fun/v1`
- 状态：`verified`、`enabled`、`default`
- API Key 从本机 Codex `auth.json` 读取后由 StaffDeck 加密存入 SQLite；未写入本文件或 `backend\.env`

该代理会拦截 OpenAI Python SDK 的默认 User-Agent。`backend/app/llm/client.py` 已为 OpenAI 兼容客户端设置 `User-Agent: StaffDeck/0.1`，对应回归断言位于 `backend/tests/test_llm_client.py`。

`backend/app/api/model_configs.py` 已修复模型验证探针未应用 32/32/128 token 上限的问题，复用了源码已有的 `_verification_probe_tokens()`。模型客户端与模型配置 API 测试结果：`68 passed`。

若删除 `backend/skill_agent_loop.db` 重建数据库，需要重新在“管理端 -> 模型配置”中添加模型并执行测试。不要在交接文档或日志中写入 API Key。

## OpenET MCP 验收交接

产品需求文档为 `OPENET_MCP_PRD.md`。实现位于 `backend/app/tools/openet_mcp/`，HTTP 入口为 `/api/mcp/openet`；官方数据目录核验版本为 `2026-07-31`。

### 已确认边界

- 目标平台是 OpenET（开放数字地球，`openet.terraqt.com`），不是同名的美国蒸散量 OpenET。
- 当前按基础版/开发者版、OpenET 全域权限设计；全域为东经 72–137、北纬 17–55，不是全球。
- 第一版只使用开源数据集；不包含 CMA 气象站与预警、TQAI、EC 商业数据或数据推送导出。
- 三个单点工具支持 `location` 中国地点名称，MCP 内部使用 Nominatim 解析；终端用户不提供经纬度，歧义时只补充省、市或区县。已有坐标的系统调用仍兼容 `lon`/`lat`。
- 使用现有 FastAPI 端口上的 `streamable_http` MCP，不新增进程、端口或依赖。当前 URL 为 `http://localhost:5173/api/mcp/openet`。
- 路由要求由 `APP_SECRET` 派生的内部服务令牌；StaffDeck 客户端仅对配置的内部 OpenET URL 自动注入，不把令牌写入 SQLite。
- token 只从已忽略的 `backend/.env` 的 `OPENET_API_TOKEN` 读取；StaffDeck MCP 配置中的 `Env JSON` 和 `Headers JSON` 均为空。真实值不得写入源码、SQLite、日志或本文档。
- 首版固定 7 个工具：`list_datasets`、`describe_dataset`、`get_point_forecast`、`get_ensemble_forecast`、`get_point_history`、`get_multi_point_forecast`、`get_area_average_forecast`。
- 查询工具默认使用 `dataset=auto`。LLM 只判断预报、集合、历史及查询目标，MCP 按本地确定性规则选择数据集并返回 `selected_dataset`、`selection_reason` 和备选项；专家可显式覆盖数据集。
- 自动选择一次只调用一个模型，不自动扇出为多模型比较；集合默认只取 `mean`，历史单次最多 7 天，区域查询只允许 `avg=true`。
- 7 个工具目前全部私有绑定给当前名称为“11”的员工，未同步到公共工具广场。

### 已完成实现

- 本地版本化目录包含 13 个首版开源数据集及各数据集官方变量、默认单位和单位转换能力。
- 实现 7 个固定工具、自然语言地点解析、经纬度与体量限制、确定性 `dataset=auto`、显式覆盖、统一返回和结构化错误。
- OpenET MCP 暴露为受内部鉴权保护的 `/api/mcp/openet` Streamable HTTP 路由；协议分发与旧 stdio 兼容入口共享，活动连接不再使用 stdio。
- 上游使用现有 `httpx`，固定调用 `https://api-pro-openet.terraqt.com/v1/{dataset}/...`，无重试、无扇出、无新增依赖或端口。
- `backend/.env.example` 已增加空的 `OPENET_API_TOKEN=""` 和可覆盖的 `OPENET_GEOCODING_URL`；FastAPI 进程自行读取 `backend/.env`。
- `backend/app/tools/mcp_client.py` 修复 Windows stdio 管道不能交给 `select()` 的既有问题，改用带超时的后台管道读取；原有 stdio 发现和探测测试已恢复通过。
- StaffDeck 租户级 `openet` Server 已原位迁移为 `streamable_http`，stdio 启动字段已清空；7 个 Tool 行、Tool ID 和员工私有绑定均保留，`headers/env` 仍为空。
- `answer_only` 会进入第二轮能力选择，模型只能从当前员工可见工具中生成并执行 `tool_call`；同步与流式会话均复用既有权限、审计和响应链。
- 前端工具集操作区宽度已修正；MCP 子工具菜单提供“编辑工具集”“测试”和员工范围“移除”，不允许把服务端发现的子工具当作独立 HTTP 工具编辑。

### 验证结果

- OpenET HTTP、协议与 Swagger 聚焦测试：`54 passed`；OpenET、MCP Server、ToolExecutor、Tool API 与 Swagger 合并回归：`93 passed`。
- 聚焦 Ruff `F401/F821`：通过；`git diff --check`：通过。
- 前端 `i18n:check`：`2124` 条英文映射覆盖通过；TypeScript 与 Vite 构建通过。
- StaffDeck 自身 `streamable_http` client 实际发现 7 个工具，并通过员工权限链调用 `list_datasets`：通过；匿名 HTTP MCP 请求返回 401。
- 敏感扫描：673 个可提交文件、12 个 SQLite/日志运行时文件和 Git diff 中的真实 OpenET token 命中均为 0。
- 真实调用严格停止在约 5 个计费单位：
  - 单点最新预报：`auto -> gfs_surface`，`t2m@C`，成功。
  - 集合均值：`auto -> ens_open`，仅 `mean`，成功；首次响应暴露官方扁平变量名 `t2m_mean`，已据实修复并回归。
  - 一天历史：`auto -> gdas_surface`，`t2m@C`，成功。
- 多点与区域接口未做额外真实调用，按 PRD 仅由 mock 上游覆盖。
- 本次 HTTP 迁移只调用本地目录工具，不请求 OpenET 上游、不消耗新增额度。
- 真实 AgentLoop 会话 `session_54b4e4203e8142f0`：用户只说“北京明天天气”，模型调用 `openet.get_point_forecast` 并传 `location=北京`、48 小时范围；MCP 解析到“北京市, 中国”，`auto -> gfs_surface`，返回 49 个时点并生成天气回答。

### 下一步

自然地点查询和员工直接工具调用已经完成。后续重点是按实际职责缩减当前员工的工具子集、设置额度预算，以及决定回答粒度和风险提示；不要把 OpenET 工具同步到公共工具广场。

## 已知源码问题

- 本次相关文件 Ruff `F401/F821` 检查通过。完整 `backend/app` 与 `backend/tests` Ruff 当前仍有 1256 条既有问题，本次按约定未做全库清理。
- 在 Codex 命令会话中，普通 `--detach` 子进程曾被宿主回收；隐藏 `scripts/dev.py up` 仍是已验证的独立启动方案。
- 2026-08-03 本次复核时，隐藏 supervisor 和单端口 app 均在运行，`scripts/dev.py status` 显示 5173 listening 且健康检查正常。
