import sys
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.api.tools import (
    create_mcp_server,
    delete_mcp_server,
    discover_mcp_tools,
    discover_mcp_tools_adhoc,
    get_mcp_tool_inventory,
    list_mcp_servers,
    list_tools,
    sync_mcp_tools,
)
from app.core import AgentLoop
from app.core.step_agent import StepAgent
from app.db.models import ChatSession, MCPServer, ModelConfig, Skill, Tenant, Tool, User
from app.db.models import AgentProfile, AgentResourceBinding
from app.llm import LLMClient
from app.security.encryption import encrypt_secret
from app.tools.tool_executor import ToolExecutor
from app.tools.tool_schema import (
    MCPDiscoverRequest,
    MCPServerConnection,
    MCPServerCreateRequest,
    MCPSyncRequest,
    ToolCall,
)


def _admin_user() -> User:
    return User(id="user_admin", tenant_id="tenant_demo", username="ops", role="admin", password_hash="test")


def _member_user() -> User:
    return User(id="user_member", tenant_id="tenant_demo", username="member", role="member", password_hash="test")


def test_discover_builtin_mcp_server_lists_tools() -> None:
    with _test_session() as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        db.commit()

        response = discover_mcp_tools_adhoc(
            MCPDiscoverRequest(
                tenant_id="tenant_demo",
                connection=MCPServerConnection(transport="builtin"),
            ),
            db,
            _member_user(),
        )

        assert response.success is True
        names = {tool.name for tool in response.tools}
        assert {"echo", "sum", "product_lookup"} <= names
        echo = next(tool for tool in response.tools if tool.name == "echo")
        assert echo.input_schema["properties"]["text"]["type"] == "string"


def test_discover_stdio_mcp_server_lists_tools() -> None:
    with _test_session() as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        db.commit()

        response = discover_mcp_tools_adhoc(
            MCPDiscoverRequest(
                tenant_id="tenant_demo",
                connection=MCPServerConnection(
                    transport="stdio",
                    command=sys.executable,
                    args=[str(_mock_mcp_server_path())],
                ),
            ),
            db,
            _member_user(),
        )

        assert response.success is True
        names = {tool.name for tool in response.tools}
        assert {"echo", "sum", "product_lookup"} <= names


def test_sync_mcp_tools_imports_tools_and_executes() -> None:
    with _test_session() as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        db.add(AgentProfile(id="agent_overall", tenant_id="tenant_demo", name="整体智能体", is_overall=True))
        db.commit()

        server = create_mcp_server(
            MCPServerCreateRequest(
                tenant_id="tenant_demo",
                name="builtin_demo",
                display_name="内置 Demo MCP",
                connection=MCPServerConnection(transport="builtin"),
            ),
            db,
            _admin_user(),
        )

        sync = sync_mcp_tools(
            server.id,
            MCPSyncRequest(tenant_id="tenant_demo", tool_names=["echo"]),
            db,
            current_user=_admin_user(),
        )

        assert sync.success is True
        assert sync.imported == ["echo"]

        tools = db.exec(select(Tool).where(Tool.mcp_server_id == server.id)).all()
        assert len(tools) == 1
        imported = tools[0]
        assert imported.name == "builtin_demo.echo"
        assert imported.tool_type == "mcp"
        assert imported.config_json == {"tool": "echo"}
        assert imported.input_schema["properties"]["text"]["type"] == "string"
        # display_name 应为工具名（leaf），不能是描述文本（否则列表里名字/描述会叠加）。
        assert imported.display_name == "echo"
        assert imported.description and imported.description != imported.display_name
        # 同步的工具应建立 open gallery 绑定，才能在工具广场列表中可见。
        binding = db.exec(
            select(AgentResourceBinding).where(
                AgentResourceBinding.tenant_id == "tenant_demo",
                AgentResourceBinding.resource_type == "tool",
                AgentResourceBinding.resource_id == imported.id,
            )
        ).first()
        assert binding is not None
        # 端到端：工具广场列表应能查到这个同步进来的工具。
        listed = list_tools(tenant_id="tenant_demo", bucket=None, agent_id="agent_overall", db=db)
        assert any(item.name == "builtin_demo.echo" for item in listed)

        result = ToolExecutor(db).execute(
            tenant_id="tenant_demo",
            tool_call=ToolCall(name="builtin_demo.echo", arguments={"text": "hi"}),
        )
        assert result.success is True
        assert result.data == {"text": "hi", "length": 2}


def test_call_mcp_toolset_selects_a_leaf_tool_and_executes_end_to_end(monkeypatch) -> None:
    with _test_session() as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        agent = AgentProfile(
            id="agent_mcp_demo",
            tenant_id="tenant_demo",
            name="MCP 演示员工",
            is_overall=True,
        )
        db.add(agent)
        db.commit()

        server = create_mcp_server(
            MCPServerCreateRequest(
                tenant_id="tenant_demo",
                name="builtin_demo_group",
                display_name="内置 Demo MCP",
                connection=MCPServerConnection(transport="builtin"),
            ),
            db,
            _admin_user(),
        )
        skill = Skill(
            tenant_id="tenant_demo",
            skill_id="demo_mcp_group_skill",
            name="Demo MCP 工具集技能",
            status="published",
            content_json={
                "skill_id": "demo_mcp_group_skill",
                "name": "Demo MCP 工具集技能",
                "version": "1.0.0",
                "start_node_id": "call_demo",
                "nodes": [
                    {
                        "node_id": "call_demo",
                        "name": "调用 Demo MCP",
                        "type": "tool_call",
                        "allowed_actions": [f"call_mcp:{server.id}"],
                    }
                ],
                "edges": [],
            },
        )
        db.add(skill)
        db.commit()

        sync = sync_mcp_tools(
            server.id,
            MCPSyncRequest(tenant_id="tenant_demo", tool_names=["echo", "sum"]),
            db,
            current_user=_admin_user(),
        )
        assert sync.success is True

        loop = AgentLoop(db)
        visible_tools = loop._list_enabled_tools("tenant_demo", agent.id)  # noqa: SLF001
        scoped_tools = loop._step_agent_tools(  # noqa: SLF001
            skill,
            visible_tools,
            active_step_id="call_demo",
        )
        assert {tool.name for tool in scoped_tools} == {
            "builtin_demo_group.echo",
            "builtin_demo_group.sum",
        }
        assert all(skill.skill_id in tool.allowed_skills_json for tool in scoped_tools)

        def fake_generate_json(self, system_prompt, payload):  # noqa: ANN001
            available_tools = payload["available_tools"]
            assert {tool["name"] for tool in available_tools} == {
                "builtin_demo_group.echo",
                "builtin_demo_group.sum",
            }
            return {
                "action": "call_tool",
                "tool_call": {
                    "name": "builtin_demo_group.echo",
                    "arguments": {"text": "hello MCP"},
                },
            }

        monkeypatch.setattr(LLMClient, "__init__", lambda self, model_config: None)
        monkeypatch.setattr(LLMClient, "generate_json", fake_generate_json)
        selected = StepAgent().run(
            "请使用 Demo MCP 回显 hello MCP",
            ChatSession(
                id="session_mcp_demo",
                tenant_id="tenant_demo",
                user_id="user_demo",
                active_skill_id=skill.skill_id,
                active_step_id="call_demo",
            ),
            skill,
            scoped_tools,
            ModelConfig(
                tenant_id="tenant_demo",
                name="Fake model",
                api_key_encrypted=encrypt_secret("test-key"),
                model="fake",
                enabled=True,
            ),
        )

        assert selected.tool_call is not None
        result = ToolExecutor(db).execute(
            tenant_id="tenant_demo",
            tool_call=selected.tool_call,
            active_skill_id=skill.skill_id,
            agent_id=agent.id,
        )
        assert result.success is True
        assert result.data == {"text": "hello MCP", "length": 9}

        server_row = db.get(MCPServer, server.id)
        assert server_row is not None
        server_row.enabled = False
        db.add(server_row)
        db.commit()
        assert loop._list_enabled_tools("tenant_demo", agent.id) == []  # noqa: SLF001
        disabled_result = ToolExecutor(db).execute(
            tenant_id="tenant_demo",
            tool_call=selected.tool_call,
            active_skill_id=skill.skill_id,
            agent_id=agent.id,
        )
        assert disabled_result.success is False
        assert disabled_result.error is not None
        assert disabled_result.error.code == "MCP_ERROR"
        assert "未启用" in disabled_result.error.message


def test_mcp_server_counts_and_cached_inventory_are_scope_aware(monkeypatch) -> None:
    with _test_session() as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        db.add(AgentProfile(id="agent_overall", tenant_id="tenant_demo", name="整体智能体", is_overall=True))
        db.add(AgentProfile(id="agent_employee", tenant_id="tenant_demo", name="数字员工", is_overall=False))
        db.commit()

        server = create_mcp_server(
            MCPServerCreateRequest(
                tenant_id="tenant_demo",
                name="builtin_demo",
                connection=MCPServerConnection(transport="builtin"),
            ),
            db,
            _admin_user(),
        )
        sync_mcp_tools(
            server.id,
            MCPSyncRequest(tenant_id="tenant_demo", tool_names=["echo"]),
            db,
            agent_id="agent_employee",
            current_user=_admin_user(),
        )

        listed = list_mcp_servers("tenant_demo", db)
        assert len(listed) == 1
        assert listed[0].available_tool_count == 3
        assert listed[0].tool_count == 1

        def fail_if_remote_called(*_args, **_kwargs):
            raise AssertionError("cached inventory must not call tools/list")

        monkeypatch.setattr("app.api.tools.list_mcp_tools", fail_if_remote_called)
        employee_inventory = get_mcp_tool_inventory(
            server.id, "tenant_demo", "agent_employee", db
        )
        assert employee_inventory.cache_available is True
        assert employee_inventory.available_count == 3
        assert employee_inventory.imported_count == 1
        assert employee_inventory.current_scope_count == 1
        assert employee_inventory.current_scope_is_overall is False
        by_name = {tool.name: tool for tool in employee_inventory.tools}
        assert by_name["echo"].imported is True
        assert by_name["echo"].in_current_scope is True
        assert by_name["sum"].imported is False
        assert by_name["sum"].in_current_scope is False

        overall_inventory = get_mcp_tool_inventory(
            server.id, "tenant_demo", "agent_overall", db
        )
        assert overall_inventory.imported_count == 1
        assert overall_inventory.current_scope_count == 1
        assert overall_inventory.current_scope_is_overall is True
        assert {tool.name for tool in overall_inventory.tools if tool.in_current_scope} == {
            "echo"
        }

        persisted = db.get(MCPServer, server.id)
        persisted.discovered_tools_json = [
            tool
            for tool in persisted.discovered_tools_json
            if tool.get("name") != "echo"
        ]
        db.add(persisted)
        db.commit()
        stale_cache_inventory = get_mcp_tool_inventory(
            server.id, "tenant_demo", "agent_employee", db
        )
        assert stale_cache_inventory.available_count == 2
        assert stale_cache_inventory.imported_count == 1
        assert stale_cache_inventory.current_scope_count == 1


def test_mcp_inventory_without_cache_reports_unknown(monkeypatch) -> None:
    with _test_session() as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        db.commit()
        server = create_mcp_server(
            MCPServerCreateRequest(
                tenant_id="tenant_demo",
                name="new_server",
                connection=MCPServerConnection(transport="builtin"),
            ),
            db,
            _admin_user(),
        )

        def fail_if_remote_called(*_args, **_kwargs):
            raise AssertionError("empty inventory must not call tools/list")

        monkeypatch.setattr("app.api.tools.list_mcp_tools", fail_if_remote_called)
        inventory = get_mcp_tool_inventory(server.id, "tenant_demo", None, db)
        assert inventory.cache_available is False
        assert inventory.available_count is None
        assert inventory.imported_count == 0
        assert inventory.current_scope_count == 0
        assert inventory.tools == []


def test_successful_empty_discovery_is_cached(monkeypatch) -> None:
    with _test_session() as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        db.commit()
        server = create_mcp_server(
            MCPServerCreateRequest(
                tenant_id="tenant_demo",
                name="empty_server",
                connection=MCPServerConnection(transport="builtin"),
            ),
            db,
            _admin_user(),
        )
        monkeypatch.setattr("app.api.tools.list_mcp_tools", lambda *_args, **_kwargs: [])

        discovered = discover_mcp_tools(
            server.id,
            MCPDiscoverRequest(tenant_id="tenant_demo"),
            db,
            _admin_user(),
        )

        assert discovered.success is True
        assert discovered.tools == []
        listed = list_mcp_servers("tenant_demo", db)
        assert listed[0].available_tool_count == 0
        inventory = get_mcp_tool_inventory(server.id, "tenant_demo", None, db)
        assert inventory.cache_available is True
        assert inventory.available_count == 0


def test_failed_discovery_preserves_cached_inventory(monkeypatch) -> None:
    with _test_session() as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        db.commit()
        server = create_mcp_server(
            MCPServerCreateRequest(
                tenant_id="tenant_demo",
                name="builtin_demo",
                connection=MCPServerConnection(transport="builtin"),
            ),
            db,
            _admin_user(),
        )
        discovered = discover_mcp_tools(
            server.id,
            MCPDiscoverRequest(tenant_id="tenant_demo"),
            db,
            _admin_user(),
        )
        assert discovered.success is True
        cached_before = list(db.get(MCPServer, server.id).discovered_tools_json)

        def fail_discovery(*_args, **_kwargs):
            raise RuntimeError("provider unavailable")

        monkeypatch.setattr("app.api.tools.list_mcp_tools", fail_discovery)
        failed = discover_mcp_tools(
            server.id,
            MCPDiscoverRequest(tenant_id="tenant_demo"),
            db,
            _admin_user(),
        )
        assert failed.success is False
        assert db.get(MCPServer, server.id).discovered_tools_json == cached_before


def test_sync_mcp_tools_preserves_execution_policy() -> None:
    with _test_session() as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        server = MCPServer(
            id="server_builtin_policy",
            tenant_id="tenant_demo",
            name="builtin-policy",
            transport="builtin",
        )
        db.add(server)
        db.add(
            Tool(
                id="tool_policy",
                tenant_id="tenant_demo",
                name="mcp.builtin-policy.echo",
                tool_type="mcp",
                method="POST",
                url="mcp://builtin-policy/echo",
                mcp_server_id=server.id,
                config_json={"tool": "echo", "execution": {"timeout_seconds": 20}},
            )
        )
        db.commit()

        sync_mcp_tools(
            server.id,
            MCPSyncRequest(tenant_id="tenant_demo", tool_names=["echo"]),
            db,
            current_user=_admin_user(),
        )

        tool = db.get(Tool, "tool_policy")
        assert tool is not None
        assert tool.config_json == {"tool": "echo", "execution": {"timeout_seconds": 20}}


def test_sync_mcp_tools_scoped_to_employee_binds_privately() -> None:
    with _test_session() as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        db.add(AgentProfile(id="agent_overall", tenant_id="tenant_demo", name="整体智能体", is_overall=True))
        db.add(AgentProfile(id="agent_employee", tenant_id="tenant_demo", name="数字员工", is_overall=False))
        db.commit()

        server = create_mcp_server(
            MCPServerCreateRequest(
                tenant_id="tenant_demo",
                name="builtin_demo",
                connection=MCPServerConnection(transport="builtin"),
            ),
            db,
            _admin_user(),
        )

        sync = sync_mcp_tools(
            server.id,
            MCPSyncRequest(tenant_id="tenant_demo", tool_names=["echo"]),
            db,
            agent_id="agent_employee",
            current_user=_admin_user(),
        )
        assert sync.success is True
        assert sync.imported == ["echo"]

        imported = db.exec(select(Tool).where(Tool.mcp_server_id == server.id)).first()
        assert imported is not None

        # 员工范围内同步应建立私有绑定，工具只对该员工可见，不出现在工具广场。
        employee_tools = list_tools(tenant_id="tenant_demo", bucket=None, agent_id="agent_employee", db=db)
        assert any(item.id == imported.id for item in employee_tools)

        plaza_tools = list_tools(tenant_id="tenant_demo", bucket=None, agent_id="agent_overall", db=db)
        assert all(item.id != imported.id for item in plaza_tools)


def test_sync_is_idempotent_and_updates_schema() -> None:
    with _test_session() as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        db.commit()

        server = create_mcp_server(
            MCPServerCreateRequest(
                tenant_id="tenant_demo",
                name="builtin_demo",
                connection=MCPServerConnection(transport="builtin"),
            ),
            db,
            _admin_user(),
        )

        first = sync_mcp_tools(
            server.id,
            MCPSyncRequest(tenant_id="tenant_demo"),
            db,
            current_user=_admin_user(),
        )
        assert first.success is True
        assert len(first.imported) == 3

        second = sync_mcp_tools(
            server.id,
            MCPSyncRequest(tenant_id="tenant_demo"),
            db,
            current_user=_admin_user(),
        )
        assert second.success is True
        assert second.imported == []
        assert set(second.updated) == {"echo", "sum", "product_lookup"}

        tools = db.exec(select(Tool).where(Tool.mcp_server_id == server.id)).all()
        assert len(tools) == 3


def test_discover_saved_server_marks_imported() -> None:
    with _test_session() as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        db.commit()

        server = create_mcp_server(
            MCPServerCreateRequest(
                tenant_id="tenant_demo",
                name="builtin_demo",
                connection=MCPServerConnection(transport="builtin"),
            ),
            db,
            _admin_user(),
        )
        sync_mcp_tools(
            server.id,
            MCPSyncRequest(tenant_id="tenant_demo", tool_names=["echo"]),
            db,
            current_user=_admin_user(),
        )

        response = discover_mcp_tools(
            server.id,
            MCPDiscoverRequest(tenant_id="tenant_demo"),
            db,
            _admin_user(),
        )

        assert response.success is True
        by_name = {tool.name: tool for tool in response.tools}
        assert by_name["echo"].imported is True
        assert by_name["echo"].tool_id is not None
        assert by_name["sum"].imported is False


def test_delete_mcp_server_removes_tools() -> None:
    with _test_session() as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        db.commit()

        server = create_mcp_server(
            MCPServerCreateRequest(
                tenant_id="tenant_demo",
                name="builtin_demo",
                connection=MCPServerConnection(transport="builtin"),
            ),
            db,
            _admin_user(),
        )
        sync_mcp_tools(
            server.id,
            MCPSyncRequest(tenant_id="tenant_demo", tool_names=["echo"]),
            db,
            current_user=_admin_user(),
        )

        result = delete_mcp_server(
            server.id,
            "tenant_demo",
            db,
            agent_id=None,
            remove_tools=True,
            current_user=_admin_user(),
        )

        assert result == {"status": "deleted"}
        assert db.get(MCPServer, server.id) is None
        assert len(db.exec(select(Tool).where(Tool.mcp_server_id == server.id)).all()) == 0


def test_delete_mcp_server_in_employee_scope_only_unbinds() -> None:
    with _test_session() as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        db.add(AgentProfile(id="agent_overall", tenant_id="tenant_demo", name="整体智能体", is_overall=True))
        db.add(AgentProfile(id="agent_employee", tenant_id="tenant_demo", name="数字员工", is_overall=False))
        db.commit()

        server = create_mcp_server(
            MCPServerCreateRequest(
                tenant_id="tenant_demo",
                name="builtin_demo",
                connection=MCPServerConnection(transport="builtin"),
            ),
            db,
            _admin_user(),
        )
        sync_mcp_tools(
            server.id,
            MCPSyncRequest(tenant_id="tenant_demo", tool_names=["echo"]),
            db,
            agent_id="agent_employee",
            current_user=_admin_user(),
        )

        result = delete_mcp_server(
            server.id,
            "tenant_demo",
            db,
            agent_id="agent_employee",
            remove_tools=True,
            current_user=_admin_user(),
        )

        assert result == {"status": "hidden"}
        # 工具集与工具行都是租户级资产,员工范围内的"移除"不得删除它们
        assert db.get(MCPServer, server.id) is not None
        assert len(db.exec(select(Tool).where(Tool.mcp_server_id == server.id)).all()) == 1
        assert list_tools(tenant_id="tenant_demo", bucket=None, agent_id="agent_employee", db=db) == []


def test_resync_restores_tools_removed_from_employee() -> None:
    """移除是可逆的:再次同步必须把工具装回来,否则私有同步的工具会永久失联。"""
    with _test_session() as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        db.add(AgentProfile(id="agent_overall", tenant_id="tenant_demo", name="整体智能体", is_overall=True))
        db.add(AgentProfile(id="agent_employee", tenant_id="tenant_demo", name="数字员工", is_overall=False))
        db.commit()

        server = create_mcp_server(
            MCPServerCreateRequest(
                tenant_id="tenant_demo",
                name="builtin_demo",
                connection=MCPServerConnection(transport="builtin"),
            ),
            db,
            _admin_user(),
        )
        sync_request = MCPSyncRequest(tenant_id="tenant_demo", tool_names=["echo"])
        sync_mcp_tools(server.id, sync_request, db, agent_id="agent_employee", current_user=_admin_user())
        delete_mcp_server(
            server.id,
            "tenant_demo",
            db,
            agent_id="agent_employee",
            remove_tools=True,
            current_user=_admin_user(),
        )
        assert list_tools(tenant_id="tenant_demo", bucket=None, agent_id="agent_employee", db=db) == []

        sync_mcp_tools(server.id, sync_request, db, agent_id="agent_employee", current_user=_admin_user())

        restored = list_tools(tenant_id="tenant_demo", bucket=None, agent_id="agent_employee", db=db)
        assert [item.name for item in restored] == ["builtin_demo.echo"]


def test_delete_mcp_server_in_employee_scope_without_tools_returns_404() -> None:
    with _test_session() as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        db.add(AgentProfile(id="agent_employee", tenant_id="tenant_demo", name="数字员工", is_overall=False))
        db.commit()

        server = create_mcp_server(
            MCPServerCreateRequest(
                tenant_id="tenant_demo",
                name="builtin_demo",
                connection=MCPServerConnection(transport="builtin"),
            ),
            db,
            _admin_user(),
        )

        with pytest.raises(HTTPException) as exc:
            delete_mcp_server(
                server.id,
                "tenant_demo",
                db,
                agent_id="agent_employee",
                remove_tools=True,
                current_user=_admin_user(),
            )
        assert exc.value.status_code == 404


def _mock_mcp_server_path() -> Path:
    return Path(__file__).resolve().parents[1] / "mock_servers" / "mcp_stdio_server.py"


def _test_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return Session(engine)
