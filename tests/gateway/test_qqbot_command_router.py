import pytest

from gateway.config import Platform
from gateway.platforms.base import MessageEvent
from gateway.qqbot_commands import (
    QQCommandDef,
    dispatch_admin_qq_command,
    dispatch_public_qq_command,
    get_admin_qq_commands,
    get_public_qq_commands,
    parse_qq_admin_command,
    parse_qq_command,
)
from gateway.qqbot_config import QQBotAccessConfig, QQBotCapabilitiesConfig, QQBotConfig
from gateway.qqbot_policy import QQBotPolicy
from gateway.session import SessionSource

QQ_ADMIN = "1003"
QQ_ALLOWED_USER = "1010"


def _make_policy(*, predefined_commands_for_users: bool = True) -> QQBotPolicy:
    return QQBotPolicy(
        QQBotConfig(
            enabled=True,
            platform="napcat",
            access=QQBotAccessConfig(
                admins=[QQ_ADMIN],
                allow_users=[QQ_ALLOWED_USER],
                allow_groups=[],
            ),
            capabilities=QQBotCapabilitiesConfig(
                llm_users=[],
                predefined_commands_for_users=predefined_commands_for_users,
                admin_slash_admin_only=True,
                modify_state_admin_only=True,
            ),
        )
    )


def _make_event(text: str, *, user_id: str) -> MessageEvent:
    source = SessionSource(
        platform=Platform.QQBOT,
        chat_id=user_id,
        chat_type="dm",
        user_id=user_id,
        user_name="tester",
    )
    return MessageEvent(text=text, source=source, message_id="m1")


async def _handle_custom_command(event: MessageEvent, policy: QQBotPolicy, args: list[str]) -> str:
    del event, policy
    return "custom:" + " ".join(args)


def test_parse_qq_command_extracts_command_and_args():
    assert parse_qq_command("/help more words") == ("help", "more words")
    assert parse_qq_command("hello") == (None, "hello")



def test_parse_qq_admin_command_extracts_subcommand_and_args():
    assert parse_qq_admin_command("/admin status verbose") == ("status", ["verbose"])
    assert parse_qq_admin_command("/help") == (None, [])



def test_command_registries_expose_expected_commands():
    assert "help" in get_public_qq_commands()
    assert "ping" in get_public_qq_commands()
    assert "status" in get_admin_qq_commands()


@pytest.mark.asyncio
async def test_dispatch_public_help_command_returns_help_text():
    result = await dispatch_public_qq_command(
        _make_event("/help", user_id=QQ_ALLOWED_USER),
        policy=_make_policy(),
    )

    assert result.status == "handled"
    assert result.command_name == "help"
    assert result.message is not None
    assert "/help" in result.message
    assert "/ping" in result.message


@pytest.mark.asyncio
async def test_dispatch_public_alias_resolves_to_help_handler():
    result = await dispatch_public_qq_command(
        _make_event("/menu", user_id=QQ_ALLOWED_USER),
        policy=_make_policy(),
    )

    assert result.status == "handled"
    assert result.command_name == "help"
    assert result.message is not None
    assert "/help" in result.message


@pytest.mark.asyncio
async def test_dispatch_public_help_uses_override_registry_for_rendering():
    custom_commands = {
        "help": QQCommandDef(
            name="help",
            description="Show custom commands",
            handler=_handle_custom_command,
            aliases=("menu",),
        ),
        "custom": QQCommandDef(
            name="custom",
            description="Run the custom command",
            handler=_handle_custom_command,
            aliases=("c",),
            args_hint="[value]",
        ),
    }

    result = await dispatch_public_qq_command(
        _make_event("/help", user_id=QQ_ALLOWED_USER),
        policy=_make_policy(),
        commands=custom_commands,
    )

    assert result.status == "handled"
    assert result.message is not None
    assert "/custom [value]" in result.message
    assert "/ping" not in result.message


@pytest.mark.asyncio
async def test_dispatch_public_empty_registry_does_not_fallback_to_defaults():
    result = await dispatch_public_qq_command(
        _make_event("/ping", user_id=QQ_ALLOWED_USER),
        policy=_make_policy(),
        commands={},
    )

    assert result.status == "not_applicable"
    assert result.command_name == "ping"


@pytest.mark.asyncio
async def test_dispatch_admin_status_command_for_admin_user():
    result = await dispatch_admin_qq_command(
        _make_event("/admin status", user_id=QQ_ADMIN),
        policy=_make_policy(),
    )

    assert result.status == "handled"
    assert result.command_name == "status"
    assert result.message is not None
    assert "QQ admin status" in result.message
    assert QQ_ADMIN in result.message


@pytest.mark.asyncio
async def test_dispatch_admin_alias_resolves_to_status_handler():
    result = await dispatch_admin_qq_command(
        _make_event("/admin stat", user_id=QQ_ADMIN),
        policy=_make_policy(),
    )

    assert result.status == "handled"
    assert result.command_name == "status"
    assert result.message is not None
    assert "QQ admin status" in result.message


@pytest.mark.asyncio
async def test_dispatch_admin_unknown_command_uses_override_registry_help():
    custom_admin = {
        "status": QQCommandDef(
            name="status",
            description="Show custom admin status",
            handler=_handle_custom_command,
            aliases=("stat",),
        ),
    }

    result = await dispatch_admin_qq_command(
        _make_event("/admin mystery", user_id=QQ_ADMIN),
        policy=_make_policy(),
        commands=custom_admin,
    )

    assert result.status == "unknown"
    assert result.message is not None
    assert "/admin status" in result.message
    assert "Show custom admin status" in result.message


@pytest.mark.asyncio
async def test_dispatch_admin_unknown_command_returns_structured_result():
    result = await dispatch_admin_qq_command(
        _make_event("/admin mystery", user_id=QQ_ADMIN),
        policy=_make_policy(),
    )

    assert result.status == "unknown"
    assert result.command_name == "mystery"
    assert result.message is not None
    assert "Unknown QQ admin command" in result.message
    assert "/admin status" in result.message


@pytest.mark.asyncio
async def test_public_command_is_accessible_to_ordinary_user():
    result = await dispatch_public_qq_command(
        _make_event("/ping", user_id=QQ_ALLOWED_USER),
        policy=_make_policy(),
    )

    assert result.status == "handled"
    assert result.command_name == "ping"
    assert result.message == "pong"


@pytest.mark.asyncio
async def test_admin_command_is_denied_to_ordinary_user():
    result = await dispatch_admin_qq_command(
        _make_event("/admin status", user_id=QQ_ALLOWED_USER),
        policy=_make_policy(),
    )

    assert result.status == "denied"
    assert result.command_name == "status"
    assert result.message is not None
    assert "admin-only" in result.message.lower()
