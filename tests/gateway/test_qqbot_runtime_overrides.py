import asyncio
import sys
import threading
import types
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import gateway.run as gateway_run
from gateway.config import Platform
from gateway.platforms.base import MessageEvent
from gateway.qqbot_config import QQBotConfig
from gateway.session import SessionSource


class _CapturingAgent:
    last_init = None

    def __init__(self, *args, **kwargs):
        type(self).last_init = dict(kwargs)
        self.tools = []

    def run_conversation(self, user_message, conversation_history=None, task_id=None, persist_user_message=None):
        return {
            "final_response": "ok",
            "messages": [],
            "api_calls": 1,
            "completed": True,
        }


def _install_fake_agent(monkeypatch):
    fake_run_agent = types.ModuleType("run_agent")
    fake_run_agent.AIAgent = _CapturingAgent
    monkeypatch.setitem(sys.modules, "run_agent", fake_run_agent)


def _make_runner(*, qqbot_config: QQBotConfig, smart_model_routing: dict | None = None):
    runner = object.__new__(gateway_run.GatewayRunner)
    runner.adapters = {}
    runner._ephemeral_system_prompt = ""
    runner._prefill_messages = []
    runner._reasoning_config = None
    runner._service_tier = None
    runner._provider_routing = {}
    runner._fallback_model = None
    runner._smart_model_routing = smart_model_routing or {}
    runner._running_agents = {}
    runner._pending_model_notes = {}
    runner._session_db = None
    runner._agent_cache = {}
    runner._agent_cache_lock = threading.Lock()
    runner._session_model_overrides = {}
    runner.hooks = SimpleNamespace(loaded_hooks=False)
    runner.config = SimpleNamespace(streaming=None, qqbot=qqbot_config)
    runner.session_store = SimpleNamespace(
        get_or_create_session=lambda source: SimpleNamespace(session_id="session-1"),
        load_transcript=lambda session_id: [],
    )
    runner._get_or_create_gateway_honcho = lambda session_key: (None, None)
    runner._enrich_message_with_vision = AsyncMock(return_value="ENRICHED")
    return runner


def _make_source(platform: Platform) -> SessionSource:
    return SessionSource(
        platform=platform,
        chat_id="12345",
        chat_type="dm",
        user_id="user-1",
    )


async def _run_agent_for_source(runner, source: SessionSource):
    return await runner._run_agent(
        message="hello",
        context_prompt="",
        history=[],
        source=source,
        session_id="session-1",
        session_key=f"agent:main:{source.platform.value}:dm:{source.chat_id}",
    )


def _runtime_kwargs():
    return {
        "provider": "openrouter",
        "api_mode": "chat_completions",
        "base_url": "https://openrouter.ai/api/v1",
        "api_key": "***",
        "command": None,
        "args": [],
        "credential_pool": None,
    }


def test_run_agent_uses_qqbot_runtime_model_override(monkeypatch):
    _install_fake_agent(monkeypatch)
    runner = _make_runner(
        qqbot_config=QQBotConfig.from_dict(
            {
                "enabled": True,
                "platform": "napcat",
                "runtime": {
                    "mode": "qqbot",
                    "model": "qwen/qq-fast",
                },
            }
        )
    )

    monkeypatch.setattr(gateway_run, "_load_gateway_config", lambda: {})
    monkeypatch.setattr(gateway_run, "_env_path", gateway_run._hermes_home / ".env")
    monkeypatch.setattr(gateway_run, "load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.setattr(gateway_run, "_resolve_gateway_model", lambda config=None: "gpt-5.4")
    monkeypatch.setattr(gateway_run, "_resolve_runtime_agent_kwargs", _runtime_kwargs)

    import hermes_cli.tools_config as tools_config

    monkeypatch.setattr(tools_config, "_get_platform_tools", lambda user_config, platform_key: {"web"})

    _CapturingAgent.last_init = None
    result = asyncio.run(_run_agent_for_source(runner, _make_source(Platform.QQBOT)))

    assert result["final_response"] == "ok"
    assert _CapturingAgent.last_init["model"] == "qwen/qq-fast"


def test_run_agent_uses_qqbot_runtime_toolset_override(monkeypatch):
    _install_fake_agent(monkeypatch)
    runner = _make_runner(
        qqbot_config=QQBotConfig.from_dict(
            {
                "enabled": True,
                "platform": "napcat",
                "runtime": {
                    "mode": "qqbot",
                    "enabled_toolsets": ["qq", "memory"],
                },
            }
        )
    )

    monkeypatch.setattr(gateway_run, "_load_gateway_config", lambda: {})
    monkeypatch.setattr(gateway_run, "_env_path", gateway_run._hermes_home / ".env")
    monkeypatch.setattr(gateway_run, "load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.setattr(gateway_run, "_resolve_gateway_model", lambda config=None: "gpt-5.4")
    monkeypatch.setattr(gateway_run, "_resolve_runtime_agent_kwargs", _runtime_kwargs)

    import hermes_cli.tools_config as tools_config

    monkeypatch.setattr(tools_config, "_get_platform_tools", lambda user_config, platform_key: {"web"})

    _CapturingAgent.last_init = None
    result = asyncio.run(_run_agent_for_source(runner, _make_source(Platform.QQBOT)))

    assert result["final_response"] == "ok"
    assert set(_CapturingAgent.last_init["enabled_toolsets"]) == {"qq", "memory"}


def test_other_platforms_are_unaffected_by_qqbot_runtime_overrides(monkeypatch):
    _install_fake_agent(monkeypatch)
    runner = _make_runner(
        qqbot_config=QQBotConfig.from_dict(
            {
                "enabled": True,
                "platform": "napcat",
                "runtime": {
                    "mode": "qqbot",
                    "model": "qwen/qq-fast",
                    "enabled_toolsets": ["qq", "memory"],
                },
            }
        )
    )

    monkeypatch.setattr(gateway_run, "_load_gateway_config", lambda: {})
    monkeypatch.setattr(gateway_run, "_env_path", gateway_run._hermes_home / ".env")
    monkeypatch.setattr(gateway_run, "load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.setattr(gateway_run, "_resolve_gateway_model", lambda config=None: "gpt-5.4")
    monkeypatch.setattr(gateway_run, "_resolve_runtime_agent_kwargs", _runtime_kwargs)

    import hermes_cli.tools_config as tools_config

    monkeypatch.setattr(tools_config, "_get_platform_tools", lambda user_config, platform_key: {"web"})

    _CapturingAgent.last_init = None
    result = asyncio.run(_run_agent_for_source(runner, _make_source(Platform.TELEGRAM)))

    assert result["final_response"] == "ok"
    assert _CapturingAgent.last_init["model"] == "gpt-5.4"
    assert set(_CapturingAgent.last_init["enabled_toolsets"]) == {"web"}


def test_turn_route_uses_smart_routing_flag_from_qqbot_runtime_block():
    runner = _make_runner(
        qqbot_config=QQBotConfig.from_dict(
            {
                "enabled": True,
                "platform": "napcat",
                "runtime": {
                    "mode": "qqbot",
                    "model": "qwen/qq-fast",
                    "provider": "openrouter",
                    "enable_smart_routing": True,
                },
            }
        ),
        smart_model_routing={"enabled": False},
    )
    runtime_kwargs = _runtime_kwargs()
    captured = {}

    def spy_resolve(user_message, routing_config, primary):
        captured["routing_config"] = routing_config
        return {
            "model": primary["model"],
            "runtime": dict(runtime_kwargs),
            "label": None,
            "signature": (
                primary["model"],
                primary["provider"],
                primary["base_url"],
                primary["api_mode"],
                primary["command"],
                tuple(primary["args"]),
            ),
        }

    with patch("agent.smart_model_routing.resolve_turn_route", side_effect=spy_resolve):
        route = gateway_run.GatewayRunner._resolve_turn_agent_config(
            runner,
            "hello",
            "gpt-5.4",
            runtime_kwargs,
            source=_make_source(Platform.QQBOT),
        )

    assert route["model"] == "gpt-5.4"
    assert captured["routing_config"]["enabled"] is True
    assert captured["routing_config"]["cheap_model"]["model"] == "qwen/qq-fast"
    assert captured["routing_config"]["cheap_model"]["provider"] == "openrouter"
