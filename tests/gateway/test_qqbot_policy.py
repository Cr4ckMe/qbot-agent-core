from gateway.config import Platform
from gateway.qqbot_config import QQBotAccessConfig, QQBotCapabilitiesConfig, QQBotConfig
from gateway.qqbot_policy import QQBotPolicy
from gateway.session import SessionSource


def _make_config(**overrides) -> QQBotConfig:
    defaults = {
        'enabled': True,
        'platform': 'napcat',
        'access': QQBotAccessConfig(
            admins=['1003'],
            allow_users=['1010'],
            allow_groups=['1004'],
            group_user_allowlist={'1004': ['1010']},
        ),
        'capabilities': QQBotCapabilitiesConfig(llm_users=['1021']),
    }
    defaults.update(overrides)
    return QQBotConfig(**defaults)


def _make_source(platform: Platform = Platform.QQBOT) -> SessionSource:
    return SessionSource(platform=platform, chat_id='1004', chat_type='dm', user_id='1010')


def test_is_enabled_for_matches_enabled_qqbot_sources_even_when_product_platform_is_napcat():
    policy = QQBotPolicy(_make_config())

    assert policy.is_enabled_for(_make_source()) is True
    assert policy.is_enabled_for(_make_source(platform=Platform.TELEGRAM)) is False
    assert QQBotPolicy(_make_config(enabled=False)).is_enabled_for(_make_source()) is False


def test_detects_admin_and_allowlisted_users():
    policy = QQBotPolicy(_make_config())

    assert policy.is_admin('1003') is True
    assert policy.is_allowed_dm_user('1003') is True
    assert policy.is_allowed_dm_user('1010') is True
    assert policy.is_allowed_dm_user('1013') is False


def test_group_user_allowlist_and_admin_bypass_are_preserved():
    policy = QQBotPolicy(_make_config())

    assert policy.is_allowed_group('1004') is True
    assert policy.is_allowed_group_user('1004', '1010') is True
    assert policy.is_allowed_group_user('1004', '1013') is False
    assert policy.is_allowed_group_user('1014', '1003') is True


def test_capability_gates_are_preserved():
    policy = QQBotPolicy(_make_config())

    assert policy.can_use_predefined_commands('1010') is True
    assert policy.can_use_llm('1021') is True
    assert policy.can_use_llm('1010') is False
    assert policy.can_use_admin_slash('1010') is False
    assert policy.can_modify_state('1010') is False
    assert policy.can_use_admin_slash('1003') is True
    assert policy.can_modify_state('1003') is True
