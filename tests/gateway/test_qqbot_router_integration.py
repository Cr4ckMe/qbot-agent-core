import asyncio
from textwrap import dedent
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.base import MessageEvent
from gateway.qqbot_config import (
    QQBotAccessConfig,
    QQBotCLIConfig,
    QQBotCapabilitiesConfig,
    QQBotConfig,
    QQBotTestingConfig,
)
from gateway.session import SessionSource

QQ_ADMIN = "1003"
QQ_ALLOWED_USER = "1010"
QQ_UNAUTHORIZED_USER = "1013"


def _make_runner(
    *,
    llm_users=None,
    predefined_commands_for_users=True,
    explicit_denials=False,
    allow_users=None,
    allow_groups=None,
    group_user_allowlist=None,
):
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(
        platforms={
            Platform.QQBOT: PlatformConfig(
                enabled=True,
                token="test",
                extra={
                    "http_api": "http://127.0.0.1:3000",
                    "self_id": "1007",
                    "group_policy": "allowlist",
                    "group_allow_from": ["1004"],
                    "require_mention_in_groups": True,
                },
            )
        },
        qqbot=QQBotConfig(
            enabled=True,
            platform="napcat",
            access=QQBotAccessConfig(
                admins=[QQ_ADMIN],
                allow_users=list(allow_users or [QQ_ALLOWED_USER]),
                allow_groups=list(allow_groups or []),
                group_user_allowlist=dict(group_user_allowlist or {}),
            ),
            capabilities=QQBotCapabilitiesConfig(
                llm_users=list(llm_users or []),
                predefined_commands_for_users=predefined_commands_for_users,
                admin_slash_admin_only=True,
                modify_state_admin_only=True,
            ),
            cli=QQBotCLIConfig(enable_legacy_bare_commands=True),
            testing=QQBotTestingConfig(explicit_denials=explicit_denials),
        ),
    )
    runner.adapters = {
        Platform.QQBOT: SimpleNamespace(send=AsyncMock()),
    }
    runner.pairing_store = SimpleNamespace(
        is_approved=MagicMock(return_value=False),
        _is_rate_limited=MagicMock(return_value=False),
        generate_code=MagicMock(return_value="PAIR123"),
        _record_rate_limit=MagicMock(),
    )
    runner.hooks = SimpleNamespace(emit=AsyncMock(), loaded_hooks=False)
    runner._running_agents = {}
    runner._running_agents_ts = {}
    runner._pending_messages = {}
    runner._pending_approvals = {}
    runner._voice_mode = {}
    runner._draining = False
    runner._handle_help_command = AsyncMock(return_value="built-in help")
    runner._handle_model_command = AsyncMock(return_value="built-in model")
    runner._handle_provider_command = AsyncMock(return_value="built-in provider")
    runner._handle_message_with_agent = AsyncMock(return_value="agent path")
    return runner


def _make_event(text: str, *, user_id: str) -> MessageEvent:
    source = SessionSource(
        platform=Platform.QQBOT,
        chat_id=user_id,
        chat_type="dm",
        user_id=user_id,
        user_name="tester",
    )
    return MessageEvent(text=text, source=source, message_id="m1")


def _make_busy_napcat_adapter(runner):
    from gateway.platforms.napcat import NapCatAdapter

    adapter = NapCatAdapter(runner.config.platforms[Platform.QQBOT])
    adapter.set_message_handler(runner._handle_message)
    adapter.set_busy_session_handler(runner._handle_active_session_busy_message)
    adapter._send_with_retry = AsyncMock()
    runner.adapters[Platform.QQBOT] = adapter
    return adapter


def _make_napcat_payload(*, user_id: str, text: str, chat_type: str = "dm", group_id: str = "1004", mention_bot: bool = False):
    message = []
    if mention_bot:
        message.append({"type": "at", "data": {"qq": "1007"}})
        message.append({"type": "text", "data": {"text": f" {text}"}})
    else:
        message.append({"type": "text", "data": {"text": text}})

    payload = {
        "post_type": "message",
        "message_type": chat_type,
        "user_id": int(user_id),
        "message_id": 99,
        "message": message,
        "sender": {"nickname": "tester", "card": "tester"},
    }
    if chat_type == "group":
        payload["group_id"] = int(group_id)
    return payload


async def _drain_adapter_background_tasks(adapter):
    while getattr(adapter, "_background_tasks", None):
        tasks = list(adapter._background_tasks)
        await asyncio.gather(*tasks)


def _write_profile_config(tmp_path, config_text: str) -> None:
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir(exist_ok=True)
    (hermes_home / "config.yaml").write_text(dedent(config_text).strip() + "\n", encoding="utf-8")


@pytest.mark.asyncio
async def test_ordinary_user_help_is_handled_by_qq_router():
    runner = _make_runner()

    result = await runner._handle_message(_make_event("/help", user_id=QQ_ALLOWED_USER))

    assert result is not None
    assert result.startswith("QQ bot commands:")
    assert "/help" in result
    runner._handle_help_command.assert_not_called()
    runner._handle_message_with_agent.assert_not_called()


@pytest.mark.asyncio
async def test_unauthorized_dm_help_is_blackholed_or_debugged_before_public_router():
    runner = _make_runner(explicit_denials=True)

    result = await runner._maybe_handle_qqbot_command(_make_event("/help", user_id=QQ_UNAUTHORIZED_USER))

    assert result == "should no reply"
    runner._handle_message_with_agent.assert_not_called()


@pytest.mark.asyncio
async def test_unauthorized_dm_ping_is_blackholed_or_debugged_before_public_router():
    runner = _make_runner(explicit_denials=True)

    result = await runner._maybe_handle_qqbot_command(_make_event("/ping", user_id=QQ_UNAUTHORIZED_USER))

    assert result == "should no reply"
    runner._handle_message_with_agent.assert_not_called()


@pytest.mark.asyncio
async def test_ordinary_user_model_is_denied_before_builtin_dispatch():
    runner = _make_runner()

    result = await runner._handle_message(_make_event("/model openai/gpt-4.1", user_id=QQ_ALLOWED_USER))

    assert result is not None
    assert "Permission denied" in result
    assert "/model" in result
    assert "/lute help" in result
    runner._handle_model_command.assert_not_called()
    runner._handle_message_with_agent.assert_not_called()


@pytest.mark.asyncio
async def test_ordinary_user_free_text_is_denied_before_agent_path():
    runner = _make_runner()

    result = await runner._handle_message(_make_event("hello", user_id=QQ_ALLOWED_USER))

    assert result is not None
    assert "LLM access" in result
    assert "/help" in result
    runner._handle_message_with_agent.assert_not_called()


@pytest.mark.asyncio
async def test_free_text_denial_omits_help_hint_when_predefined_commands_are_disabled():
    runner = _make_runner(predefined_commands_for_users=False)

    result = await runner._handle_message(_make_event("hello", user_id=QQ_ALLOWED_USER))

    assert result is not None
    assert "LLM access" in result
    assert "/help" not in result
    assert "predefined commands" not in result.lower()
    assert "conversational access" in result.lower()
    runner._handle_message_with_agent.assert_not_called()


@pytest.mark.asyncio
async def test_llm_granted_user_free_text_reaches_agent_path():
    runner = _make_runner(llm_users=[QQ_ALLOWED_USER])

    result = await runner._handle_message(_make_event("hello", user_id=QQ_ALLOWED_USER))

    assert result == "agent path"
    runner._handle_message_with_agent.assert_awaited_once()


@pytest.mark.asyncio
async def test_admin_free_text_reaches_agent_path():
    runner = _make_runner()

    result = await runner._handle_message(_make_event("hello", user_id=QQ_ADMIN))

    assert result == "agent path"
    runner._handle_message_with_agent.assert_awaited_once()


@pytest.mark.asyncio
async def test_real_ingress_dm_event_from_napcat_is_not_marked_internal():
    runner = _make_runner()
    adapter = _make_busy_napcat_adapter(runner)

    event = adapter._build_event_from_payload(
        _make_napcat_payload(user_id=QQ_ALLOWED_USER, text="hello", chat_type="dm")
    )

    assert event is not None
    assert event.internal is False


@pytest.mark.asyncio
async def test_real_ingress_unauthorized_dm_free_text_is_swallowed_or_debugged_before_llm_gate():
    runner = _make_runner(explicit_denials=True, allow_users=[QQ_ADMIN])
    adapter = _make_busy_napcat_adapter(runner)
    event = adapter._build_event_from_payload(
        _make_napcat_payload(user_id=QQ_UNAUTHORIZED_USER, text="hello", chat_type="dm")
    )

    assert event is not None
    await adapter.handle_message(event)
    await _drain_adapter_background_tasks(adapter)

    adapter._send_with_retry.assert_awaited_once()
    assert adapter._send_with_retry.await_args.kwargs["content"] == "should no reply"
    runner._handle_message_with_agent.assert_not_called()


@pytest.mark.asyncio
async def test_real_ingress_unauthorized_group_mention_free_text_is_swallowed_or_debugged_before_llm_gate():
    runner = _make_runner(
        explicit_denials=True,
        allow_users=[QQ_ADMIN],
        allow_groups=["1004"],
        group_user_allowlist={},
    )
    adapter = _make_busy_napcat_adapter(runner)
    event = adapter._build_event_from_payload(
        _make_napcat_payload(
            user_id=QQ_UNAUTHORIZED_USER,
            text="hello",
            chat_type="group",
            mention_bot=True,
        )
    )

    assert event is not None
    await adapter.handle_message(event)
    await _drain_adapter_background_tasks(adapter)

    adapter._send_with_retry.assert_awaited_once()
    assert adapter._send_with_retry.await_args.kwargs["content"] == "should no reply"
    runner._handle_message_with_agent.assert_not_called()


@pytest.mark.asyncio
async def test_real_ingress_admin_group_mention_free_text_reaches_agent_path():
    runner = _make_runner(allow_users=[QQ_ADMIN], allow_groups=["1004"])
    adapter = _make_busy_napcat_adapter(runner)
    event = adapter._build_event_from_payload(
        _make_napcat_payload(
            user_id=QQ_ADMIN,
            text="hello",
            chat_type="group",
            mention_bot=True,
        )
    )

    assert event is not None
    await adapter.handle_message(event)
    await _drain_adapter_background_tasks(adapter)

    runner._handle_message_with_agent.assert_awaited_once()
    adapter._send_with_retry.assert_awaited_once()
    assert adapter._send_with_retry.await_args.kwargs["content"] == "agent path"


@pytest.mark.asyncio
async def test_admin_reload_refreshes_live_napcat_trigger_settings(tmp_path, monkeypatch):
    _write_profile_config(
        tmp_path,
        """
        platforms:
          napcat:
            enabled: true
            extra:
              http_api: http://127.0.0.1:3000
              self_id: "1007"
              group_policy: allowlist
              group_allow_from:
                - "1004"
              require_mention_in_groups: true
        qqbot:
          enabled: true
          platform: napcat
          access:
            admins:
              - "1003"
            allow_users:
              - "1003"
            allow_groups:
              - "1004"
        """,
    )
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
    runner = _make_runner(allow_users=[QQ_ADMIN], allow_groups=["1004"])
    adapter = _make_busy_napcat_adapter(runner)

    _write_profile_config(
        tmp_path,
        """
        platforms:
          napcat:
            enabled: true
            extra:
              http_api: http://127.0.0.1:3000
              self_id: "1007"
              group_policy: allowlist
              group_allow_from:
                - "1004"
              require_mention_in_groups: true
        qqbot:
          enabled: true
          platform: napcat
          access:
            admins:
              - "1003"
            allow_users:
              - "1003"
            allow_groups:
              - "1004"
          triggers:
            command_prefixes:
              - "/"
              - "！！"
            group_free_text_require_mention: false
            allow_reply_without_mention: true
        """,
    )

    result = await runner._handle_message(_make_event("/admin reload", user_id=QQ_ADMIN))

    assert result is not None
    assert "reloaded" in result.lower()
    assert adapter.command_prefixes == ["/", "！！"]
    assert adapter.group_free_text_require_mention is False
    assert adapter.allow_reply_without_mention is True
    adapter._known_bot_message_ids.add("42")

    custom_command = adapter._build_event_from_payload(
        _make_napcat_payload(user_id=QQ_ADMIN, text="！！help", chat_type="group")
    )
    free_text = adapter._build_event_from_payload(
        _make_napcat_payload(user_id=QQ_ADMIN, text="hello", chat_type="group")
    )
    reply_text = adapter._build_event_from_payload(
        {
            "post_type": "message",
            "message_type": "group",
            "group_id": 1004,
            "user_id": int(QQ_ADMIN),
            "message_id": 100,
            "message": [
                {"type": "reply", "data": {"id": "42"}},
                {"type": "text", "data": {"text": "hello"}},
            ],
            "sender": {"nickname": "tester", "card": "tester"},
        }
    )

    assert custom_command is not None
    assert custom_command.text == "/help"
    assert free_text is not None
    assert free_text.text == "hello"
    assert reply_text is not None
    assert reply_text.reply_to_message_id == "42"


@pytest.mark.asyncio
async def test_admin_reload_failure_does_not_replace_live_runner_config(tmp_path, monkeypatch):
    _write_profile_config(
        tmp_path,
        """
        platforms:
          napcat:
            enabled: true
            extra:
              http_api: http://127.0.0.1:3000
              self_id: "1007"
              group_policy: allowlist
              group_allow_from:
                - "1004"
              require_mention_in_groups: true
        qqbot:
          enabled: true
          platform: napcat
          access:
            admins:
              - "1003"
            allow_users:
              - "1003"
            allow_groups:
              - "1004"
        """,
    )
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
    runner = _make_runner(allow_users=[QQ_ADMIN], allow_groups=["1004"])
    adapter = _make_busy_napcat_adapter(runner)
    original_prefixes = list(adapter.command_prefixes)
    original_require = adapter.group_free_text_require_mention

    _write_profile_config(
        tmp_path,
        """
        qqbot:
          enabled: true
          platform: telegram
          access:
            admins:
              - "1003"
        """,
    )

    result = await runner._handle_message(_make_event("/admin reload", user_id=QQ_ADMIN))

    assert result is not None
    assert "reload failed" in result.lower()
    assert runner.config.qqbot.platform == "napcat"
    assert adapter.command_prefixes == original_prefixes
    assert adapter.group_free_text_require_mention is original_require


@pytest.mark.asyncio
async def test_admin_model_still_reaches_builtin_slash_path():
    runner = _make_runner()

    result = await runner._handle_message(_make_event("/model openai/gpt-4.1", user_id=QQ_ADMIN))

    assert result == "built-in model"
    runner._handle_model_command.assert_awaited_once()
    runner._handle_message_with_agent.assert_not_called()


@pytest.mark.asyncio
async def test_admin_status_is_handled_by_qq_admin_router():
    runner = _make_runner()

    result = await runner._handle_message(_make_event("/admin status", user_id=QQ_ADMIN))

    assert result is not None
    assert "QQ admin status" in result
    assert QQ_ADMIN in result
    runner._handle_model_command.assert_not_called()
    runner._handle_help_command.assert_not_called()
    runner._handle_message_with_agent.assert_not_called()


@pytest.mark.asyncio
async def test_admin_allow_user_add_updates_live_runner_config_for_next_turn():
    runner = _make_runner()

    result = await runner._handle_message(_make_event("/admin allow user add 1013", user_id=QQ_ADMIN))

    assert result is not None
    assert "1013" in result
    assert "allow_users" in result
    assert "1013" in runner.config.qqbot.access.allow_users

    follow_up = await runner._handle_message(_make_event("/help", user_id="1010"))
    assert follow_up is not None
    assert follow_up.startswith("QQ bot commands:")


@pytest.mark.asyncio
async def test_busy_session_ordinary_user_model_is_still_denied_by_qq_router():
    runner = _make_runner()
    event = _make_event("/model openai/gpt-4.1", user_id=QQ_ALLOWED_USER)
    running_agent = MagicMock()
    runner._running_agents[runner._session_key_for_source(event.source)] = running_agent

    result = await runner._handle_message(event)

    assert result is not None
    assert "Permission denied" in result
    assert "/model" in result
    runner._handle_model_command.assert_not_called()
    runner._handle_message_with_agent.assert_not_called()
    running_agent.interrupt.assert_not_called()
    assert runner._pending_messages == {}


@pytest.mark.asyncio
async def test_busy_session_real_ingress_ordinary_user_model_is_denied_by_qq_router():
    runner = _make_runner()
    adapter = _make_busy_napcat_adapter(runner)
    event = _make_event("/model openai/gpt-4.1", user_id=QQ_ALLOWED_USER)
    session_key = runner._session_key_for_source(event.source)
    adapter._active_sessions[session_key] = asyncio.Event()

    await adapter.handle_message(event)

    adapter._send_with_retry.assert_awaited_once()
    sent = adapter._send_with_retry.await_args.kwargs["content"]
    assert "Permission denied" in sent
    assert "/model" in sent
    runner._handle_model_command.assert_not_called()
    runner._handle_message_with_agent.assert_not_called()


@pytest.mark.asyncio
async def test_busy_session_real_ingress_ordinary_user_free_text_without_llm_is_denied():
    runner = _make_runner()
    adapter = _make_busy_napcat_adapter(runner)
    event = _make_event("hello", user_id=QQ_ALLOWED_USER)
    session_key = runner._session_key_for_source(event.source)
    interrupt_event = asyncio.Event()
    adapter._active_sessions[session_key] = interrupt_event

    await adapter.handle_message(event)

    adapter._send_with_retry.assert_awaited_once()
    sent = adapter._send_with_retry.await_args.kwargs["content"]
    assert "LLM access" in sent
    assert "/help" in sent
    runner._handle_message_with_agent.assert_not_called()
    assert adapter._pending_messages == {}
    assert interrupt_event.is_set() is False


@pytest.mark.asyncio
async def test_busy_session_real_ingress_unauthorized_dm_is_swallowed_or_debugged_without_queueing():
    runner = _make_runner(explicit_denials=True)
    adapter = _make_busy_napcat_adapter(runner)
    event = _make_event("hello", user_id="1010")
    session_key = runner._session_key_for_source(event.source)
    interrupt_event = asyncio.Event()
    adapter._active_sessions[session_key] = interrupt_event

    await adapter.handle_message(event)

    adapter._send_with_retry.assert_awaited_once()
    assert adapter._send_with_retry.await_args.kwargs["content"] == "should no reply"
    runner._handle_message_with_agent.assert_not_called()
    assert adapter._pending_messages == {}
    assert interrupt_event.is_set() is False


@pytest.mark.asyncio
async def test_busy_session_real_ingress_unauthorized_dm_pair_mode_still_blackholes_without_queueing():
    runner = _make_runner()
    runner.config.qqbot.unauthorized_dm_behavior = "pair"
    adapter = _make_busy_napcat_adapter(runner)
    event = _make_event("/help", user_id="1010")
    session_key = runner._session_key_for_source(event.source)
    interrupt_event = asyncio.Event()
    adapter._active_sessions[session_key] = interrupt_event

    await adapter.handle_message(event)

    runner._handle_message_with_agent.assert_not_called()
    assert adapter._pending_messages == {}
    assert interrupt_event.is_set() is False
    adapter._send_with_retry.assert_not_called()
    runner.pairing_store.generate_code.assert_not_called()


@pytest.mark.asyncio
async def test_busy_session_real_ingress_admin_model_uses_builtin_path():
    runner = _make_runner()
    adapter = _make_busy_napcat_adapter(runner)
    event = _make_event("/model openai/gpt-4.1", user_id=QQ_ADMIN)
    session_key = runner._session_key_for_source(event.source)
    adapter._active_sessions[session_key] = asyncio.Event()
    runner._running_agents[session_key] = MagicMock()
    runner._running_agents_ts[session_key] = 0.0

    await adapter.handle_message(event)

    runner._handle_model_command.assert_awaited_once()
    adapter._send_with_retry.assert_awaited_once()
    assert adapter._send_with_retry.await_args.kwargs["content"] == "built-in model"
    assert session_key in runner._running_agents
    assert runner._pending_messages == {}


@pytest.mark.asyncio
async def test_busy_session_real_ingress_admin_provider_alias_uses_builtin_model_path():
    runner = _make_runner()
    adapter = _make_busy_napcat_adapter(runner)
    event = _make_event("/provider openai", user_id=QQ_ADMIN)
    session_key = runner._session_key_for_source(event.source)
    adapter._active_sessions[session_key] = asyncio.Event()
    runner._running_agents[session_key] = MagicMock()
    runner._running_agents_ts[session_key] = 0.0

    await adapter.handle_message(event)

    runner._handle_model_command.assert_awaited_once()
    runner._handle_provider_command.assert_not_called()
    adapter._send_with_retry.assert_awaited_once()
    assert adapter._send_with_retry.await_args.kwargs["content"] == "built-in model"
    assert session_key in runner._running_agents
    assert runner._pending_messages == {}


@pytest.mark.asyncio
async def test_busy_session_real_ingress_admin_plan_does_not_bypass_queue_path():
    runner = _make_runner()
    adapter = _make_busy_napcat_adapter(runner)
    event = _make_event("/plan ship it", user_id=QQ_ADMIN)
    session_key = runner._session_key_for_source(event.source)
    interrupt_event = asyncio.Event()
    adapter._active_sessions[session_key] = interrupt_event
    runner._running_agents[session_key] = MagicMock()
    runner._running_agents_ts[session_key] = 0.0

    await adapter.handle_message(event)

    adapter._send_with_retry.assert_not_called()
    runner._handle_model_command.assert_not_called()
    runner._handle_provider_command.assert_not_called()
    runner._handle_message_with_agent.assert_not_called()
    assert adapter._pending_messages[session_key] is event
    assert interrupt_event.is_set() is True


@pytest.mark.asyncio
async def test_unknown_qq_slash_command_gets_controlled_reply():
    runner = _make_runner()

    result = await runner._handle_message(_make_event("/foo", user_id=QQ_ALLOWED_USER))

    assert result == "Unknown QQ command `/foo`. Use /lute help to see available QQ bot commands."
    runner._handle_help_command.assert_not_called()
    runner._handle_model_command.assert_not_called()
    runner._handle_message_with_agent.assert_not_called()
