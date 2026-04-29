from textwrap import dedent

from gateway.config import GatewayConfig, Platform, PlatformConfig, load_gateway_config
from gateway.qqbot_config import QQBotAccessConfig, QQBotConfig


def test_gateway_config_defaults_qqbot_when_absent():
    config = GatewayConfig.from_dict({})

    assert config.qqbot == QQBotConfig(enabled=False)
    assert config.to_dict()['qqbot'] == QQBotConfig(enabled=False).to_dict()


def test_gateway_config_roundtrip_preserves_qqbot_block_and_projects_trigger_settings_to_platform_extra():
    config = GatewayConfig.from_dict(
        {
            'qqbot': {
                'enabled': True,
                'platform': 'napcat',
                'unauthorized_dm_behavior': 'ignore',
                'access': {
                    'admins': ['1003'],
                    'allow_users': ['1010'],
                    'allow_groups': ['1004'],
                    'group_user_allowlist': {'1004': ['1010']},
                },
                'triggers': {
                    'command_prefixes': ['/','！！'],
                    'group_commands_require_mention': False,
                    'group_free_text_require_mention': False,
                    'allow_reply_without_mention': True,
                },
            },
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
            },
        }
    )

    assert config.qqbot.enabled is True
    assert config.qqbot.platform == 'napcat'
    assert config.qqbot.access == QQBotAccessConfig(
        admins=['1003'],
        allow_users=['1010'],
        allow_groups=['1004'],
        group_user_allowlist={'1004': ['1010']},
    )
    assert Platform.QQBOT in config.platforms
    assert config.platforms[Platform.QQBOT].extra['command_prefixes'] == ['/', '！！']
    assert config.platforms[Platform.QQBOT].extra['group_commands_require_mention'] is False
    assert config.platforms[Platform.QQBOT].extra['group_free_text_require_mention'] is False
    assert config.platforms[Platform.QQBOT].extra['allow_reply_without_mention'] is True


def test_load_gateway_config_reads_qqbot_block_from_config_yaml(tmp_path, monkeypatch):
    hermes_home = tmp_path / '.hermes'
    hermes_home.mkdir()
    (hermes_home / 'config.yaml').write_text(
        dedent(
            '''
            platforms:
              napcat:
                enabled: true
                token: test
                extra:
                  http_api: http://127.0.0.1:3000
                  ws_host: 127.0.0.1
                  ws_port: 18800
                  self_id: '1005'
            qqbot:
              enabled: true
              platform: napcat
              unauthorized_dm_behavior: ignore
              access:
                admins:
                  - 1003
                allow_users:
                  - 1010
              triggers:
                command_prefixes:
                  - /
                  - '！！'
                group_commands_require_mention: false
                group_free_text_require_mention: false
                allow_reply_without_mention: true
            '''
        ).strip()
        + '\n',
        encoding='utf-8',
    )
    monkeypatch.setenv('HERMES_HOME', str(hermes_home))

    config = load_gateway_config()

    assert config.qqbot.enabled is True
    assert config.qqbot.platform == 'napcat'
    assert config.qqbot.unauthorized_dm_behavior == 'ignore'
    assert config.qqbot.access.admins == ['1003']
    assert config.platforms[Platform.QQBOT].token == 'test'
    assert config.platforms[Platform.QQBOT].extra['http_api'] == 'http://127.0.0.1:3000'
