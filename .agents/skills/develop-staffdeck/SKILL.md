---
name: develop-staffdeck
description: 在 StaffDeck 仓库内进行编码、重构或代码审查时，遵循现有 FastAPI/SQLModel 与 React/Vite 架构、租户隔离、兼容契约及分层验证。仅在用户显式调用 $develop-staffdeck 时使用。
---

# StaffDeck 开发规范

## 工作方法

- 先读取相关模块、邻近测试、配置和当前 diff，再确定最小改动范围。
- 保留用户未提交的修改；不要顺带清理、改名、格式化或重构无关代码。
- 复用现有类型、辅助函数和模块边界；没有必要时不要增加依赖或抽象。
- 运行状态和启动问题先查看 `NEXT_AGENT_HANDOFF.md`，不要把密钥写入源码、日志或文档。
- 提交时显式列出文件；不要使用 `git add .` 或 `git add -A`，默认排除 `design-*.md`。

## 架构边界

- 将 HTTP 输入、鉴权和响应映射留在 `backend/app/api/`，将可复用业务逻辑放回对应领域模块。
- 将 Agent 编排、状态投影和能力执行保持在现有 `core/`、`capabilities/`、`skills/`、`tools/` 边界内。
- 通过 `llm/` 的统一客户端和协议驱动接入模型；不要让业务层直接耦合单一 SDK。
- 将持久化模型与 SQLite 兼容迁移分别放在 `db/models.py` 和 `db/database.py`。
- 将跨前后端的 Agent 行为兼容要求视为 `contracts/agent/v1/` 的受保护契约。
- 不要仅因 `legacy_*` 命名删除模块；先确认调用链和黄金契约覆盖。

## 后端

- 使用 Python 3.11、`from __future__ import annotations` 和完整参数/返回类型标注。
- 使用 Pydantic 模型表达 API/运行时契约；可变默认值使用 `Field(default_factory=...)`。
- 使用 SQLModel `Session` 和现有查询模式；写入后显式 `commit()`，需要返回最新值时 `refresh()`。
- 使用 `utc_now()`、`new_id()` 和现有领域辅助函数，不另建重复实现。
- 每个租户资源操作都先认证，再校验 `tenant_id`、角色、员工范围和资源归属；复用 `security` 中的 `ensure_*`/`require_*`。
- 使用现有加密模块保存模型及渠道凭据；错误响应不得泄露密钥、令牌或完整敏感配置。
- 修改表结构时同步 SQLModel 字段、幂等 SQLite 增量迁移和迁移回归测试。
- 对流式、重试、渠道和工具副作用保持幂等、可恢复及可审计，避免重复投递或重复执行。

## 前端

- 使用严格 TypeScript、函数组件和 Hooks；复用 `src/types`、`src/api/client.ts`、认证和领域工具函数。
- 优先使用 `@/` 别名；仅在相邻代码明确采用相对路径时保持局部一致。
- 新页面和重构优先从 `@/components/ui` 使用 shadcn/ui；不要新增 Ant Design。
- 使用 `cn()` 合并类名，使用 `notify` 显示通知，并复用 `enterprise-ui` 中的共享样式常量。
- 使用现有 Lucide 图标或 `src/assets/` 下的 SVG 资源；不要在页面 JSX 中手写 SVG 路径。
- 将状态、类型、路由等有限取值集中为枚举或常量，避免散落魔法字符串。
- 通过集中 API 客户端继承认证和错误处理；仅在上传、Blob、外部资源或流式协议确有需要时直接 `fetch`。
- 新增中文界面文案时同步 `src/i18n/en.json`；只处理本次新增的翻译缺口。

## 脚本与验证

- 将跨平台生命周期逻辑集中在 Python；保持 PowerShell/Shell 包装脚本轻量并委托共享入口。
- 后端改动运行相关 pytest；Agent 状态、SSE、事件或会话投影改动同时运行 golden contract 测试。
- 对修改的 Python 文件运行聚焦 Ruff：`--select F401,F821`，不要顺带清理全仓既有告警。
- 前端逻辑运行相关 Vitest；类型、页面或构建配置改动运行 `npm --prefix frontend-enterprise run build`。
- 新增界面文案时运行 `npm --prefix frontend-enterprise run i18n:check`，区分本次问题与既有缺口。
- 开发脚本改动运行 `backend/tests/test_dev_scripts.py`；打包改动运行对应平台的构建或冒烟检查。
- 完成前运行 `git diff --check`，检查限定 diff、实际测试结果和剩余风险后再报告完成。
