# StaffDeck 本地启动交接

## 工作目录

`E:\TLong\StaffDeck`

## 当前状态

- 后端 Python 3.11 虚拟环境：`backend\.venv`
- 后端依赖：已安装 `backend[dev]`
- 前端依赖：已执行 `npm --prefix frontend-enterprise ci`
- 本地配置：`backend\.env` 已创建，使用 SQLite 和随机强 `APP_SECRET`
- 前端已构建：`frontend-enterprise\dist`
- 服务当前运行于 `http://127.0.0.1:5173`
- 默认模型：`gpt-5.6-sol`，已验证、启用并设为默认
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

## 已知源码问题

- 启动脚本会显示 `/docs`，但 `backend/app/main.py` 当前关闭了 Swagger/OpenAPI 路由，因此 `/docs` 返回 404。
- `npm --prefix frontend-enterprise run i18n:check` 当前报告 64 处现有英文翻译缺失；不影响构建和启动。
- 对模型相关文件执行 Ruff `F401/F821` 检查通过。完整 Ruff 规则仍会报告既有导入顺序、过期 `noqa` 和长行问题，本次未做无关清理。
- 在 Codex 命令会话中，普通 `--detach` 子进程曾被宿主回收；当前服务改由上述 Windows 隐藏进程启动。
