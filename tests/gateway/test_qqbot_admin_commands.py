from textwrap import dedent

import pytest
import yaml

from gateway.config import Platform, load_gateway_config
from gateway.platforms.base import MessageEvent
from gateway.qqbot_policy import QQBotPolicy
from gateway.session import SessionSource
from gateway.status import write_runtime_status

QQ_ADMIN = '1003'


def _make_event(text: str, *, user_id: str = QQ_ADMIN) -> MessageEvent:
    source = SessionSource(
        platform=Platform.QQBOT,
        chat_id=user_id,
        chat_type='dm',
        user_id=user_id,
        user_name='admin',
    )
    return MessageEvent(text=text, source=source, message_id='m1')


def _write_config(tmp_path, config_text: str) -> None:
    hermes_home = tmp_path / '.hermes'
    hermes_home.mkdir(exist_ok=True)
    (hermes_home / 'config.yaml').write_text(dedent(config_text).strip() + '\n', encoding='utf-8')


def _make_policy(tmp_path, monkeypatch) -> QQBotPolicy:
    hermes_home = tmp_path / '.hermes'
    monkeypatch.setenv('HERMES_HOME', str(hermes_home))
    return QQBotPolicy(load_gateway_config().qqbot)


def _reload_saved_qqbot(tmp_path, monkeypatch):
    hermes_home = tmp_path / '.hermes'
    monkeypatch.setenv('HERMES_HOME', str(hermes_home))
    return load_gateway_config().qqbot


def _read_saved_yaml(tmp_path) -> dict:
    hermes_home = tmp_path / '.hermes'
    return yaml.safe_load((hermes_home / 'config.yaml').read_text(encoding='utf-8')) or {}


@pytest.mark.asyncio
async def test_admin_status_reports_allowlists_and_llm_grants(tmp_path, monkeypatch):
    from gateway.qqbot_commands import dispatch_admin_qq_command

    _write_config(
        tmp_path,
        """
        qqbot:
          enabled: true
          platform: napcat
          access:
            admins:
              - 1003
            allow_users:
              - 1010
            allow_groups:
              - 1004
          capabilities:
            llm_users:
              - 1010
        """,
    )
    policy = _make_policy(tmp_path, monkeypatch)

    result = await dispatch_admin_qq_command(
        _make_event('/admin status'),
        policy=policy,
    )

    assert result.status == 'handled'
    assert result.command_name == 'status'
    assert result.message is not None
    assert 'QQ admin status' in result.message
    assert 'allow_users: 1010' in result.message
    assert 'allow_groups: 1004' in result.message
    assert 'llm_users: 1010' in result.message


@pytest.mark.asyncio
async def test_admin_allow_user_add_persists_to_config_yaml(tmp_path, monkeypatch):
    from gateway.qqbot_commands import dispatch_admin_qq_command

    _write_config(
        tmp_path,
        """
        qqbot:
          enabled: true
          platform: napcat
          access:
            admins:
              - 1003
            allow_users:
              - 1010
        """,
    )
    policy = _make_policy(tmp_path, monkeypatch)

    result = await dispatch_admin_qq_command(
        _make_event('/admin allow user add 1013'),
        policy=policy,
    )

    saved = _reload_saved_qqbot(tmp_path, monkeypatch)
    saved_yaml = _read_saved_yaml(tmp_path)

    assert result.status == 'handled'
    assert result.command_name == 'allow'
    assert result.message == 'QQ user 1013 added to allow_users.'
    assert '1013' in saved.access.allow_users
    assert '1013' in saved_yaml['qqbot']['access']['allow_users']


@pytest.mark.asyncio
async def test_admin_runtime_show_reports_runtime_config_and_gateway_status(tmp_path, monkeypatch):
    from gateway.qqbot_commands import dispatch_admin_qq_command

    _write_config(
        tmp_path,
        """
        qqbot:
          enabled: true
          platform: napcat
          access:
            admins:
              - 1003
          runtime:
            mode: agent
            provider: openrouter
            model: anthropic/claude-sonnet-4
            enabled_toolsets:
              - search
              - browser
            max_iterations: 42
        """,
    )
    policy = _make_policy(tmp_path, monkeypatch)
    write_runtime_status(
        gateway_state='running',
        restart_requested=False,
        active_agents=2,
        platform='qqbot',
        platform_state='connected',
    )

    result = await dispatch_admin_qq_command(
        _make_event('/admin runtime show'),
        policy=policy,
    )

    assert result.status == 'handled'
    assert result.command_name == 'runtime'
    assert result.message is not None
    assert 'QQ admin runtime' in result.message
    assert 'provider: openrouter' in result.message
    assert 'model: anthropic/claude-sonnet-4' in result.message
    assert 'gateway_state: running' in result.message
    assert 'napcat_state: connected' in result.message
