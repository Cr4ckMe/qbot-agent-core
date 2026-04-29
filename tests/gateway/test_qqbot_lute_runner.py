from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.base import MessageEvent
from gateway.qqbot_config import QQBotAccessConfig, QQBotCapabilitiesConfig, QQBotCLIConfig, QQBotConfig
from gateway.session import SessionSource


QQ_ADMIN = '1003'
QQ_ALLOWED_USER = '1010'


def _make_runner():
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(
        platforms={
            Platform.QQBOT: PlatformConfig(
                enabled=True,
                token='test',
                extra={'http_api': 'http://127.0.0.1:3000', 'ws_host': '127.0.0.1', 'ws_port': 18800, 'self_id': '1007'},
            )
        },
        qqbot=QQBotConfig(
            enabled=True,
            platform='napcat',
            access=QQBotAccessConfig(admins=[QQ_ADMIN], allow_users=[QQ_ALLOWED_USER], allow_groups=['1004']),
            capabilities=QQBotCapabilitiesConfig(predefined_commands_for_users=True),
            cli=QQBotCLIConfig(enable_legacy_bare_commands=True),
        ),
    )
    runner.adapters = {Platform.QQBOT: SimpleNamespace(send=AsyncMock())}
    runner.pairing_store = SimpleNamespace(
        is_approved=MagicMock(return_value=False),
        _is_rate_limited=MagicMock(return_value=False),
        generate_code=MagicMock(return_value='PAIR123'),
        _record_rate_limit=MagicMock(),
    )
    runner.session_store = None
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


def _make_event(text: str, *, user_id: str) -> MessageEvent:
    source = SessionSource(
        platform=Platform.QQBOT,
        chat_id=user_id,
        chat_type='dm',
        user_id=user_id,
        user_name='tester',
    )
    return MessageEvent(text=text, source=source, message_id='m1')


@pytest.mark.asyncio
async def test_handle_message_routes_lute_status_for_allowed_qqbot_user():
    runner = _make_runner()

    result = await runner._handle_message(_make_event('/lute status', user_id=QQ_ALLOWED_USER))

    assert result is not None
    assert result.startswith('Lute status')
    runner._handle_message_with_agent.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_message_routes_admin_status_for_qqbot_admin():
    runner = _make_runner()

    result = await runner._handle_message(_make_event('/admin status', user_id=QQ_ADMIN))

    assert result is not None
    assert 'QQ admin status' in result
    assert QQ_ADMIN in result
    runner._handle_message_with_agent.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_message_executes_lute_admin_command_text_via_admin_router():
    runner = _make_runner()

    result = await runner._handle_message(_make_event('/lute system runtime show', user_id=QQ_ADMIN))

    assert result is not None
    assert 'QQ admin runtime' in result
    runner._handle_message_with_agent.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_adapter_message_routes_lute_help_via_structured_delivery_without_image_path_text():
    runner = _make_runner()
    runner._send_gateway_response = AsyncMock(return_value=None)

    result = await runner._handle_adapter_message(_make_event('/lute help', user_id=QQ_ALLOWED_USER))

    assert result is None
    runner._send_gateway_response.assert_awaited_once()
    delivered = runner._send_gateway_response.await_args.args[2]
    assert getattr(delivered, 'view', None) is not None
    assert getattr(delivered, 'text', None) == ''


@pytest.mark.asyncio
async def test_handle_adapter_message_routes_bare_lute_via_structured_delivery_without_marker_text():
    runner = _make_runner()
    runner._send_gateway_response = AsyncMock(return_value=None)

    result = await runner._handle_adapter_message(_make_event('/lute', user_id=QQ_ALLOWED_USER))

    assert result is None
    runner._send_gateway_response.assert_awaited_once()
    delivered = runner._send_gateway_response.await_args.args[2]
    assert getattr(delivered, 'view', None) is not None
    assert getattr(delivered, 'text', None) == ''


@pytest.mark.asyncio
async def test_handle_adapter_message_routes_lute_menu_via_structured_delivery_without_marker_text():
    runner = _make_runner()
    runner._send_gateway_response = AsyncMock(return_value=None)

    result = await runner._handle_adapter_message(_make_event('/lute menu', user_id=QQ_ALLOWED_USER))

    assert result is None
    runner._send_gateway_response.assert_awaited_once()
    delivered = runner._send_gateway_response.await_args.args[2]
    assert getattr(delivered, 'view', None) is not None
    assert getattr(delivered, 'text', None) == ''
