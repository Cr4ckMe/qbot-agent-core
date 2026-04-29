from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.base import MessageEvent
from gateway.qqbot_config import QQBotAccessConfig, QQBotConfig, QQBotTestingConfig
from gateway.session import SessionSource


QQ_ADMIN = "1003"
QQ_ALLOWED_USER = "1010"
QQ_ALLOWED_GROUP = "1004"
QQ_UNAUTHORIZED_USER = "1013"
QQ_UNAUTHORIZED_GROUP = "1014"


def _make_qqbot_config(
    enabled: bool = True,
    unauthorized_dm_behavior: str = "ignore",
    *,
    explicit_denials: bool = False,
) -> QQBotConfig:
    return QQBotConfig(
        enabled=enabled,
        platform="napcat",
        unauthorized_dm_behavior=unauthorized_dm_behavior,
        access=QQBotAccessConfig(
            admins=[QQ_ADMIN],
            allow_users=[QQ_ALLOWED_USER],
            allow_groups=[QQ_ALLOWED_GROUP],
            group_user_allowlist={QQ_ALLOWED_GROUP: [QQ_ALLOWED_USER]},
        ),
        testing=QQBotTestingConfig(explicit_denials=explicit_denials),
    )


def _make_runner(
    qqbot_enabled: bool = True,
    unauthorized_dm_behavior: str = "ignore",
    *,
    explicit_denials: bool = False,
):
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(
        platforms={
            Platform.QQBOT: PlatformConfig(enabled=True, token="test", extra={"http_api": "http://127.0.0.1:3000"}),
            Platform.TELEGRAM: PlatformConfig(enabled=True, token="test"),
        },
        qqbot=_make_qqbot_config(
            enabled=qqbot_enabled,
            unauthorized_dm_behavior=unauthorized_dm_behavior,
            explicit_denials=explicit_denials,
        ),
    )
    runner.adapters = {
        Platform.QQBOT: SimpleNamespace(send=AsyncMock()),
        Platform.TELEGRAM: SimpleNamespace(send=AsyncMock()),
    }
    runner.pairing_store = SimpleNamespace(
        is_approved=MagicMock(return_value=False),
        _is_rate_limited=MagicMock(return_value=False),
        generate_code=MagicMock(return_value="PAIR123"),
        _record_rate_limit=MagicMock(),
    )
    return runner


def _make_source(
    *,
    platform: Platform = Platform.QQBOT,
    chat_type: str = "dm",
    user_id: str = QQ_UNAUTHORIZED_USER,
    chat_id: str | None = None,
) -> SessionSource:
    if chat_id is None:
        chat_id = user_id if chat_type == "dm" else QQ_UNAUTHORIZED_GROUP
    return SessionSource(
        platform=platform,
        chat_id=chat_id,
        chat_type=chat_type,
        user_id=user_id,
        user_name="tester",
    )


def _make_event(source: SessionSource) -> MessageEvent:
    return MessageEvent(text="hello", source=source, message_id="m1")


@pytest.mark.asyncio
async def test_unauthorized_qq_dm_is_ignored_without_pairing_message():
    runner = _make_runner()

    result = await runner._handle_message(_make_event(_make_source()))

    assert result is None
    runner.adapters[Platform.QQBOT].send.assert_not_called()
    runner.pairing_store._is_rate_limited.assert_not_called()
    runner.pairing_store.generate_code.assert_not_called()


@pytest.mark.asyncio
async def test_unauthorized_qq_dm_returns_debug_hint_when_explicit_denials_enabled():
    runner = _make_runner(explicit_denials=True)

    result = await runner._handle_message(_make_event(_make_source()))

    assert result == "should no reply"
    runner.adapters[Platform.QQBOT].send.assert_not_called()
    runner.pairing_store.generate_code.assert_not_called()


@pytest.mark.asyncio
async def test_unauthorized_qq_dm_ignores_legacy_pair_setting_in_qqbot_mode():
    runner = _make_runner(unauthorized_dm_behavior="pair")

    result = await runner._handle_message(_make_event(_make_source()))

    assert result is None
    runner.pairing_store._is_rate_limited.assert_not_called()
    runner.pairing_store.generate_code.assert_not_called()
    runner.adapters[Platform.QQBOT].send.assert_not_called()


@pytest.mark.asyncio
async def test_unauthorized_qq_group_is_ignored():
    runner = _make_runner()
    source = _make_source(chat_type="group")

    result = await runner._handle_message(_make_event(source))

    assert result is None
    runner.adapters[Platform.QQBOT].send.assert_not_called()
    runner.pairing_store.generate_code.assert_not_called()


@pytest.mark.asyncio
async def test_unauthorized_qq_group_returns_debug_hint_when_explicit_denials_enabled():
    runner = _make_runner(explicit_denials=True)
    source = _make_source(chat_type="group")

    result = await runner._handle_message(_make_event(source))

    assert result == "should no reply"
    runner.adapters[Platform.QQBOT].send.assert_not_called()


def test_admin_qq_dm_is_allowed():
    runner = _make_runner()
    source = _make_source(user_id=QQ_ADMIN, chat_id=QQ_ADMIN)
    event = _make_event(source)

    assert runner._qqbot_enabled_for_source(source) is True
    assert runner._is_qqbot_message_allowed(event) is True
    assert runner._is_user_authorized(source) is True



def test_allowed_qq_user_dm_is_allowed():
    runner = _make_runner()
    source = _make_source(user_id=QQ_ALLOWED_USER, chat_id=QQ_ALLOWED_USER)
    event = _make_event(source)

    assert runner._is_qqbot_message_allowed(event) is True
    assert runner._is_user_authorized(source) is True



def test_allowed_qq_group_is_allowed_for_group_allowlisted_user():
    runner = _make_runner()
    source = _make_source(chat_type="group", user_id=QQ_ALLOWED_USER, chat_id=QQ_ALLOWED_GROUP)
    event = _make_event(source)

    assert runner._is_qqbot_message_allowed(event) is True
    assert runner._is_user_authorized(source) is True



def test_group_allowlisted_but_non_member_user_is_denied():
    runner = _make_runner()
    source = _make_source(chat_type="group", user_id="1010", chat_id=QQ_ALLOWED_GROUP)
    event = _make_event(source)

    assert runner._is_qqbot_message_allowed(event) is False
    assert runner._is_user_authorized(source) is False



def test_admin_bypasses_group_user_allowlist():
    runner = _make_runner()
    source = _make_source(chat_type="group", user_id=QQ_ADMIN, chat_id=QQ_UNAUTHORIZED_GROUP)
    event = _make_event(source)

    assert runner._is_qqbot_message_allowed(event) is True
    assert runner._is_user_authorized(source) is True


@pytest.mark.asyncio
async def test_non_qq_platforms_keep_existing_pairing_flow():
    runner = _make_runner()
    source = _make_source(platform=Platform.TELEGRAM, chat_id="telegram-chat", user_id="telegram-user")

    result = await runner._handle_message(_make_event(source))

    assert result is None
    runner.pairing_store._is_rate_limited.assert_called_once_with("telegram", "telegram-user")
    runner.pairing_store.generate_code.assert_called_once_with("telegram", "telegram-user", "tester")
    runner.adapters[Platform.TELEGRAM].send.assert_awaited_once()
