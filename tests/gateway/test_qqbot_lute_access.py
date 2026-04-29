from textwrap import dedent
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.base import MessageEvent
from gateway.qqbot_config import (
    QQBotAccessConfig,
    QQBotCapabilitiesConfig,
    QQBotCLIConfig,
    QQBotCLIDomainConfig,
    QQBotConfig,
)
from gateway.qqbot_lute_registry import build_default_lute_registry, render_lute_help
from gateway.qqbot_lute_types import LuteVerbSpec
from gateway.qqbot_policy import QQBotPolicy
from gateway.session import SessionSource
from gateway.view.store import GatewayViewStore
from gateway.view.telemetry import GatewayViewTelemetry

QQ_ADMIN = '1003'
QQ_ALLOWED_USER = '1010'


def _make_policy(*, predefined_commands_for_users: bool = True) -> QQBotPolicy:
    return QQBotPolicy(
        QQBotConfig(
            enabled=True,
            platform='napcat',
            access=QQBotAccessConfig(admins=[QQ_ADMIN], allow_users=[QQ_ALLOWED_USER]),
            capabilities=QQBotCapabilitiesConfig(predefined_commands_for_users=predefined_commands_for_users),
            cli=QQBotCLIConfig(),
        )
    )


def _make_runner() -> object:
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(
        platforms={
            Platform.QQBOT: PlatformConfig(
                enabled=True,
                token='test',
                extra={'http_api': 'http://127.0.0.1:3000', 'self_id': '1007'},
            )
        },
        qqbot=QQBotConfig(
            enabled=True,
            platform='napcat',
            access=QQBotAccessConfig(admins=[QQ_ADMIN], allow_users=[QQ_ALLOWED_USER]),
            capabilities=QQBotCapabilitiesConfig(predefined_commands_for_users=True),
            cli=QQBotCLIConfig(),
        ),
    )
    runner.adapters = {Platform.QQBOT: SimpleNamespace(send=AsyncMock())}
    runner.pairing_store = SimpleNamespace(is_approved=MagicMock(return_value=False))
    runner.hooks = SimpleNamespace(emit=AsyncMock(), loaded_hooks=False)
    runner._running_agents = {}
    runner._running_agents_ts = {}
    runner._pending_messages = {}
    runner._pending_approvals = {}
    runner._voice_mode = {}
    runner._draining = False
    runner._handle_help_command = AsyncMock(return_value='built-in help')
    runner._handle_model_command = AsyncMock(return_value='built-in model')
    runner._handle_provider_command = AsyncMock(return_value='built-in provider')
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


def _write_profile_config(tmp_path, config_text: str) -> None:
    hermes_home = tmp_path / '.hermes'
    hermes_home.mkdir(exist_ok=True)
    (hermes_home / 'config.yaml').write_text(dedent(config_text).strip() + '\n', encoding='utf-8')


def _usage_block(*entries: str) -> str:
    return 'Usage:\n' + '\n'.join(f'- {entry}' for entry in entries)


_SYSTEM_RUNTIME_USAGE = _usage_block(
    '/lute system runtime show  查看 QQBot runtime 配置',
    '/lute system runtime status  查看 QQBot runtime 配置（show 别名）',
)

_CONFIG_LLM_USAGE = _usage_block(
    '/lute config llm grant <qq_id>  授予指定 QQ 号 LLM 权限',
    '/lute config llm revoke <qq_id>  撤销指定 QQ 号 LLM 权限',
)

_CONFIG_ALLOW_USAGE = _usage_block(
    '/lute config allow user add <qq_id>  将指定 QQ 用户加入 allowlist',
    '/lute config allow user remove <qq_id>  将指定 QQ 用户移出 allowlist',
    '/lute config allow group add <group_id>  将指定群加入 allowlist',
    '/lute config allow group remove <group_id>  将指定群移出 allowlist',
    '/lute config allow group  将当前群加入 allowlist',
    '/lute config allow group remove  将当前群移出 allowlist',
    '/lute config allow group-user add <qq_id>  将当前群内指定用户加入 group_user_allowlist',
    '/lute config allow group-user remove <qq_id>  将当前群内指定用户移出 group_user_allowlist',
    '/lute config allow group-user add <group_id> <qq_id>  将指定群内指定用户加入 group_user_allowlist',
    '/lute config allow group-user remove <group_id> <qq_id>  将指定群内指定用户移出 group_user_allowlist',
)


_CONFIG_FEATURE_USAGE = _usage_block(
    '/lute config feature list  查看普通用户功能开关',
    '/lute config feature enable <domain|module|all>  对普通用户开启指定功能或模块',
    '/lute config feature disable <domain|module|all>  对普通用户关闭指定功能或模块',
)


def test_policy_allows_user_domains_for_predefined_users():
    policy = _make_policy(predefined_commands_for_users=True)
    registry = build_default_lute_registry(policy.config.cli)

    assert policy.can_access_lute_domain(registry['bangumi'], QQ_ALLOWED_USER) is True


def test_policy_denies_user_domains_when_predefined_commands_are_disabled():
    policy = _make_policy(predefined_commands_for_users=False)
    registry = build_default_lute_registry(policy.config.cli)

    assert policy.can_access_lute_domain(registry['bangumi'], QQ_ALLOWED_USER) is False


def test_render_lute_help_omits_user_domains_when_policy_disables_predefined_commands():
    policy = _make_policy(predefined_commands_for_users=False)
    registry = build_default_lute_registry(policy.config.cli)

    text = render_lute_help(registry, is_admin=False, cli=policy.config.cli, policy=policy, user_id=QQ_ALLOWED_USER)

    assert '看番剧 / 今天放送' not in text
    assert '看订阅 / 热榜动态' not in text
    assert '🤖 AI 助手' in text
    assert '/lute analyze' in text


def test_render_lute_domain_help_is_hidden_when_policy_disables_predefined_commands():
    policy = _make_policy(predefined_commands_for_users=False)
    registry = build_default_lute_registry(policy.config.cli)

    text = render_lute_help(
        registry,
        is_admin=False,
        domain='bangumi',
        cli=policy.config.cli,
        policy=policy,
        user_id=QQ_ALLOWED_USER,
    )

    assert text == 'Unknown /lute section: bangumi\nTry: /lute help'


def test_policy_denies_admin_domains_for_normal_users_and_allows_admins():
    policy = _make_policy()
    registry = build_default_lute_registry(policy.config.cli)

    assert policy.can_access_lute_domain(registry['system'], QQ_ALLOWED_USER) is False
    assert policy.can_access_lute_domain(registry['system'], QQ_ADMIN) is True


def test_policy_denies_admin_only_verb_inside_user_domain_for_normal_users():
    policy = _make_policy()
    registry = build_default_lute_registry(policy.config.cli)
    admin_verb = LuteVerbSpec(name='delete', summary='dangerous', example='/lute bangumi delete 1', access='admin')

    assert policy.can_access_lute_verb(registry['bangumi'], admin_verb, QQ_ALLOWED_USER) is False
    assert policy.can_access_lute_verb(registry['bangumi'], admin_verb, QQ_ADMIN) is True


@pytest.mark.asyncio
async def test_lute_status_returns_compact_status_for_allowed_user():
    runner = _make_runner()

    result = await runner._handle_message(_make_event('/lute status', user_id=QQ_ALLOWED_USER))

    assert result is not None
    assert result.startswith('Lute status')
    assert 'role: user' in result
    assert 'predefined_commands: yes' in result


@pytest.mark.asyncio
async def test_lute_show_alias_returns_compact_status_for_allowed_user():
    runner = _make_runner()

    result = await runner._handle_message(_make_event('/lute show', user_id=QQ_ALLOWED_USER))

    assert result is not None
    assert result.startswith('Lute status')


@pytest.mark.asyncio
async def test_lute_status_returns_compact_status_for_admin():
    runner = _make_runner()

    result = await runner._handle_message(_make_event('/lute status', user_id=QQ_ADMIN))

    assert result is not None
    assert result.startswith('Lute status')
    assert 'role: admin' in result
    assert 'llm: yes' in result


@pytest.mark.asyncio
async def test_lute_system_reload_preserves_reload_safety_guarantee(tmp_path, monkeypatch):
    _write_profile_config(
        tmp_path,
        '''
        qqbot:
          enabled: true
          platform: napcat
          access:
            admins:
              - "1022"
            allow_users:
              - "1010"
        ''',
    )
    monkeypatch.setenv('HERMES_HOME', str(tmp_path / '.hermes'))
    runner = _make_runner()

    result = await runner._handle_message(_make_event('/lute system reload', user_id=QQ_ADMIN))

    assert result == 'QQ bot config reload failed: reloaded config would remove your admin access'
    assert runner.config.qqbot.access.admins == [QQ_ADMIN]


@pytest.mark.asyncio
async def test_lute_system_runtime_show_routes_to_admin_runtime_command():
    runner = _make_runner()

    result = await runner._handle_message(_make_event('/lute system runtime show', user_id=QQ_ADMIN))

    assert result is not None
    assert result.startswith('QQ admin runtime')


@pytest.mark.asyncio
async def test_lute_system_runtime_status_routes_to_admin_runtime_command():
    runner = _make_runner()

    result = await runner._handle_message(_make_event('/lute system runtime status', user_id=QQ_ADMIN))

    assert result is not None
    assert result.startswith('QQ admin runtime')


@pytest.mark.asyncio
async def test_lute_system_show_alias_routes_to_admin_status_command():
    runner = _make_runner()

    result = await runner._handle_message(_make_event('/lute system show', user_id=QQ_ADMIN))

    assert result is not None
    assert result.startswith('QQ admin status')


@pytest.mark.asyncio
async def test_lute_system_runtime_usage_is_rewritten_to_lute_namespace():
    runner = _make_runner()

    result = await runner._handle_message(_make_event('/lute system runtime nope', user_id=QQ_ADMIN))

    assert result == _SYSTEM_RUNTIME_USAGE


@pytest.mark.asyncio
async def test_lute_system_reload_rejects_extra_args_with_structured_usage():
    runner = _make_runner()

    result = await runner._handle_message(_make_event('/lute system reload now', user_id=QQ_ADMIN))

    assert result == 'Usage: /lute system reload'


@pytest.mark.asyncio
async def test_lute_system_stats_show_reads_gateway_view_telemetry(tmp_path):
    runner = _make_runner()
    runner.view_store = GatewayViewStore(tmp_path / 'gateway-view.db')
    runner.view_telemetry = GatewayViewTelemetry(store=runner.view_store)
    runner.view_telemetry.emit(event_type='delivery_succeeded', domain='bangumi', verb='search', renderer='help.usage-card', success=True)
    runner.view_telemetry.emit(event_type='delivery_failed', domain='bangumi', verb='search', renderer='help.usage-card', success=False)
    runner.view_telemetry.emit(event_type='recall_succeeded', success=True)
    runner.view_telemetry.emit(event_type='recall_failed', success=False)
    runner.view_telemetry.emit(event_type='cache_hit', renderer='help.usage-card', cache_hit=True, success=True)
    runner.view_telemetry.emit(event_type='cache_miss', renderer='help.usage-card', cache_hit=False, success=True)
    runner.view_telemetry.emit(event_type='external_api_call', api_name='bangumi.search', success=True)

    try:
        result = await runner._handle_message(_make_event('/lute system stats show', user_id=QQ_ADMIN))
    finally:
        runner.view_store.close()

    assert result.startswith('QQBot view stats')
    assert '- delivery_succeeded: 1' in result
    assert '- delivery_failed: 1' in result
    assert '- recall_succeeded: 1' in result
    assert '- recall_failed: 1' in result
    assert '- cache_hit: 1' in result
    assert '- cache_miss: 1' in result
    assert '- cache_hit_ratio_percent: 50.0' in result
    assert '- bangumi/search: 2' in result
    assert '- help.usage-card: 4' in result
    assert '- bangumi.search: 1' in result


@pytest.mark.asyncio
async def test_lute_system_stats_domain_filters_domain_counts(tmp_path):
    runner = _make_runner()
    runner.view_store = GatewayViewStore(tmp_path / 'gateway-view.db')
    runner.view_telemetry = GatewayViewTelemetry(store=runner.view_store)
    runner.view_telemetry.emit(event_type='delivery_succeeded', domain='bangumi', verb='search', success=True)
    runner.view_telemetry.emit(event_type='delivery_succeeded', domain='torrent', verb='search', success=True)

    try:
        result = await runner._handle_message(_make_event('/lute system stats domain bangumi', user_id=QQ_ADMIN))
    finally:
        runner.view_store.close()

    assert result.startswith('QQBot view stats: domain bangumi')
    assert '- bangumi/search: 1' in result
    assert 'torrent/search' not in result


@pytest.mark.asyncio
async def test_lute_system_stats_api_reads_api_counts(tmp_path):
    runner = _make_runner()
    runner.view_store = GatewayViewStore(tmp_path / 'gateway-view.db')
    runner.view_telemetry = GatewayViewTelemetry(store=runner.view_store)
    runner.view_telemetry.emit(event_type='external_api_call', api_name='bangumi.search', success=True)
    runner.view_telemetry.emit(event_type='external_api_call', api_name='pixiv.search', success=True)

    try:
        result = await runner._handle_message(_make_event('/lute system stats api', user_id=QQ_ADMIN))
    finally:
        runner.view_store.close()

    assert result.startswith('QQBot view API stats')
    assert '- bangumi.search: 1' in result
    assert '- pixiv.search: 1' in result


@pytest.mark.asyncio
async def test_lute_system_stats_usage_is_structured():
    runner = _make_runner()

    result = await runner._handle_message(_make_event('/lute system stats nope', user_id=QQ_ADMIN))

    assert result == _usage_block(
        '/lute system stats show  查看 QQBot view/delivery 统计总览',
        '/lute system stats domain <domain>  查看指定 Lute domain 的调用统计',
        '/lute system stats api  查看外部 API 调用统计',
    )


@pytest.mark.asyncio
async def test_lute_system_stats_is_denied_for_non_admin_user():
    runner = _make_runner()

    result = await runner._handle_message(_make_event('/lute system stats show', user_id=QQ_ALLOWED_USER))

    assert result == ''


@pytest.mark.asyncio
async def test_lute_system_logs_reads_recent_view_events(tmp_path):
    runner = _make_runner()
    runner.view_store = GatewayViewStore(tmp_path / 'gateway-view.db')
    runner.view_telemetry = GatewayViewTelemetry(store=runner.view_store)
    runner.view_telemetry.emit(event_type='delivery_succeeded', domain='bangumi', verb='search', success=True, details={'kind': 'text'})
    runner.view_telemetry.emit(event_type='cache_hit', domain='bangumi', verb='search', success=True)

    try:
        result = await runner._handle_message(_make_event('/lute system logs', user_id=QQ_ADMIN))
    finally:
        runner.view_store.close()

    assert result.startswith('QQBot view event log')
    assert 'delivery_succeeded' in result
    assert 'cache_hit' in result
    assert 'bangumi/search' in result


@pytest.mark.asyncio
async def test_lute_system_logs_accepts_custom_limit(tmp_path):
    runner = _make_runner()
    runner.view_store = GatewayViewStore(tmp_path / 'gateway-view.db')
    runner.view_telemetry = GatewayViewTelemetry(store=runner.view_store)
    runner.view_telemetry.emit(event_type='delivery_succeeded', domain='bangumi', verb='search', success=True)
    runner.view_telemetry.emit(event_type='delivery_failed', domain='torrent', verb='search', success=False)
    runner.view_telemetry.emit(event_type='recall_succeeded', success=True)

    try:
        result = await runner._handle_message(_make_event('/lute system logs 2', user_id=QQ_ADMIN))
    finally:
        runner.view_store.close()

    assert result.startswith('QQBot view event log')
    assert 'delivery_succeeded' not in result
    assert 'delivery_failed' in result
    assert 'recall_succeeded' in result


@pytest.mark.asyncio
async def test_lute_system_logs_usage_is_structured():
    runner = _make_runner()

    result = await runner._handle_message(_make_event('/lute system logs nope', user_id=QQ_ADMIN))

    assert result == 'Usage: /lute system logs [limit]'


@pytest.mark.asyncio
async def test_lute_system_logs_is_denied_for_non_admin_user():
    runner = _make_runner()

    result = await runner._handle_message(_make_event('/lute system logs', user_id=QQ_ALLOWED_USER))

    assert result == ''


@pytest.mark.asyncio
async def test_lute_config_llm_grant_updates_live_runner_config(tmp_path, monkeypatch):
    _write_profile_config(
        tmp_path,
        '''
        qqbot:
          enabled: true
          platform: napcat
          access:
            admins:
              - "1003"
            allow_users:
              - "1010"
        ''',
    )
    monkeypatch.setenv('HERMES_HOME', str(tmp_path / '.hermes'))
    runner = _make_runner()

    result = await runner._handle_message(_make_event('/lute config llm grant 1013', user_id=QQ_ADMIN))

    assert result == 'LLM access granted to QQ user 1013.'
    assert '1013' in runner.config.qqbot.capabilities.llm_users


@pytest.mark.asyncio
async def test_lute_config_llm_grant_is_denied_for_non_admin_user():
    runner = _make_runner()

    result = await runner._handle_message(_make_event('/lute config llm grant 1013', user_id=QQ_ALLOWED_USER))

    assert result == ''


@pytest.mark.asyncio
async def test_lute_config_llm_usage_is_rewritten_to_lute_namespace():
    runner = _make_runner()

    result = await runner._handle_message(_make_event('/lute config llm', user_id=QQ_ADMIN))

    assert result == _CONFIG_LLM_USAGE


@pytest.mark.asyncio
async def test_lute_config_allow_usage_is_rewritten_to_lute_namespace():
    runner = _make_runner()

    result = await runner._handle_message(_make_event('/lute config allow', user_id=QQ_ADMIN))

    assert result == _CONFIG_ALLOW_USAGE


@pytest.mark.asyncio
async def test_lute_config_feature_usage_is_rewritten_to_lute_namespace():
    runner = _make_runner()

    result = await runner._handle_message(_make_event('/lute config feature nope', user_id=QQ_ADMIN))

    assert result == _CONFIG_FEATURE_USAGE


@pytest.mark.asyncio
async def test_lute_config_feature_is_denied_for_non_admin_user():
    runner = _make_runner()

    result = await runner._handle_message(_make_event('/lute config feature list', user_id=QQ_ALLOWED_USER))

    assert result == ''


@pytest.mark.asyncio
async def test_lute_config_feature_disable_domain_restricts_users_but_not_admins(tmp_path, monkeypatch):
    _write_profile_config(
        tmp_path,
        '''
        qqbot:
          enabled: true
          platform: napcat
          access:
            admins:
              - "1003"
            allow_users:
              - "1010"
        ''',
    )
    monkeypatch.setenv('HERMES_HOME', str(tmp_path / '.hermes'))
    runner = _make_runner()

    result = await runner._handle_message(_make_event('/lute config feature disable bangumi', user_id=QQ_ADMIN))

    assert result == 'Feature bangumi disabled for ordinary users.'
    assert runner.config.qqbot.cli.domains['bangumi'].enabled is True
    assert runner.config.qqbot.cli.domains['bangumi'].access == 'admin'
    assert runner.config.qqbot.cli.domains['bangumi'].visible_in_help is False
    registry = build_default_lute_registry(runner.config.qqbot.cli)
    assert 'bangumi' in registry
    assert QQBotPolicy(runner.config.qqbot).can_access_lute_domain(registry['bangumi'], QQ_ADMIN) is True
    assert QQBotPolicy(runner.config.qqbot).can_access_lute_domain(registry['bangumi'], QQ_ALLOWED_USER) is False


@pytest.mark.asyncio
async def test_lute_config_feature_enable_information_lookup_module_updates_multiple_domains(tmp_path, monkeypatch):
    _write_profile_config(
        tmp_path,
        '''
        qqbot:
          enabled: true
          platform: napcat
          access:
            admins:
              - "1003"
            allow_users:
              - "1010"
        ''',
    )
    monkeypatch.setenv('HERMES_HOME', str(tmp_path / '.hermes'))
    runner = _make_runner()
    runner.config.qqbot.cli.domains['bili'] = QQBotCLIDomainConfig(access='admin', visible_in_help=False)
    runner.config.qqbot.cli.domains['utility'] = QQBotCLIDomainConfig(access='admin', visible_in_help=False)

    result = await runner._handle_message(_make_event('/lute config feature enable 信息查询', user_id=QQ_ADMIN))

    assert result == 'Feature module 信息查询 enabled for ordinary users: bili, utility.'
    assert runner.config.qqbot.cli.domains['bili'].access == 'user'
    assert runner.config.qqbot.cli.domains['bili'].visible_in_help is True
    assert runner.config.qqbot.cli.domains['utility'].access == 'user'
    assert runner.config.qqbot.cli.domains['utility'].visible_in_help is True


@pytest.mark.asyncio
async def test_lute_config_feature_enable_all_only_changes_user_domains(tmp_path, monkeypatch):
    _write_profile_config(
        tmp_path,
        '''
        qqbot:
          enabled: true
          platform: napcat
          access:
            admins:
              - "1003"
            allow_users:
              - "1010"
        ''',
    )
    monkeypatch.setenv('HERMES_HOME', str(tmp_path / '.hermes'))
    runner = _make_runner()
    runner.config.qqbot.cli.domains['bangumi'] = QQBotCLIDomainConfig(access='admin', visible_in_help=False)
    runner.config.qqbot.cli.domains['system'] = QQBotCLIDomainConfig(enabled=False, access='user', visible_in_help=True)

    result = await runner._handle_message(_make_event('/lute config feature enable all', user_id=QQ_ADMIN))

    assert 'All ordinary-user feature domains enabled.' in result
    assert runner.config.qqbot.cli.domains['bangumi'].access == 'user'
    assert runner.config.qqbot.cli.domains['bangumi'].visible_in_help is True
    assert runner.config.qqbot.cli.domains['system'].enabled is False


@pytest.mark.asyncio
async def test_lute_config_feature_preserves_existing_domain_enabled_flag(tmp_path, monkeypatch):
    _write_profile_config(
        tmp_path,
        '''
        qqbot:
          enabled: true
          platform: napcat
          access:
            admins:
              - "1003"
            allow_users:
              - "1010"
        ''',
    )
    monkeypatch.setenv('HERMES_HOME', str(tmp_path / '.hermes'))
    runner = _make_runner()
    runner.config.qqbot.cli.domains['torrent'] = QQBotCLIDomainConfig(enabled=False, access='user', visible_in_help=True)

    await runner._handle_message(_make_event('/lute config feature disable torrent', user_id=QQ_ADMIN))

    assert runner.config.qqbot.cli.domains['torrent'].enabled is False
    assert runner.config.qqbot.cli.domains['torrent'].access == 'admin'
    assert runner.config.qqbot.cli.domains['torrent'].visible_in_help is False


@pytest.mark.asyncio
async def test_lute_config_feature_list_shows_user_scope_and_module_aliases():
    runner = _make_runner()
    runner.config.qqbot.cli.domains['bangumi'] = QQBotCLIDomainConfig(access='admin', visible_in_help=False)

    result = await runner._handle_message(_make_event('/lute config feature list', user_id=QQ_ADMIN))

    assert result.startswith('QQBot feature toggles for ordinary users')
    assert 'bangumi: disabled' in result
    assert 'epic: enabled' in result
    assert '信息查询=bili, utility' in result
