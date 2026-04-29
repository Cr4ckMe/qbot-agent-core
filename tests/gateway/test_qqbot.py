"""Tests for the branch-specific qqbot platform adapter.

On this branch, Platform.QQBOT preserves the public platform identity but the
active transport is NapCat / OneBot 11.
"""

from unittest.mock import AsyncMock

import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig


def _make_config(**extra):
    payload = {
        'http_api': 'http://127.0.0.1:3000',
        'ws_host': '127.0.0.1',
        'ws_port': 18800,
        'self_id': '1005',
    }
    payload.update(extra)
    return PlatformConfig(enabled=True, token='test', extra=payload)


def test_connected_platforms_includes_qqbot_when_napcat_transport_fields_present():
    config = GatewayConfig(platforms={Platform.QQBOT: _make_config()})

    assert Platform.QQBOT in config.get_connected_platforms()


def test_gateway_config_maps_legacy_napcat_platform_key_to_qqbot():
    data = {
        'platforms': {
            'napcat': {
                'enabled': True,
                'token': 'test',
                'extra': {
                    'http_api': 'http://127.0.0.1:3000',
                    'ws_host': '127.0.0.1',
                    'ws_port': 18800,
                    'self_id': '1005',
                },
            }
        }
    }

    config = GatewayConfig.from_dict(data)

    assert Platform.QQBOT in config.platforms
    qq = config.platforms[Platform.QQBOT]
    assert qq.enabled is True
    assert qq.token == 'test'
    assert qq.extra['http_api'] == 'http://127.0.0.1:3000'


def test_check_qq_requirements_returns_bool():
    from gateway.platforms.qqbot import check_qq_requirements

    result = check_qq_requirements()
    assert isinstance(result, bool)


def test_adapter_exposes_napcat_transport_attributes():
    from gateway.platforms.qqbot import QQAdapter

    adapter = QQAdapter(_make_config())

    assert adapter.http_api == 'http://127.0.0.1:3000'
    assert adapter.ws_host == '127.0.0.1'
    assert adapter.ws_port == 18800
    assert adapter.self_id == '1005'
    assert adapter.access_token == 'test'
    assert adapter.name == 'QQBot'


def test_coerce_list_supports_string_and_list_inputs():
    from gateway.platforms.qqbot import _coerce_list

    assert _coerce_list('a, b ,c') == ['a', 'b', 'c']
    assert _coerce_list(['x', 'y']) == ['x', 'y']
    assert _coerce_list(None) == []


def test_adapter_parses_private_message_event():
    from gateway.platforms.base import MessageType
    from gateway.platforms.qqbot import QQAdapter

    adapter = QQAdapter(_make_config())

    payload = {
        'post_type': 'message',
        'message_type': 'private',
        'user_id': 1000,
        'message_id': 42,
        'raw_message': 'hello',
        'message': [{'type': 'text', 'data': {'text': 'hello'}}],
        'sender': {'nickname': 'tester'},
    }

    event = adapter._build_event_from_payload(payload)
    assert event is not None
    assert event.text == 'hello'
    assert event.message_type == MessageType.TEXT
    assert event.source.platform == Platform.QQBOT
    assert event.source.chat_type == 'dm'
    assert event.source.chat_id == '1000'
    assert event.source.user_id == '1000'


def test_adapter_parses_group_at_message_event():
    from gateway.platforms.qqbot import QQAdapter

    adapter = QQAdapter(_make_config())

    payload = {
        'post_type': 'message',
        'message_type': 'group',
        'group_id': 8888,
        'user_id': 1000,
        'message_id': 99,
        'raw_message': '@1005 hi',
        'message': [
            {'type': 'at', 'data': {'qq': '1005'}},
            {'type': 'text', 'data': {'text': ' hi'}},
        ],
        'sender': {'nickname': 'tester', 'card': 'Tester Card'},
    }

    event = adapter._build_event_from_payload(payload)
    assert event is not None
    assert event.text == 'hi'
    assert event.source.platform == Platform.QQBOT
    assert event.source.chat_type == 'group'
    assert event.source.chat_id == '8888'
    assert event.source.user_id == '1000'
    assert event.source.user_name == 'Tester Card'


@pytest.mark.asyncio
async def test_adapter_forwards_dropped_unmentioned_group_payload_to_raw_handler():
    from gateway.platforms.qqbot import QQAdapter

    adapter = QQAdapter(
        _make_config(group_free_text_require_mention=True)
    )

    raw_handler = AsyncMock(return_value=True)
    message_handler = AsyncMock()
    adapter.set_raw_event_handler(raw_handler)
    adapter.set_message_handler(message_handler)

    payload = {
        'post_type': 'message',
        'message_type': 'group',
        'group_id': 8888,
        'user_id': 1000,
        'message_id': 100,
        'raw_message': 'hello without mention',
        'message': [{'type': 'text', 'data': {'text': 'hello without mention'}}],
        'sender': {'nickname': 'tester', 'role': 'member'},
    }

    await adapter._process_payload(payload)

    raw_handler.assert_awaited_once_with(payload)
    message_handler.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_group_reply_uses_reply_segment_only(monkeypatch):
    from gateway.platforms.qqbot import QQAdapter

    adapter = QQAdapter(_make_config())

    payload = {
        'post_type': 'message',
        'message_type': 'group',
        'group_id': 8888,
        'user_id': 1000,
        'message_id': 99,
        'raw_message': '@1005 hi',
        'message': [
            {'type': 'at', 'data': {'qq': '1005'}},
            {'type': 'text', 'data': {'text': ' hi'}},
        ],
        'sender': {'nickname': 'tester', 'card': 'Tester Card'},
    }
    adapter._build_event_from_payload(payload)

    seen = {}

    async def fake_call(action, call_payload):
        seen['action'] = action
        seen['payload'] = call_payload
        return {'data': {'message_id': 123}}

    monkeypatch.setattr(adapter, '_call_api', fake_call)
    result = await adapter.send('8888', 'hello back', reply_to='99', metadata={'chat_type': 'group'})

    assert result.success is True
    assert seen['action'] == 'send_group_msg'
    assert seen['payload']['message'][0] == {'type': 'reply', 'data': {'id': '99'}}
    assert seen['payload']['message'][1] == {'type': 'text', 'data': {'text': 'hello back'}}
