from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, MagicMock

import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.base import MessageEvent
from gateway.qqbot_config import (
    QQBotAccessConfig,
    QQBotCapabilitiesConfig,
    QQBotCLIConfig,
    QQBotConfig,
    QQBotRuntimeConfig,
)
from gateway.session import SessionSource

QQ_ADMIN = '1003'
QQ_ALLOWED_USER = '1006'
QQ_ALLOWED_GROUP = '1004'
QQ_BOT_SELF_ID = '1007'


@pytest.mark.asyncio
async def test_group_admin_store_persists_and_resets_group_config(monkeypatch, tmp_path):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path / '.hermes'))

    from gateway.qqbot_group_admin_store import QQBotGroupAdminStore

    store = QQBotGroupAdminStore()
    initial = store.get_group_config(QQ_ALLOWED_GROUP)
    assert initial['builtin_ban'] is False
    assert initial['custom_ban_words'] == []
    assert initial['join_welcome'] == ''

    updated = store.update_group_config(
        QQ_ALLOWED_GROUP,
        {
            'builtin_ban': True,
            'custom_ban_words': ['foo', 'bar'],
            'word_ban_time': 180,
            'join_welcome': '欢迎加入',
        },
    )
    assert updated['builtin_ban'] is True
    assert updated['custom_ban_words'] == ['foo', 'bar']
    assert updated['word_ban_time'] == 180
    assert updated['join_welcome'] == '欢迎加入'

    reloaded = QQBotGroupAdminStore()
    persisted = reloaded.get_group_config(QQ_ALLOWED_GROUP)
    assert persisted['builtin_ban'] is True
    assert persisted['custom_ban_words'] == ['foo', 'bar']
    assert persisted['word_ban_time'] == 180
    assert persisted['join_welcome'] == '欢迎加入'

    reset = reloaded.reset_group_config(QQ_ALLOWED_GROUP)
    assert reset['builtin_ban'] is False
    assert reset['custom_ban_words'] == []
    assert reset['word_ban_time'] == 0
    assert reset['join_welcome'] == ''

    store.close()
    reloaded.close()


def _make_group_event(
    text: str,
    *,
    user_id: str = QQ_ALLOWED_USER,
    role: str = 'member',
    message_id: str = 'msg-1',
) -> MessageEvent:
    source = SessionSource(
        platform=Platform.QQBOT,
        chat_id=QQ_ALLOWED_GROUP,
        chat_name='test-group',
        chat_type='group',
        user_id=user_id,
        user_name='tester',
    )
    raw_message = {
        'post_type': 'message',
        'message_type': 'group',
        'group_id': int(QQ_ALLOWED_GROUP),
        'user_id': int(user_id),
        'message_id': message_id,
        'sender': {'user_id': int(user_id), 'nickname': 'tester', 'role': role},
    }
    return MessageEvent(text=text, source=source, message_id=message_id, raw_message=raw_message)


@pytest.mark.asyncio
async def test_group_admin_runtime_custom_word_hit_recalls_and_mutes(monkeypatch, tmp_path):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path / '.hermes'))

    from gateway.qqbot_group_admin_runtime import QQBotGroupAdminRuntime

    calls: list[tuple[str, dict]] = []

    async def fake_action(action: str, params: dict):
        calls.append((action, params))

    runtime = QQBotGroupAdminRuntime(action_executor=fake_action)
    runtime.update_group_config(QQ_ALLOWED_GROUP, {'custom_ban_words': ['广告词'], 'word_ban_time': 120})

    result = await runtime.handle_group_message(_make_group_event('这是广告词内容', message_id='1008'))

    assert result.handled is True
    assert result.actions == ['delete_msg', 'set_group_ban']
    assert calls == [
        ('delete_msg', {'message_id': 1008}),
        ('set_group_ban', {'group_id': int(QQ_ALLOWED_GROUP), 'user_id': int(QQ_ALLOWED_USER), 'duration': 120}),
    ]

    await runtime.shutdown()


@pytest.mark.asyncio
async def test_group_admin_runtime_group_decrease_kick_me_does_not_blacklist_self(monkeypatch, tmp_path):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path / '.hermes'))

    from gateway.qqbot_group_admin_runtime import QQBotGroupAdminRuntime

    runtime = QQBotGroupAdminRuntime(self_id=QQ_BOT_SELF_ID)
    runtime.update_group_config(QQ_ALLOWED_GROUP, {'leave_notify': True, 'kick_block': True})

    payload = {
        'post_type': 'notice',
        'notice_type': 'group_decrease',
        'sub_type': 'kick_me',
        'group_id': int(QQ_ALLOWED_GROUP),
        'user_id': int(QQ_BOT_SELF_ID),
        'operator_id': 1009,
    }

    result = await runtime.handle_napcat_raw_event(payload)

    assert result.handled is False
    assert result.messages == []
    assert runtime.get_group_config(QQ_ALLOWED_GROUP)['block_ids'] == []

    await runtime.shutdown()


def _make_runner() -> object:
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(
        platforms={
            Platform.QQBOT: PlatformConfig(
                enabled=True,
                token='test',
                extra={
                    'http_api': 'http://127.0.0.1:3000',
                    'ws_host': '127.0.0.1',
                    'ws_port': 18800,
                    'self_id': QQ_BOT_SELF_ID,
                },
            )
        },
        qqbot=QQBotConfig(
            enabled=True,
            platform='napcat',
            access=QQBotAccessConfig(
                admins=[QQ_ADMIN],
                allow_users=[QQ_ALLOWED_USER],
                allow_groups=[QQ_ALLOWED_GROUP],
                group_user_allowlist={QQ_ALLOWED_GROUP: [QQ_ALLOWED_USER]},
            ),
            capabilities=QQBotCapabilitiesConfig(predefined_commands_for_users=True),
            cli=QQBotCLIConfig(),
            runtime=QQBotRuntimeConfig(provider='openrouter', model='anthropic/claude-sonnet-4'),
        ),
    )
    runner.adapters = {Platform.QQBOT: SimpleNamespace(send=AsyncMock(), _send_with_retry=AsyncMock())}
    runner.pairing_store = SimpleNamespace(
        is_approved=MagicMock(return_value=False),
        _is_rate_limited=MagicMock(return_value=False),
        generate_code=MagicMock(return_value='PAIR123'),
        _record_rate_limit=MagicMock(),
    )
    runner.session_store = None
    runner.hooks = SimpleNamespace(emit=AsyncMock(), loaded_hooks=False)
    runner._update_prompt_pending = {}
    runner._running_agents = {}
    runner._running_agents_ts = {}
    runner._session_run_generation = {}
    runner._session_run_generation_lock = None
    runner._pending_messages = {}
    runner._pending_approvals = {}
    runner._voice_mode = {}
    runner._draining = False
    runner._handle_message_with_agent = AsyncMock(return_value='agent path')
    return runner


def test_gateway_runner_runtime_injects_llm_provider_and_model():
    from gateway.run import GatewayRunner

    runner = _make_runner()
    runtime = GatewayRunner._get_qqbot_group_admin_runtime(runner)

    assert runtime.llm_provider == 'openrouter'
    assert runtime.llm_model == 'anthropic/claude-sonnet-4'


@pytest.mark.asyncio
async def test_gateway_runner_short_circuits_when_group_admin_runtime_handles_message_for_non_allowlisted_group_member():
    from gateway.run import GatewayRunner

    runner = _make_runner()
    fake_runtime = SimpleNamespace(
        handle_group_message=AsyncMock(return_value=SimpleNamespace(handled=True, actions=['delete_msg'], messages=[])),
        handle_napcat_raw_event=AsyncMock(),
    )
    runner._get_qqbot_group_admin_runtime = MagicMock(return_value=fake_runtime)

    result = await GatewayRunner._handle_message(runner, _make_group_event('命中禁词', user_id='1010'))

    assert result is None
    fake_runtime.handle_group_message.assert_awaited_once()
    runner._handle_message_with_agent.assert_not_awaited()


@pytest.mark.asyncio
async def test_gateway_runner_handles_raw_unmentioned_group_payload_for_group_admin_runtime():
    from gateway.run import GatewayRunner

    runner = _make_runner()
    fake_runtime = SimpleNamespace(
        handle_group_message=AsyncMock(return_value=SimpleNamespace(handled=True, actions=['set_group_ban'], messages=[])),
        handle_napcat_raw_event=AsyncMock(return_value=SimpleNamespace(handled=False, actions=[], messages=[])),
    )
    runner._get_qqbot_group_admin_runtime = MagicMock(return_value=fake_runtime)

    payload = {
        'post_type': 'message',
        'message_type': 'group',
        'group_id': int(QQ_ALLOWED_GROUP),
        'user_id': 1011,
        'message_id': 1012,
        'raw_message': 'hermes批2禁词',
        'message': [{'type': 'text', 'data': {'text': 'hermes批2禁词'}}],
        'sender': {'user_id': 1011, 'nickname': '0x15', 'role': 'member'},
    }

    handled = await GatewayRunner._handle_qqbot_napcat_raw_event(runner, payload)

    assert handled is True
    fake_runtime.handle_napcat_raw_event.assert_awaited_once_with(payload, action_executor=ANY)
    fake_runtime.handle_group_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_gateway_runner_sends_raw_event_messages_back_to_group():
    from gateway.run import GatewayRunner

    runner = _make_runner()
    adapter = SimpleNamespace(send=AsyncMock(), _send_with_retry=AsyncMock())
    runner.adapters = {Platform.QQBOT: adapter}
    fake_runtime = SimpleNamespace(
        handle_group_message=AsyncMock(return_value=SimpleNamespace(handled=False, actions=[], messages=[])),
        handle_napcat_raw_event=AsyncMock(return_value=SimpleNamespace(handled=True, actions=[], messages=['leave notice'])),
    )
    runner._get_qqbot_group_admin_runtime = MagicMock(return_value=fake_runtime)

    payload = {
        'post_type': 'notice',
        'notice_type': 'group_decrease',
        'sub_type': 'leave',
        'group_id': int(QQ_ALLOWED_GROUP),
        'user_id': 1011,
    }

    handled = await GatewayRunner._handle_qqbot_napcat_raw_event(runner, payload)

    assert handled is True
    adapter._send_with_retry.assert_awaited_once_with(
        chat_id=QQ_ALLOWED_GROUP,
        content='leave notice',
        reply_to=None,
        metadata={'chat_type': 'group'},
    )
