from unittest import mock

import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig


def _make_napcat_qqbot_config(**extra):
    payload = {
        'http_api': 'http://127.0.0.1:3000',
        'ws_host': '127.0.0.1',
        'ws_port': 18800,
        'self_id': '1005',
    }
    payload.update(extra)
    return PlatformConfig(enabled=True, token='test', extra=payload)


def test_connected_platforms_includes_qqbot_when_napcat_transport_fields_present():
    config = GatewayConfig(platforms={Platform.QQBOT: _make_napcat_qqbot_config()})

    assert Platform.QQBOT in config.get_connected_platforms()


def test_qq_adapter_accepts_napcat_transport_fields_without_official_qq_credentials():
    from gateway.platforms.qqbot import QQAdapter

    adapter = QQAdapter(_make_napcat_qqbot_config())

    assert getattr(adapter, 'http_api', '') == 'http://127.0.0.1:3000'
    assert getattr(adapter, 'ws_host', '') == '127.0.0.1'
    assert getattr(adapter, 'ws_port', None) == 18800
    assert getattr(adapter, 'self_id', '') == '1005'
    assert getattr(adapter, 'access_token', '') == 'test'


def test_qq_adapter_parses_private_message_event_from_napcat_payload():
    from gateway.platforms.qqbot import QQAdapter
    from gateway.platforms.base import MessageType

    adapter = QQAdapter(_make_napcat_qqbot_config())
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


@pytest.mark.asyncio
async def test_qq_adapter_send_group_reply_uses_napcat_group_api(monkeypatch):
    from gateway.platforms.qqbot import QQAdapter

    adapter = QQAdapter(_make_napcat_qqbot_config())
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


@pytest.mark.asyncio
async def test_qq_adapter_send_document_uses_napcat_group_file_upload(monkeypatch, tmp_path):
    from gateway.platforms.qqbot import QQAdapter

    report = tmp_path / 'ai-daily.pdf'
    report.write_bytes(b'%PDF-1.4\n')
    adapter = QQAdapter(_make_napcat_qqbot_config())
    seen = {}

    async def fake_call(action, call_payload):
        seen['action'] = action
        seen['payload'] = call_payload
        return {'data': {'file_id': 'abc'}}

    monkeypatch.setattr(adapter, '_call_api', fake_call)

    result = await adapter.send_document('8888', str(report), metadata={'chat_type': 'group'})

    assert result.success is True
    assert seen['action'] == 'upload_group_file'
    assert seen['payload']['group_id'] == 8888
    assert seen['payload']['file'] == str(report.resolve())
    assert seen['payload']['name'] == 'ai-daily.pdf'


def test_check_qq_requirements_no_longer_depends_on_official_qq_credentials(monkeypatch):
    monkeypatch.delenv('QQ_APP_ID', raising=False)
    monkeypatch.delenv('QQ_CLIENT_SECRET', raising=False)

    from gateway.platforms.qqbot import check_qq_requirements

    with mock.patch('gateway.platforms.qqbot.adapter.AIOHTTP_AVAILABLE', True), mock.patch(
        'gateway.platforms.qqbot.adapter.HTTPX_AVAILABLE', True
    ):
        assert check_qq_requirements() is True
