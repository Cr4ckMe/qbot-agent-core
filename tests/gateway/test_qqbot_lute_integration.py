import socket
from datetime import datetime, timedelta, timezone
from textwrap import dedent
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.base import MessageEvent
from gateway.qqbot_config import (
    QQBotAccessConfig,
    QQBotCLIConfig,
    QQBotCapabilitiesConfig,
    QQBotConfig,
    QQBotFeedCardRenderConfig,
    QQBotTestingConfig,
    QQBotViewConfig,
)
from gateway.qqbot_lute_handlers import dispatch_lute_invocation
from gateway.qqbot_lute_registry import build_default_lute_registry, build_default_lute_root_commands
from gateway.qqbot_lute_types import LuteInvocation, LuteResponse
from gateway.qqbot_policy import QQBotPolicy
from gateway.session import SessionSource


QQ_ADMIN = '1003'
QQ_ALLOWED_USER = '1010'


def _admin_only_lute_command_samples() -> list[str]:
    cli = QQBotCLIConfig(enable_legacy_bare_commands=False)
    registry = build_default_lute_registry(cli)
    samples: list[str] = []
    for domain_name, domain_spec in registry.items():
        if domain_spec.access == 'admin':
            samples.append(f'/lute {domain_name}')
            samples.extend(f'/lute {domain_name} {verb_name}' for verb_name in domain_spec.verbs)
            continue
        samples.extend(
            f'/lute {domain_name} {verb_name}'
            for verb_name, verb_spec in domain_spec.verbs.items()
            if verb_spec.access == 'admin'
        )
    return samples


def _make_runner(*, enable_legacy_bare_commands: bool, explicit_denials: bool = False, allow_group_user: bool = False):
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
            access=QQBotAccessConfig(
                admins=[QQ_ADMIN],
                allow_users=[QQ_ALLOWED_USER],
                allow_groups=['1004'],
                group_user_allowlist={'1004': [QQ_ALLOWED_USER]} if allow_group_user else {},
            ),
            cli=QQBotCLIConfig(enable_legacy_bare_commands=enable_legacy_bare_commands),
            testing=QQBotTestingConfig(explicit_denials=explicit_denials),
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


def _make_group_event(
    text: str,
    *,
    user_id: str,
    chat_id: str = '1004',
    reply_to_message_id: str | None = None,
    reply_to_text: str | None = None,
    referenced_media_urls: list[str] | None = None,
    referenced_media_types: list[str] | None = None,
) -> MessageEvent:
    source = SessionSource(
        platform=Platform.QQBOT,
        chat_id=chat_id,
        chat_type='group',
        user_id=user_id,
        user_name='tester',
    )
    return MessageEvent(
        text=text,
        source=source,
        message_id='g1',
        reply_to_message_id=reply_to_message_id,
        reply_to_text=reply_to_text,
        referenced_media_urls=list(referenced_media_urls or []),
        referenced_media_types=list(referenced_media_types or []),
    )


def _write_profile_config(tmp_path, config_text: str) -> None:
    hermes_home = tmp_path / '.hermes'
    hermes_home.mkdir(exist_ok=True)
    (hermes_home / 'config.yaml').write_text(dedent(config_text).strip() + '\n', encoding='utf-8')


def _usage_block(*entries: str) -> str:
    return 'Usage:\n' + '\n'.join(f'- {entry}' for entry in entries)


_GROUP_MEMBER_USAGE = _usage_block(
    '/lute group member list  查看当前群成员列表',
    '/lute group member detail <qq_id>  查看指定成员资料',
    '/lute group member admin <qq_id> on|off  设置或取消群管理员',
    '/lute group member card <qq_id> <card>  修改成员群名片',
    '/lute group member title <qq_id> <title>  修改成员专属头衔',
)

_GROUP_NOTICE_USAGE = _usage_block(
    '/lute group notice send <内容> [--image <url_or_path>]  发送当前群公告',
    '/lute group notice clone <source_group_id> <notice_id> [--image <url_or_path>]  克隆公告，可改配图',
    '/lute group notice list  查看当前群公告列表',
    '/lute group notice detail <notice_id>  查看公告详情',
    '/lute group notice delete <notice_id>  删除当前群公告',
)

_GROUP_ADMIN_SHOW_USAGE = _usage_block(
    '/lute group admin show  查看本群 qqadmin 总览',
    '/lute group admin status  查看本群 qqadmin 总览（show 别名）',
)

_GROUP_ADMIN_MODERATION_USAGE = _usage_block(
    '/lute group admin moderation show  查看违禁词与封禁配置',
    '/lute group admin moderation status  查看违禁词与封禁配置（show 别名）',
    '/lute group admin moderation builtin on|off  开关内置违禁词库',
    '/lute group admin moderation words set <词...>  覆盖自定义违禁词列表',
    '/lute group admin moderation words add <词...>  追加自定义违禁词',
    '/lute group admin moderation words remove <词...>  删除自定义违禁词',
    '/lute group admin moderation word-ban <seconds>  设置违禁词触发封禁时长',
    '/lute group admin moderation spam-ban <seconds>  设置刷屏触发封禁时长',
)

_GROUP_ADMIN_LEAVE_USAGE = _usage_block(
    '/lute group admin leave notify on|off  开关退群通知',
    '/lute group admin leave block on|off  开关退群后拉黑',
    '/lute group admin leave kick-block on|off  开关被踢后拉黑',
)

_GROUP_ADMIN_CURFEW_USAGE = _usage_block(
    '/lute group admin curfew show  查看宵禁配置',
    '/lute group admin curfew status  查看宵禁配置（show 别名）',
    '/lute group admin curfew set <HH:MM> <HH:MM>  设置宵禁时间窗',
    '/lute group admin curfew clear  清除宵禁时间窗',
)

_GROUP_ADMIN_CLEANUP_USAGE = _usage_block(
    '/lute group admin cleanup preview [--inactive-days <n>] [--max-level <n>]  预览待清理成员',
    '/lute group admin cleanup apply [<qq_id...>] [--inactive-days <n>] [--max-level <n>] [--reject-add-request]  执行成员清理',
)

_GROUP_ADMIN_AI_USAGE = _usage_block(
    '/lute group admin ai gname <qq_id> [--history-count <n>]  生成群昵称建议',
    '/lute group admin ai gname apply <qq_id> [--history-count <n>]  生成并应用群昵称建议',
    '/lute group admin ai title <qq_id> [--history-count <n>]  生成头衔建议',
    '/lute group admin ai title apply <qq_id> [--history-count <n>]  生成并应用头衔建议',
)

_GROUP_ESSENCE_USAGE = _usage_block(
    '/lute group essence list  查看当前群精华消息',
    '/lute group essence add [message_id]  添加精华消息',
    '/lute group essence remove [message_id]  移除精华消息',
)

_GROUP_REACT_USAGE = _usage_block(
    '/lute group react add <emoji_id> [message_id]  为目标消息添加表情回应',
    '/lute group react remove <emoji_id> [message_id]  移除目标消息的表情回应',
)

_GROUP_READ_USAGE = 'Usage: /lute group read'
_GROUP_RECALL_USAGE = 'Usage: /lute group recall <message_id>'
_GROUP_MUTE_USAGE = 'Usage: /lute group mute <qq_id> <seconds>'
_GROUP_KICK_USAGE = 'Usage: /lute group kick <qq_id>'

_QQ_MESSAGE_SEND_USAGE = _usage_block(
    '/lute qq message send private <qq_id> <text>  给指定 QQ 私聊发送消息',
    '/lute qq message send group <group_id> <text>  给指定 QQ 群发送消息',
)

_QQ_MESSAGE_FORWARD_USAGE = _usage_block(
    '/lute qq message forward <message_id> --to-user <qq_id>  转发消息到指定 QQ 用户',
    '/lute qq message forward <message_id> --to-group <group_id>  转发消息到指定 QQ 群',
)

_QQ_MESSAGE_MERGE_USAGE = _usage_block(
    '/lute qq message merge <message_id> [more_ids...] --to-user <qq_id>  合并转发到指定 QQ 用户',
    '/lute qq message merge <message_id> [more_ids...] --to-group <group_id>  合并转发到指定 QQ 群',
)

_QQ_FILE_USAGE = _usage_block(
    '/lute qq file list <group_id>  查看群根目录文件',
    '/lute qq file all <group_id>  查看群全部文件',
    '/lute qq file detail <group_id> <file_id>  查看群文件详情',
    '/lute qq file url <group_id> <file_id>  获取群文件下载地址',
    '/lute qq file mkdir <group_id> <folder_name>  创建群文件目录',
    '/lute qq file delete <group_id> <file_id>  删除群文件',
    '/lute qq file download <url>  下载远程文件到本地',
)

_BANGUMI_SEARCH_USAGE = _usage_block(
    '/lute bangumi search [<关键词>] [--tag <标签>] [--meta-tag <公共标签>] [--year <年份>] [--image]  搜索番剧；至少提供一个关键词或筛选条件',
)

_BANGUMI_SUBJECT_USAGE = _usage_block(
    '/lute bangumi subject <subject_id> [more_ids...] [--image]  查看一个或多个条目详情，可选输出长图',
)

_FEED_FETCH_USAGE = _usage_block(
    '/lute feed fetch <subscription_id>  抓取指定订阅，也可传已存在订阅标题/标签进行匹配',
)

_FEED_ADD_USAGE = _usage_block(
    '/lute feed add <url> [--title 标题]  添加订阅，可选附加标题/间隔/标签',
)

_FEED_REMOVE_USAGE = _usage_block(
    '/lute feed remove <subscription_id>  删除指定订阅',
)

_PIXIV_SEARCH_USAGE = _usage_block(
    '/lute pixiv search <关键词>  搜索 Pixiv 作品',
)

_PIXIV_ILLUST_USAGE = _usage_block(
    '/lute pixiv illust <illust_id>  查看作品详情',
)

_PIXIV_RELATED_USAGE = _usage_block(
    '/lute pixiv related <illust_id>  查看相关作品',
)

_PIXIV_DOWNLOAD_USAGE = _usage_block(
    '/lute pixiv download <illust_id>  下载指定作品',
)

_TORRENT_SEARCH_USAGE = _usage_block(
    '/lute torrent search <关键词> [--season 1] [--episode 5] [--quality 1080p] [--language en]  使用 TorrentClaw 搜索资源',
)

_TORRENT_STREAM_USAGE = _usage_block(
    '/lute torrent stream <content_id> [--country US]  查看指定条目的可观看平台',
)

_TORRENT_FALLBACK_USAGE = _usage_block(
    '/lute torrent fallback <关键词>  显式调用备用搜索后端',
)

_TORRENT_ANALYZE_USAGE = _usage_block(
    '/lute torrent analyze <magnet_or_hash>  分析磁链或 BTIH 哈希',
)

_BOOK_SEARCH_USAGE = _usage_block(
    '/lute book search <关键词>  搜索电子书资源',
)

_BOOK_METADATA_USAGE = _usage_block(
    '/lute book metadata <book_id> --hash <book_hash>  查看电子书元数据',
)


@pytest.mark.asyncio
async def test_send_gateway_response_delivers_text_images_and_files(tmp_path):
    runner = _make_runner(enable_legacy_bare_commands=False)
    image_path = tmp_path / 'card.webp'
    image_path.write_text('fake-image', encoding='utf-8')
    file_path = tmp_path / 'report.txt'
    file_path.write_text('fake-report', encoding='utf-8')

    adapter = SimpleNamespace(
        _send_with_retry=AsyncMock(return_value=SimpleNamespace(success=True)),
        send_voice=AsyncMock(return_value=SimpleNamespace(success=True)),
        send_video=AsyncMock(return_value=SimpleNamespace(success=True)),
        send_image_file=AsyncMock(return_value=SimpleNamespace(success=True)),
        send_document=AsyncMock(return_value=SimpleNamespace(success=True)),
        extract_media=MagicMock(side_effect=lambda content: ([], content)),
        extract_local_files=MagicMock(side_effect=lambda content: ([], content)),
    )
    event = _make_event('/lute bangumi image 123', user_id=QQ_ALLOWED_USER)
    response = LuteResponse(text='Bangumi card', media_paths=[str(image_path)], file_paths=[str(file_path)])

    await runner._send_gateway_response(adapter, event, response)

    adapter._send_with_retry.assert_awaited_once()
    assert adapter._send_with_retry.await_args.kwargs['content'] == 'Bangumi card'
    adapter.send_image_file.assert_awaited_once()
    assert adapter.send_image_file.await_args.kwargs['image_path'] == str(image_path)
    adapter.send_document.assert_awaited_once()
    assert adapter.send_document.await_args.kwargs['file_path'] == str(file_path)


@pytest.mark.asyncio
async def test_lute_help_legacy_compat_path_renders_cached_help_menu_image_marker(monkeypatch, tmp_path):
    runner = _make_runner(enable_legacy_bare_commands=False)
    image_path = tmp_path / 'lute-help.jpg'
    image_path.write_bytes(b'\xff\xd8\xff\xe0minimal-jpeg-payload\xff\xd9')

    def fake_render(view):
        assert view.data['variant'] == 'help-menu'
        assert view.telemetry_tags == {'domain': 'lute', 'verb': 'help'}
        assert any(module['title'] == '🎮 娱乐休闲' for module in view.data['modules'])
        return str(image_path)

    monkeypatch.setattr('gateway.view.help_image_renderer.render_help_usage_card', fake_render)

    result = await runner._handle_message(_make_event('/lute help', user_id=QQ_ALLOWED_USER))

    assert result == f'IMAGE_PATH={image_path}'
    runner._handle_help_command.assert_not_called()


@pytest.mark.asyncio
async def test_bare_lute_legacy_compat_path_renders_task_first_cli_menu_marker(monkeypatch, tmp_path):
    runner = _make_runner(enable_legacy_bare_commands=False)
    image_path = tmp_path / 'bare-lute-help.jpg'
    image_path.write_bytes(b'\xff\xd8\xff\xe0minimal-jpeg-payload\xff\xd9')
    monkeypatch.setattr('gateway.view.help_image_renderer.render_help_usage_card', lambda _view: str(image_path))

    result = await runner._handle_message(_make_event('/lute', user_id=QQ_ALLOWED_USER))

    assert result == f'IMAGE_PATH={image_path}'
    runner._handle_help_command.assert_not_called()


@pytest.mark.asyncio
async def test_lute_menu_legacy_compat_path_renders_task_first_cli_menu_marker(monkeypatch, tmp_path):
    runner = _make_runner(enable_legacy_bare_commands=False)
    image_path = tmp_path / 'lute-menu.jpg'
    image_path.write_bytes(b'\xff\xd8\xff\xe0minimal-jpeg-payload\xff\xd9')
    monkeypatch.setattr('gateway.view.help_image_renderer.render_help_usage_card', lambda _view: str(image_path))

    result = await runner._handle_message(_make_event('/lute menu', user_id=QQ_ALLOWED_USER))

    assert result == f'IMAGE_PATH={image_path}'
    runner._handle_help_command.assert_not_called()


@pytest.mark.asyncio
async def test_lute_ping_routes_to_lute_core_handler():
    runner = _make_runner(enable_legacy_bare_commands=False)

    result = await runner._handle_message(_make_event('/lute ping', user_id=QQ_ALLOWED_USER))

    assert result == 'Lute is online.'
    runner._handle_help_command.assert_not_called()
    runner._handle_message_with_agent.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('command_text', _admin_only_lute_command_samples())
async def test_admin_only_lute_commands_are_denied_by_access_layer_for_regular_group_user(command_text):
    runner = _make_runner(enable_legacy_bare_commands=False, explicit_denials=True, allow_group_user=True)

    result = await runner._handle_message(_make_group_event(command_text, user_id=QQ_ALLOWED_USER))

    assert result == 'should no reply'
    runner._handle_help_command.assert_not_called()
    runner._handle_message_with_agent.assert_not_called()


@pytest.mark.asyncio
async def test_lute_epic_weekly_uses_script_backend(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_script_backend(command, **kwargs):
        assert command[1].endswith('epicgame.py')
        assert '--json' in command
        return LuteResponse(text='Epic weekly result')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_script_backend', _fake_call_script_backend)

    result = await runner._handle_message(_make_event('/lute epic weekly', user_id=QQ_ALLOWED_USER))

    assert result == 'Epic weekly result'


@pytest.mark.asyncio
async def test_bare_lute_epic_defaults_to_weekly(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_script_backend(command, **kwargs):
        assert command[1].endswith('epicgame.py')
        return LuteResponse(text='Epic weekly result')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_script_backend', _fake_call_script_backend)

    result = await runner._handle_message(_make_event('/lute epic', user_id=QQ_ALLOWED_USER))

    assert result == 'Epic weekly result'


@pytest.mark.asyncio
async def test_lute_bangumi_today_uses_script_backend(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)
    monkeypatch.setenv('QQBOT_SCRIPT_PYTHON', '/tmp/qqbot-python')

    async def _fake_call_script_backend(command, **kwargs):
        assert command[0] == '/tmp/qqbot-python'
        assert command[1].endswith('bangumi.py')
        assert command[2:] == ['today', '--json']
        return LuteResponse(text='Today result')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_script_backend', _fake_call_script_backend)

    result = await runner._handle_message(_make_event('/lute bangumi today', user_id=QQ_ALLOWED_USER))

    assert result == 'Today result'


@pytest.mark.asyncio
async def test_lute_bangumi_search_prefers_tag_filters_without_forcing_keyword(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_script_backend(command, **kwargs):
        assert command[1].endswith('bangumi.py')
        assert command[2] == 'search'
        assert '--keyword' not in command
        assert command[command.index('--subject-type') + 1] == '2'
        assert command[command.index('--limit') + 1] == '5'
        assert command[command.index('--year') + 1] == '2026'
        assert command[command.index('--tag') + 1] == '异世界'
        assert command[-1] == '--json'
        return LuteResponse(text='Search result')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_script_backend', _fake_call_script_backend)

    result = await runner._handle_message(_make_event('/lute bangumi search --year 2026 --tag 异世界', user_id=QQ_ALLOWED_USER))

    assert result == 'Search result'


@pytest.mark.asyncio
async def test_lute_bangumi_search_without_any_query_or_filters_returns_usage():
    runner = _make_runner(enable_legacy_bare_commands=False)

    result = await runner._handle_message(_make_event('/lute bangumi search', user_id=QQ_ALLOWED_USER))

    assert result == _BANGUMI_SEARCH_USAGE


@pytest.mark.asyncio
async def test_lute_bangumi_search_invalid_year_returns_structured_usage():
    runner = _make_runner(enable_legacy_bare_commands=False)

    result = await runner._handle_message(_make_event('/lute bangumi search --year nope --tag 异世界', user_id=QQ_ALLOWED_USER))

    assert result == _BANGUMI_SEARCH_USAGE


@pytest.mark.asyncio
async def test_lute_bangumi_search_allows_keyword_and_tag_together(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_script_backend(command, **kwargs):
        assert command[1].endswith('bangumi.py')
        assert command[2] == 'search'
        assert command[3] == '食堂'
        assert command[command.index('--tag') + 1] == '异世界'
        assert command[-1] == '--json'
        return LuteResponse(text='Search result')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_script_backend', _fake_call_script_backend)

    result = await runner._handle_message(_make_event('/lute bangumi search 食堂 --tag 异世界', user_id=QQ_ALLOWED_USER))

    assert result == 'Search result'


@pytest.mark.asyncio
async def test_lute_bangumi_subject_supports_multiple_ids(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_script_backend(command, **kwargs):
        assert command[1].endswith('bangumi.py')
        assert command[2:] == ['subject-text', '123', '456', '--json']
        return LuteResponse(text='Subject 123\n\nSubject 456')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_script_backend', _fake_call_script_backend)

    result = await runner._handle_message(_make_event('/lute bangumi subject 123 456', user_id=QQ_ALLOWED_USER))

    assert result == 'Subject 123\n\nSubject 456'


@pytest.mark.asyncio
async def test_lute_bangumi_subject_invalid_id_returns_structured_usage():
    runner = _make_runner(enable_legacy_bare_commands=False)

    result = await runner._handle_message(_make_event('/lute bangumi subject abc', user_id=QQ_ALLOWED_USER))

    assert result == _BANGUMI_SUBJECT_USAGE


@pytest.mark.asyncio
async def test_lute_bangumi_subject_image_legacy_compat_path_returns_media_marker(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_script_backend(command, **kwargs):
        assert command[1].endswith('bangumi.py')
        assert command[2] == 'subject-long-card'
        assert command[3:5] == ['123', '456']
        return LuteResponse(text='Bangumi card', media_paths=['/tmp/bangumi-card.webp'])

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_script_backend', _fake_call_script_backend)

    result = await runner._handle_message(_make_event('/lute bangumi subject 123 456 --image', user_id=QQ_ALLOWED_USER))

    assert result == 'Bangumi card\nIMAGE_PATH=/tmp/bangumi-card.webp'


@pytest.mark.asyncio
async def test_dispatch_lute_root_help_returns_structured_response_not_prerendered():
    registry = build_default_lute_registry(QQBotCLIConfig())
    root_commands = build_default_lute_root_commands(QQBotCLIConfig())
    policy = QQBotPolicy(QQBotConfig(enabled=True, platform='napcat', access=QQBotAccessConfig(admins=[QQ_ADMIN], allow_users=[QQ_ALLOWED_USER]), capabilities=QQBotCapabilitiesConfig(predefined_commands_for_users=True), cli=QQBotCLIConfig()))
    invocation = LuteInvocation(root='lute', domain='', verb='', command='help', current_chat_id=QQ_ALLOWED_USER, current_chat_type='dm')

    result = await dispatch_lute_invocation(
        invocation,
        policy=policy,
        registry=registry,
        root_commands=root_commands,
        cli=policy.config.cli,
        user_id=QQ_ALLOWED_USER,
    )

    assert result.admin_command_text is None
    assert result.text is None
    assert result.response is not None
    assert result.response.text == ''
    assert result.response.view is not None
    assert result.response.view.template == 'help.usage-card'
    assert result.response.view.kind == 'image'
    assert result.response.view.data['variant'] == 'help-menu'
    assert result.response.view.fallback_text.startswith('Lute Help')


@pytest.mark.asyncio
async def test_runner_lute_root_help_returns_structured_response_to_send_pipeline():
    runner = _make_runner(enable_legacy_bare_commands=False)

    result = await runner._maybe_handle_qqbot_command(_make_event('/lute help', user_id=QQ_ALLOWED_USER))

    assert isinstance(result, LuteResponse)
    assert result.text == ''
    assert result.view is not None
    assert result.view.template == 'help.usage-card'
    assert result.view.kind == 'image'


@pytest.mark.asyncio
async def test_runner_bare_lute_returns_structured_response_to_send_pipeline():
    runner = _make_runner(enable_legacy_bare_commands=False)

    result = await runner._maybe_handle_qqbot_command(_make_event('/lute', user_id=QQ_ALLOWED_USER))

    assert isinstance(result, LuteResponse)
    assert result.text == ''
    assert result.view is not None
    assert result.view.template == 'help.usage-card'
    assert result.view.kind == 'image'


@pytest.mark.asyncio
async def test_runner_lute_menu_returns_structured_response_to_send_pipeline():
    runner = _make_runner(enable_legacy_bare_commands=False)

    result = await runner._maybe_handle_qqbot_command(_make_event('/lute menu', user_id=QQ_ALLOWED_USER))

    assert isinstance(result, LuteResponse)
    assert result.text == ''
    assert result.view is not None
    assert result.view.template == 'help.usage-card'
    assert result.view.kind == 'image'


@pytest.mark.asyncio
async def test_dispatch_lute_bangumi_subject_image_returns_view_spec(monkeypatch):
    async def _fake_call_script_backend(command, **kwargs):
        return LuteResponse(
            text='Bangumi card',
            media_paths=['/tmp/bangumi-card.webp'],
            telemetry_events=[{'event_type': 'external_api_call', 'api_name': 'script.bangumi', 'success': True}],
        )

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_script_backend', _fake_call_script_backend)
    registry = build_default_lute_registry(QQBotCLIConfig())
    root_commands = build_default_lute_root_commands(QQBotCLIConfig())
    policy = QQBotPolicy(QQBotConfig(enabled=True, platform='napcat', access=QQBotAccessConfig(admins=[QQ_ADMIN], allow_users=[QQ_ALLOWED_USER]), capabilities=QQBotCapabilitiesConfig(predefined_commands_for_users=True), cli=QQBotCLIConfig()))
    invocation = LuteInvocation(root='lute', domain='bangumi', verb='subject', args=['123', '456'], options={'image': True}, current_chat_id=QQ_ALLOWED_USER, current_chat_type='dm')

    result = await dispatch_lute_invocation(
        invocation,
        policy=policy,
        registry=registry,
        root_commands=root_commands,
        cli=policy.config.cli,
        user_id=QQ_ALLOWED_USER,
    )

    assert result.admin_command_text is None
    assert result.text is None
    assert result.response is not None
    assert result.response.text == 'Bangumi card'
    assert result.response.view is not None
    assert result.response.view.template == 'bangumi.subject-card'
    assert result.response.view.cache_policy is not None
    assert result.response.view.cache_policy.namespace == 'bangumi-rendered'
    assert result.response.view.data['image_path'] == '/tmp/bangumi-card.webp'
    assert result.response.telemetry_events == [
        {'event_type': 'external_api_call', 'api_name': 'script.bangumi', 'success': True}
    ]


@pytest.mark.asyncio
async def test_lute_bili_read_uses_script_backend(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_script_backend(command, **kwargs):
        assert command[1].endswith('biliread.py')
        assert command[2] == 'BV1GJ411x7h7'
        return LuteResponse(text='Bili result')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_script_backend', _fake_call_script_backend)

    result = await runner._handle_message(_make_event('/lute bili read BV1GJ411x7h7', user_id=QQ_ALLOWED_USER))

    assert result == 'Bili result'


@pytest.mark.asyncio
async def test_lute_bili_read_without_target_returns_usage():
    runner = _make_runner(enable_legacy_bare_commands=False)

    result = await runner._handle_message(_make_event('/lute bili read', user_id=QQ_ALLOWED_USER))

    assert result == 'Usage: /lute bili read <BV号或链接>'


@pytest.mark.asyncio
async def test_lute_feed_fetch_uses_script_backend(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_script_backend(command, **kwargs):
        assert command[1].endswith('feed_watcher.py')
        assert command[2:] == ['fetch', '--id', '7', '--limit', '15', '--timeout', '12', '--json']
        assert kwargs.get('timeout_sec') == 25
        return LuteResponse(text='Feed fetch result')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_script_backend', _fake_call_script_backend)

    result = await runner._handle_message(_make_event('/lute feed fetch 7', user_id=QQ_ALLOWED_USER))

    assert result == 'Feed fetch result'


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('command_text', 'expected_id', 'expected_text'),
    [
        ('/lute 知乎热搜', '6', 'Zhihu hot result'),
        ('/lute B站热搜', '7', 'Bili hot result'),
    ],
)
async def test_lute_high_level_feed_aliases_dispatch_to_standard_feed_fetch(monkeypatch, command_text, expected_id, expected_text):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_script_backend(command, **kwargs):
        assert command[1].endswith('feed_watcher.py')
        assert command[2:] == ['fetch', '--id', expected_id, '--limit', '15', '--timeout', '12', '--json']
        assert kwargs.get('timeout_sec') == 25
        return LuteResponse(text=expected_text)

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_script_backend', _fake_call_script_backend)

    result = await runner._handle_message(_make_event(command_text, user_id=QQ_ALLOWED_USER))

    assert result == expected_text


@pytest.mark.asyncio
async def test_lute_unknown_high_level_alias_still_returns_unknown_section():
    runner = _make_runner(enable_legacy_bare_commands=False)

    result = await runner._handle_message(_make_event('/lute 不存在热搜', user_id=QQ_ALLOWED_USER))

    assert result == 'Unknown /lute section: 不存在热搜\nTry: /lute help'


@pytest.mark.asyncio
async def test_lute_high_level_feed_alias_uses_existing_feed_forward_behavior(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_script_backend(command, **kwargs):
        assert command[2:] == ['fetch', '--id', '6', '--limit', '15', '--timeout', '12', '--json']
        return LuteResponse(
            text='',
            payload={
                'kind': 'feed_fetch',
                'subscription': {'id': 6, 'title': '知乎热搜', 'enabled': True},
                'entries': [{'title': '热搜条目', 'summary': '', 'published': '', 'link': 'https://www.zhihu.com/question/1'}],
            },
        )

    seen = {}

    async def _fake_call_tool_backend(tool_name, args, **kwargs):
        seen['tool_name'] = tool_name
        seen['args'] = args
        return LuteResponse(text='forwarded', payload={'success': True})

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_script_backend', _fake_call_script_backend)
    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_tool_backend', _fake_call_tool_backend)

    result = await runner._handle_message(_make_group_event('/lute 知乎热搜', user_id=QQ_ADMIN))

    assert result == ''
    assert seen['tool_name'] == 'qq_forward_messages'
    assert seen['args']['chat_type'] == 'group'
    assert seen['args']['target_id'] == '1004'
    assert '知乎热搜' in seen['args']['messages'][0]['data']['content'][0]['data']['text']
    assert '热搜条目' in seen['args']['messages'][1]['data']['content'][0]['data']['text']


@pytest.mark.asyncio
async def test_lute_feed_scan_is_registered_as_admin_only_for_regular_group_user():
    assert '/lute feed scan' in _admin_only_lute_command_samples()
    runner = _make_runner(enable_legacy_bare_commands=False, explicit_denials=True, allow_group_user=True)

    result = await runner._handle_message(_make_group_event('/lute feed scan', user_id=QQ_ALLOWED_USER))

    assert result == 'should no reply'
    runner._handle_help_command.assert_not_called()
    runner._handle_message_with_agent.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('command_text', ['/lute github trending', '/lute GitHub趋势'])
async def test_lute_github_trending_alias_dispatches_to_feed_fetch_by_subscription_title(monkeypatch, command_text):
    runner = _make_runner(enable_legacy_bare_commands=False)

    def _fake_resolve_feed_subscription_target(raw_target):
        assert raw_target == 'GitHub Trending'
        return 8

    async def _fake_call_script_backend(command, **kwargs):
        assert command[1].endswith('feed_watcher.py')
        assert command[2:] == ['fetch', '--id', '8', '--limit', '15', '--timeout', '12', '--json']
        assert kwargs.get('timeout_sec') == 25
        return LuteResponse(text='GitHub trending result')

    monkeypatch.setattr('gateway.qqbot_lute_handlers._resolve_feed_subscription_target', _fake_resolve_feed_subscription_target)
    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_script_backend', _fake_call_script_backend)

    result = await runner._handle_message(_make_event(command_text, user_id=QQ_ALLOWED_USER))

    assert result == 'GitHub trending result'


@pytest.mark.asyncio
@pytest.mark.parametrize('command_text', [
    '/lute feed fetch 22 refresh',
    '/lute feed fetch 22 --refresh',
])
async def test_lute_feed_fetch_direct_id_supports_refresh_keyword_and_option(monkeypatch, command_text):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_script_backend(command, **kwargs):
        assert command[1].endswith('feed_watcher.py')
        assert command[2:] == ['fetch', '--id', '22', '--limit', '15', '--timeout', '12', '--refresh', '--json']
        assert kwargs.get('timeout_sec') == 25
        return LuteResponse(text='refreshed feed')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_script_backend', _fake_call_script_backend)

    result = await runner._handle_message(_make_event(command_text, user_id=QQ_ALLOWED_USER))

    assert result == 'refreshed feed'


@pytest.mark.asyncio
@pytest.mark.parametrize('command_text', [
    '/lute github trending refresh',
    '/lute github trending --refresh',
    '/lute GitHub趋势 refresh',
    '/lute GitHub趋势 --refresh',
])
async def test_lute_github_trending_refresh_alias_dispatches_to_feed_fetch_refresh(monkeypatch, command_text):
    runner = _make_runner(enable_legacy_bare_commands=False)

    def _fake_resolve_feed_subscription_target(raw_target):
        assert raw_target == 'GitHub Trending'
        return 8

    async def _fake_call_script_backend(command, **kwargs):
        assert command[1].endswith('feed_watcher.py')
        assert command[2:] == ['fetch', '--id', '8', '--limit', '15', '--timeout', '12', '--refresh', '--json']
        assert kwargs.get('timeout_sec') == 25
        return LuteResponse(text='GitHub trending refresh result')

    monkeypatch.setattr('gateway.qqbot_lute_handlers._resolve_feed_subscription_target', _fake_resolve_feed_subscription_target)
    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_script_backend', _fake_call_script_backend)

    result = await runner._handle_message(_make_event(command_text, user_id=QQ_ALLOWED_USER))

    assert result == 'GitHub trending refresh result'


@pytest.mark.asyncio
@pytest.mark.parametrize('command_text, expected_id', [
    ('/lute 知乎热搜 --refresh', '6'),
    ('/lute 知乎热搜 refresh', '6'),
    ('/lute B站热搜 --refresh', '7'),
    ('/lute B站热搜 refresh', '7'),
])
async def test_lute_hotlist_aliases_support_explicit_refresh(monkeypatch, command_text, expected_id):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_script_backend(command, **kwargs):
        assert command[1].endswith('feed_watcher.py')
        assert command[2:] == ['fetch', '--id', expected_id, '--limit', '15', '--timeout', '12', '--refresh', '--json']
        assert kwargs.get('timeout_sec') == 25
        return LuteResponse(text='hotlist refresh result')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_script_backend', _fake_call_script_backend)

    result = await runner._handle_message(_make_event(command_text, user_id=QQ_ALLOWED_USER))

    assert result == 'hotlist refresh result'


@pytest.mark.asyncio
@pytest.mark.parametrize('command_text, expected_filter', [
    ('/lute github趋势 ai-tools', 'ai-tools'),
    ('/lute github趋势 ai-agents', 'ai-agents'),
    ('/lute GitHub趋势 AI-TOOLS', 'ai-tools'),
    ('/lute github trending ai-tools', 'ai-tools'),
    ('/lute github trending ai-agents', 'ai-agents'),
])
async def test_lute_github_trending_filtered_aliases_pass_filter_to_script(monkeypatch, command_text, expected_filter):
    runner = _make_runner(enable_legacy_bare_commands=False)

    def _fake_resolve_feed_subscription_target(raw_target):
        assert raw_target == 'GitHub Trending'
        return 8

    async def _fake_call_script_backend(command, **kwargs):
        assert command[1].endswith('feed_watcher.py')
        assert command[2] == 'fetch'
        assert command[3] == '--id'
        assert command[4] == '8'
        assert '--filter' in command
        filter_idx = command.index('--filter')
        assert command[filter_idx + 1] == expected_filter
        return LuteResponse(text=f'Filtered {expected_filter} result')

    monkeypatch.setattr('gateway.qqbot_lute_handlers._resolve_feed_subscription_target', _fake_resolve_feed_subscription_target)
    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_script_backend', _fake_call_script_backend)

    result = await runner._handle_message(_make_event(command_text, user_id=QQ_ALLOWED_USER))

    assert result == f'Filtered {expected_filter} result'


@pytest.mark.asyncio
@pytest.mark.parametrize('command_text', ['/lute ai-news', '/lute ai-news daily'])
async def test_lute_ai_news_generates_pdf_report_only(monkeypatch, command_text):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_script_backend(command, **kwargs):
        assert command[1].endswith('ai_daily_report.py')
        assert command[2:] == ['generate', '--format', 'pdf', '--json']
        assert kwargs.get('timeout_sec') == 180
        return LuteResponse(
            text='AI 早报已生成',
            file_paths=['/tmp/ai-daily.pdf'],
            payload={'title': 'AI 早报已生成', 'pdf_path': '/tmp/ai-daily.pdf', 'file_paths': ['/tmp/ai-daily.pdf']},
        )

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_script_backend', _fake_call_script_backend)

    result = await runner._handle_message(_make_event(command_text, user_id=QQ_ALLOWED_USER))

    assert result.startswith('AI 早报已生成')
    assert 'FILE_PATH=/tmp/ai-daily.pdf' in result
    assert 'FILE_PATH=/tmp/ai-daily.md' not in result


@pytest.mark.asyncio
@pytest.mark.parametrize('command_text', ['/lute AI日报', '/lute AI早报'])
async def test_lute_ai_daily_chinese_aliases_dispatch_to_ai_news(monkeypatch, command_text):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_script_backend(command, **kwargs):
        assert command[1].endswith('ai_daily_report.py')
        assert command[2:] == ['generate', '--format', 'pdf', '--json']
        return LuteResponse(text='AI 早报别名结果')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_script_backend', _fake_call_script_backend)

    result = await runner._handle_message(_make_event(command_text, user_id=QQ_ALLOWED_USER))

    assert result == 'AI 早报别名结果'


@pytest.mark.asyncio
async def test_lute_feed_fetch_direct_id_uses_existing_feed_forward_behavior(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_script_backend(command, **kwargs):
        assert command[2:] == ['fetch', '--id', '26', '--limit', '15', '--timeout', '12', '--json']
        return LuteResponse(
            text='',
            payload={
                'kind': 'feed_fetch',
                'subscription': {'id': 26, 'title': 'V2EX 技术 AI筛选', 'enabled': True},
                'entries': [{'title': 'AI 应用上架咨询', 'summary': '', 'published': '', 'link': 'https://www.v2ex.com/t/1015'}],
            },
        )

    seen = {}

    async def _fake_call_tool_backend(tool_name, args, **kwargs):
        seen['tool_name'] = tool_name
        seen['args'] = args
        return LuteResponse(text='forwarded', payload={'success': True})

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_script_backend', _fake_call_script_backend)
    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_tool_backend', _fake_call_tool_backend)

    result = await runner._handle_message(_make_group_event('/lute feed fetch 26', user_id=QQ_ADMIN))

    assert result == ''
    assert seen['tool_name'] == 'qq_forward_messages'
    assert seen['args']['chat_type'] == 'group'
    assert seen['args']['target_id'] == '1004'
    assert 'V2EX 技术 AI筛选' in seen['args']['messages'][0]['data']['content'][0]['data']['text']
    assert 'AI 应用上架咨询' in seen['args']['messages'][1]['data']['content'][0]['data']['text']


@pytest.mark.asyncio
async def test_lute_feed_list_uses_script_backend(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_script_backend(command, **kwargs):
        assert command[1].endswith('feed_watcher.py')
        assert command[2:] == ['list', '--json']
        return LuteResponse(text='Feed list result')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_script_backend', _fake_call_script_backend)

    result = await runner._handle_message(_make_event('/lute feed list', user_id=QQ_ALLOWED_USER))

    assert result == 'Feed list result'


@pytest.mark.asyncio
async def test_lute_feed_list_sends_merged_forward_to_current_group(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_script_backend(command, **kwargs):
        assert command[2:] == ['list', '--json']
        return LuteResponse(text='', payload={'kind': 'feed_list', 'subscriptions': [{'id': 6, 'title': '知乎热搜', 'enabled': True}]})

    seen = {}

    async def _fake_call_tool_backend(tool_name, args, **kwargs):
        seen['tool_name'] = tool_name
        seen['args'] = args
        return LuteResponse(text='forwarded', payload={'success': True})

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_script_backend', _fake_call_script_backend)
    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_tool_backend', _fake_call_tool_backend)

    result = await runner._handle_message(_make_group_event('/lute feed list', user_id=QQ_ADMIN))

    assert result == ''
    assert seen['tool_name'] == 'qq_forward_messages'
    assert seen['args']['chat_type'] == 'group'
    assert seen['args']['target_id'] == '1004'
    messages = seen['args']['messages']
    assert len(messages) == 2
    assert '订阅列表' in messages[0]['data']['content'][0]['data']['text']
    assert '[6] 知乎热搜' in messages[1]['data']['content'][0]['data']['text']


def test_build_feed_list_forward_messages_preserves_public_domain_names_in_titles():
    from gateway.qqbot_lute_handlers import _build_feed_list_forward_messages

    payload = {'kind': 'feed_list', 'subscriptions': [{'id': 23, 'title': 'Linux.do 人工智能日榜', 'enabled': True}]}

    messages, fallback = _build_feed_list_forward_messages(payload, root='lute')

    assert '[23] Linux.do 人工智能日榜' in messages[1]['data']['content'][0]['data']['text']
    assert '[23] Linux.do 人工智能日榜' in fallback


def test_build_feed_fetch_forward_messages_formats_repoinsider_metadata_readably(monkeypatch):
    from gateway.qqbot_lute_handlers import _build_feed_fetch_forward_messages

    monkeypatch.setattr(
        'gateway.qqbot_lute_handlers.socket.getaddrinfo',
        lambda host, port, proto=0: [(socket.AF_INET, socket.SOCK_STREAM, proto, '', ('1.2.3.4', port))],
    )
    payload = {
        'subscription': {'id': 22, 'title': 'GitHub Trending (RepoInsider)', 'enabled': True},
        'entries': [{
            'title': 'AgriciDaniel/claude-ads',
            'link': 'https://repoinsider.com/repos/AgriciDaniel/claude-ads',
            'summary': '来源: RepoInsider · SIGNAL PICK · Fresh\n分类: AI & LLM Ops\n语言: Python\n指标: Stars 3,457 · Pulse 95 · 增速 7.8x\n简介: AI skill for comprehensive paid advertising audits.',
            'metadata': {
                'source_kind': 'repoinsider',
                'source': 'RepoInsider',
                'badge': 'SIGNAL PICK',
                'status': 'Fresh',
                'category': 'AI & LLM Ops',
                'language': 'Python',
                'stars': '3,457',
                'pulse': '95',
                'velocity': '7.8x',
                'description': 'AI skill for comprehensive paid advertising audits.',
                'why': 'Automatically audit and optimize paid ads across 7 major platforms with AI.',
                'target_audience': 'Growth engineers and agencies',
                'similar_projects': 'LangSmith, Helicone',
                'best_for': 'Paid ads audit, optimization',
            },
        }],
    }

    messages, _fallback = _build_feed_fetch_forward_messages(payload, root='lute')
    text = messages[1]['data']['content'][0]['data']['text']

    assert '摘要: 来源: RepoInsider' not in text
    assert '来源: RepoInsider · SIGNAL PICK · Fresh\n分类: AI & LLM Ops\n语言: Python' in text
    assert 'Why Trending: Automatically audit and optimize paid ads across 7 major platforms with AI.' in text
    assert 'Target Audience: Growth engineers and agencies' in text
    assert 'Best for: Paid ads audit, optimization' in text


def test_lute_feed_payload_fallback_is_sanitized_without_json():
    from gateway.qqbot_lute_handlers import _safe_feed_fallback

    text = _safe_feed_fallback('订阅 https://example.com/rss example.com/path feed://example')

    assert 'http' not in text.lower()
    assert 'example.com' not in text


@pytest.mark.asyncio
async def test_lute_feed_scan_uses_script_backend(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_script_backend(command, **kwargs):
        assert command[1].endswith('feed_watcher.py')
        assert command[2:] == ['scan', '--limit', '5', '--timeout', '8', '--deadline', '38']
        assert kwargs.get('timeout_sec') == 55
        return LuteResponse(text='Feed scan result')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_script_backend', _fake_call_script_backend)

    result = await runner._handle_message(_make_event('/lute feed scan', user_id=QQ_ADMIN))

    assert result == 'Feed scan result'


@pytest.mark.asyncio
async def test_lute_feed_fetch_resolves_named_alias_before_numeric_id(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    monkeypatch.setattr('gateway.qqbot_lute_handlers._resolve_feed_subscription_target', lambda raw: 6 if raw == '知乎热搜' else None)

    async def _fake_call_script_backend(command, **kwargs):
        assert command[1].endswith('feed_watcher.py')
        assert command[2:] == ['fetch', '--id', '6', '--limit', '15', '--timeout', '12', '--json']
        return LuteResponse(text='Feed alias fetch result')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_script_backend', _fake_call_script_backend)

    result = await runner._handle_message(_make_event('/lute feed fetch 知乎热搜', user_id=QQ_ALLOWED_USER))

    assert result == 'Feed alias fetch result'


@pytest.mark.asyncio
async def test_lute_feed_fetch_sends_each_entry_as_forward_node_and_exposes_entry_url(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_script_backend(command, **kwargs):
        assert command[2:] == ['fetch', '--id', '6', '--limit', '15', '--timeout', '12', '--json']
        return LuteResponse(
            text='',
            payload={
                'kind': 'feed_fetch',
                'subscription': {'id': 6, 'title': '知乎热搜', 'enabled': True},
                'entries': [
                    {
                        'title': '如何评价 DeepSeek V4 Pro 官网限时优惠？',
                        'published': 'Sat, 25 Apr 2026 12:35:00 GMT',
                        'summary': '摘要',
                        'link': 'https://www.zhihu.com/question/2031783897910990078',
                    },
                    {
                        'title': '第二条',
                        'published': 'Fri, 24 Apr 2026 10:12:00 GMT',
                        'summary': '',
                        'url': 'https://www.zhihu.com/question/2031783897910999999',
                    },
                ],
            },
        )

    seen = {}

    async def _fake_call_tool_backend(tool_name, args, **kwargs):
        seen['tool_name'] = tool_name
        seen['args'] = args
        return LuteResponse(text='forwarded', payload={'success': True})

    monkeypatch.setattr('gateway.qqbot_lute_handlers._feed_now', lambda: datetime(2026, 4, 25, 21, 0, tzinfo=timezone(timedelta(hours=8))))
    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_script_backend', _fake_call_script_backend)
    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_tool_backend', _fake_call_tool_backend)

    result = await runner._handle_message(_make_group_event('/lute feed fetch 6', user_id=QQ_ADMIN))

    assert result == ''
    assert seen['tool_name'] == 'qq_forward_messages'
    messages = seen['args']['messages']
    assert len(messages) == 3
    assert '知乎热搜' in messages[0]['data']['content'][0]['data']['text']
    first_entry = messages[1]['data']['content'][0]['data']['text']
    second_entry = messages[2]['data']['content'][0]['data']['text']
    assert 'https://www.zhihu.com/question/2031783897910990078' in first_entry
    assert '今天 20:35' in first_entry
    assert 'https://www.zhihu.com/question/2031783897910999999' in second_entry
    assert '昨天 18:12' in second_entry


def test_build_feed_fetch_forward_messages_omits_duplicate_summary_and_blocks_internal_links(monkeypatch):
    from gateway.qqbot_lute_handlers import _build_feed_fetch_forward_messages

    monkeypatch.setattr(
        'gateway.qqbot_lute_handlers.socket.getaddrinfo',
        lambda host, port, proto=0: [(socket.AF_INET, socket.SOCK_STREAM, proto, '', ('1.2.3.4', port))] if host == 'www.bilibili.com' else [(socket.AF_INET, socket.SOCK_STREAM, proto, '', ('127.0.0.1', port))],
    )

    payload = {
        'subscription': {'id': 7, 'title': 'B站热搜', 'enabled': True},
        'entries': [
            {
                'title': '重庆狼队 好厚米',
                'summary': '重庆狼队 好厚米',
                'link': 'https://www.bilibili.com/v/topic/detail/?topic_id=1',
            },
            {
                'title': '内部地址不应暴露',
                'summary': '摘要补充',
                'link': 'http://127.0.0.1:1200/internal-only',
            },
            {
                'title': '内部域名也不应暴露',
                'summary': '摘要补充',
                'link': 'https://feed.internal.example/path',
            },
            {
                'title': '超长标题' * 40,
                'summary': '超长标题' * 40,
                'link': 'https://www.example.com/long-title',
            },
        ],
    }

    messages, fallback = _build_feed_fetch_forward_messages(payload, root='lute')

    first_entry = messages[1]['data']['content'][0]['data']['text']
    second_entry = messages[2]['data']['content'][0]['data']['text']
    third_entry = messages[3]['data']['content'][0]['data']['text']
    fourth_entry = messages[4]['data']['content'][0]['data']['text']
    assert '摘要:' not in first_entry
    assert '链接: https://www.bilibili.com/v/topic/detail/?topic_id=1' in first_entry
    assert '127.0.0.1' not in second_entry
    assert 'internal.example' not in third_entry
    assert '摘要:' not in fourth_entry
    assert '127.0.0.1' not in fallback
    assert 'internal.example' not in fallback


def test_build_feed_fetch_forward_messages_preserves_repoinsider_multiline_summary():
    from gateway.qqbot_lute_handlers import _build_feed_fetch_forward_messages

    payload = {
        'kind': 'feed_fetch',
        'subscription': {'id': 22, 'title': 'GitHub Trending', 'enabled': True},
        'entries': [
            {
                'title': 'AgriciDaniel/claude-ads',
                'summary': '\n'.join([
                    '来源: RepoInsider · SIGNAL PICK · Fresh',
                    '分类: AI & LLM Ops',
                    '语言: Python',
                    '指标: Stars 3,457 · Pulse 96 · 增速 7.8x',
                    '简介: AI skill for comprehensive paid advertising audits.',
                    '上榜原因: Automatically audit and optimize paid ads across 7 major platforms with AI.',
                ]),
                'link': 'https://repoinsider.com/repos/AgriciDaniel/claude-ads',
                'published': '',
                'metadata': {
                    'source_kind': 'repoinsider',
                    'category': 'AI & LLM Ops',
                    'category_slug': 'ai-llm-ops',
                },
            }
        ],
    }

    messages, fallback = _build_feed_fetch_forward_messages(payload, root='lute')

    entry_text = messages[1]['data']['content'][0]['data']['text']
    assert '来源: RepoInsider · SIGNAL PICK · Fresh' in entry_text
    assert '分类: AI & LLM Ops' in entry_text
    assert '语言: Python' in entry_text
    assert '指标: Stars 3,457 · Pulse 96 · 增速 7.8x' in entry_text
    assert '简介: AI skill for comprehensive paid advertising audits.' in entry_text
    assert '上榜原因: Automatically audit and optimize paid ads across 7 major platforms with AI.' in entry_text
    assert '链接: https://repoinsider.com/repos/AgriciDaniel/claude-ads' in entry_text
    assert '\n分类: AI & LLM Ops\n' in fallback


@pytest.mark.asyncio
async def test_lute_feed_fetch_forward_is_capped_at_15_entries(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_script_backend(command, **kwargs):
        return LuteResponse(
            text='',
            payload={
                'kind': 'feed_fetch',
                'subscription': {'id': 6, 'title': '知乎热搜', 'enabled': True},
                'entries': [{'title': f'条目 {idx}', 'summary': '', 'published': '', 'link': f'https://example.com/{idx}'} for idx in range(20)],
            },
        )

    seen = {}

    async def _fake_call_tool_backend(tool_name, args, **kwargs):
        seen['args'] = args
        return LuteResponse(text='forwarded', payload={'success': True})

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_script_backend', _fake_call_script_backend)
    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_tool_backend', _fake_call_tool_backend)

    result = await runner._handle_message(_make_group_event('/lute feed fetch 6', user_id=QQ_ADMIN))

    assert result == ''
    assert len(seen['args']['messages']) == 16
    assert '条目 14' in seen['args']['messages'][-1]['data']['content'][0]['data']['text']
    assert all('条目 15' not in node['data']['content'][0]['data']['text'] for node in seen['args']['messages'])


def test_format_feed_published_uses_friendly_cn_relative_time(monkeypatch):
    from gateway.qqbot_lute_handlers import _format_feed_published

    monkeypatch.setattr('gateway.qqbot_lute_handlers._feed_now', lambda: datetime(2026, 4, 25, 21, 0, tzinfo=timezone(timedelta(hours=8))))

    assert _format_feed_published('Sat, 25 Apr 2026 12:35:00 GMT') == '今天 20:35'
    assert _format_feed_published('Fri, 24 Apr 2026 10:12:00 GMT') == '昨天 18:12'
    assert _format_feed_published('Thu, 23 Apr 2026 01:30:00 GMT') == '周四 09:30'
    assert _format_feed_published('Sat, 18 Apr 2026 14:10:00 GMT') == '4月18日 22:10'
    assert _format_feed_published('2026-04-25T12:35:00Z') == '今天 20:35'
    assert _format_feed_published('2026-04-24T10:12:00+00:00') == '昨天 18:12'
    assert _format_feed_published('2026-04-26T10:12:00+00:00') == '明天 18:12'
    assert _format_feed_published('2027-01-02T00:00:00+00:00') == '2027年1月2日 08:00'


@pytest.mark.asyncio
async def test_dispatch_lute_feed_list_embeds_view_feed_render_config(monkeypatch):
    cli = QQBotCLIConfig(enable_legacy_bare_commands=False)
    config = QQBotConfig(
        enabled=True,
        access=QQBotAccessConfig(admins=[QQ_ADMIN], allow_users=[QQ_ALLOWED_USER]),
        cli=cli,
        view=QQBotViewConfig(
            feed_card=QQBotFeedCardRenderConfig(
                quality=61,
                scale_factor=1.25,
                min_width=900,
                min_height=520,
                max_width=1300,
                max_height=2600,
                font_family='Noto Sans CJK SC, Microsoft YaHei, sans-serif',
            )
        ),
    )
    policy = QQBotPolicy(config)
    registry = build_default_lute_registry(cli)
    root_commands = build_default_lute_root_commands(cli)

    async def _fake_call_script_backend(command, **kwargs):
        assert command[2:] == ['list', '--json']
        return LuteResponse(text='', payload={'kind': 'feed_list', 'subscriptions': [{'id': 6, 'title': '知乎热搜', 'enabled': True}]})

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_script_backend', _fake_call_script_backend)

    result = await dispatch_lute_invocation(
        LuteInvocation(root='lute', domain='feed', verb='list', args=[], options={}),
        policy=policy,
        registry=registry,
        root_commands=root_commands,
        cli=cli,
        user_id=QQ_ALLOWED_USER,
    )

    assert result.response is not None
    assert result.response.view is None
    assert '[6] 知乎热搜' in result.response.text


@pytest.mark.asyncio
async def test_lute_feed_fetch_without_target_returns_structured_usage():
    runner = _make_runner(enable_legacy_bare_commands=False)

    result = await runner._handle_message(_make_event('/lute feed fetch', user_id=QQ_ALLOWED_USER))

    assert result == _FEED_FETCH_USAGE


@pytest.mark.asyncio
async def test_lute_image_search_uses_script_backend(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_script_backend(command, **kwargs):
        assert command[1].endswith('reverse_search.py')
        assert command[2:] == ['--image-url', 'https://example.com/image.jpg', '--json']
        return LuteResponse(text='Image search result')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_script_backend', _fake_call_script_backend)

    result = await runner._handle_message(_make_event('/lute image search https://example.com/image.jpg', user_id=QQ_ALLOWED_USER))

    assert result == 'Image search result'


@pytest.mark.asyncio
async def test_lute_image_search_without_source_returns_usage():
    runner = _make_runner(enable_legacy_bare_commands=False)

    result = await runner._handle_message(_make_event('/lute image search', user_id=QQ_ALLOWED_USER))

    assert result == 'Usage: /lute image search <图片URL或本地路径>'


@pytest.mark.asyncio
async def test_lute_image_search_local_path_uses_image_path_backend(monkeypatch, tmp_path):
    runner = _make_runner(enable_legacy_bare_commands=False)
    image_path = tmp_path / 'example.jpg'
    image_path.write_text('fake-image', encoding='utf-8')

    async def _fake_call_script_backend(command, **kwargs):
        assert command[1].endswith('reverse_search.py')
        assert command[2:] == ['--image-path', str(image_path), '--json']
        return LuteResponse(text='Local image search result')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_script_backend', _fake_call_script_backend)

    result = await runner._handle_message(_make_event(f'/lute image search {image_path}', user_id=QQ_ALLOWED_USER))

    assert result == 'Local image search result'


def test_resolve_feed_subscription_target_matches_titles_and_tags(tmp_path, monkeypatch):
    import sqlite3
    from gateway import qqbot_lute_handlers as handlers

    db_path = tmp_path / 'feed-watcher.db'
    conn = sqlite3.connect(db_path)
    conn.execute('CREATE TABLE subscriptions (id INTEGER PRIMARY KEY, title TEXT, tags TEXT)')
    conn.execute('INSERT INTO subscriptions (id, title, tags) VALUES (1, ?, ?)', ('知乎热搜', 'zhihu,hot'))
    conn.execute('INSERT INTO subscriptions (id, title, tags) VALUES (2, ?, ?)', ('少数派精选', 'sspai,tech'))
    conn.execute('INSERT INTO subscriptions (id, title, tags) VALUES (3, ?, ?)', ('游戏资讯', 'epic,games'))
    conn.commit()
    conn.close()

    monkeypatch.setattr(handlers, 'FEED_WATCHER_DB', db_path)

    assert handlers._resolve_feed_subscription_target('知乎热搜') == 1
    assert handlers._resolve_feed_subscription_target('少数派') == 2
    assert handlers._resolve_feed_subscription_target('games') == 3


@pytest.mark.asyncio
async def test_lute_image_search_rejects_base64_input_for_now():
    runner = _make_runner(enable_legacy_bare_commands=False)

    result = await runner._handle_message(_make_event('/lute image search base64://abc123', user_id=QQ_ALLOWED_USER))

    assert result == 'Usage: /lute image search <图片URL或本地路径>'


@pytest.mark.asyncio
async def test_lute_pixiv_search_uses_script_backend(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_script_backend(command, **kwargs):
        assert command[1].endswith('pixiv.py')
        assert command[2] == 'search'
        assert command[3:5] == ['--keyword', '初音ミク']
        assert '--json' in command
        return LuteResponse(text='Pixiv search result')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_script_backend', _fake_call_script_backend)

    result = await runner._handle_message(_make_event('/lute pixiv search 初音ミク', user_id=QQ_ALLOWED_USER))

    assert result == 'Pixiv search result'


@pytest.mark.asyncio
async def test_lute_pixiv_search_without_keyword_returns_structured_usage():
    runner = _make_runner(enable_legacy_bare_commands=False)

    result = await runner._handle_message(_make_event('/lute pixiv search', user_id=QQ_ALLOWED_USER))

    assert result == _PIXIV_SEARCH_USAGE


@pytest.mark.asyncio
async def test_lute_pixiv_rank_uses_script_backend(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_script_backend(command, **kwargs):
        assert command[1].endswith('pixiv.py')
        assert command[2:] == ['rank', '--type', 'day', '--json']
        return LuteResponse(text='Pixiv rank result')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_script_backend', _fake_call_script_backend)

    result = await runner._handle_message(_make_event('/lute pixiv rank day', user_id=QQ_ALLOWED_USER))

    assert result == 'Pixiv rank result'


@pytest.mark.asyncio
async def test_lute_pixiv_illust_uses_script_backend(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_script_backend(command, **kwargs):
        assert command[1].endswith('pixiv.py')
        assert command[2:] == ['illust', '--id', '1000', '--json']
        return LuteResponse(text='Pixiv illust result')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_script_backend', _fake_call_script_backend)

    result = await runner._handle_message(_make_event('/lute pixiv illust 1000', user_id=QQ_ALLOWED_USER))

    assert result == 'Pixiv illust result'


@pytest.mark.asyncio
async def test_lute_pixiv_illust_without_id_returns_structured_usage():
    runner = _make_runner(enable_legacy_bare_commands=False)

    result = await runner._handle_message(_make_event('/lute pixiv illust', user_id=QQ_ALLOWED_USER))

    assert result == _PIXIV_ILLUST_USAGE


@pytest.mark.asyncio
async def test_lute_pixiv_related_uses_script_backend(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_script_backend(command, **kwargs):
        assert command[1].endswith('pixiv.py')
        assert command[2:] == ['related', '--id', '1000', '--limit', '5', '--json']
        return LuteResponse(text='Pixiv related result')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_script_backend', _fake_call_script_backend)

    result = await runner._handle_message(_make_event('/lute pixiv related 1000', user_id=QQ_ALLOWED_USER))

    assert result == 'Pixiv related result'


@pytest.mark.asyncio
async def test_lute_pixiv_related_without_id_returns_structured_usage():
    runner = _make_runner(enable_legacy_bare_commands=False)

    result = await runner._handle_message(_make_event('/lute pixiv related', user_id=QQ_ALLOWED_USER))

    assert result == _PIXIV_RELATED_USAGE


@pytest.mark.asyncio
async def test_lute_pixiv_user_uses_tool_backend(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_tool_backend(tool_name, args, **kwargs):
        assert tool_name == 'mcp_pixiv_search_user'
        assert args == {'word': '米山舞', 'limit': 5, 'view': 'raw'}
        return LuteResponse(
            text='ignored',
            payload={
                'ok': True,
                'user_previews': [
                    {'user': {'id': 1, 'name': '米山舞', 'account': 'yoneyamai'}},
                    {'user': {'id': 2, 'name': 'Sob', 'account': 'sob'}}
                ]
            },
        )

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_tool_backend', _fake_call_tool_backend)

    result = await runner._handle_message(_make_event('/lute pixiv user 米山舞', user_id=QQ_ALLOWED_USER))

    assert result == 'Unknown /lute verb: user\nTry: /lute help pixiv'


@pytest.mark.asyncio
async def test_lute_torrent_search_uses_movie_search_script_fast_path(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_script_backend(command, **kwargs):
        assert command[1].endswith('movie_search.py')
        assert command[2] == 'search'
        assert command[3] == 'shogun s01e05'
        assert command[command.index('--limit') + 1] == '5'
        assert command[command.index('--availability') + 1] == 'available'
        assert '--compact' in command
        assert command[-1] == '--json'
        return LuteResponse(text='TorrentClaw result')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_script_backend', _fake_call_script_backend)

    result = await runner._handle_message(_make_event('/lute torrent search shogun s01e05', user_id=QQ_ALLOWED_USER))

    assert result == 'TorrentClaw result'


@pytest.mark.asyncio
async def test_lute_torrent_search_passes_optional_filters_to_movie_search_script(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_script_backend(command, **kwargs):
        assert command[1].endswith('movie_search.py')
        assert command[2] == 'search'
        assert command[3] == 'shogun'
        assert command[command.index('--season') + 1] == '1'
        assert command[command.index('--episode') + 1] == '5'
        assert command[command.index('--quality') + 1] == '1080p'
        assert command[command.index('--language') + 1] == 'en'
        return LuteResponse(text='Torrent filtered result')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_script_backend', _fake_call_script_backend)

    result = await runner._handle_message(_make_event('/lute torrent search shogun --season 1 --episode 5 --quality 1080p --language en', user_id=QQ_ALLOWED_USER))

    assert result == 'Torrent filtered result'


@pytest.mark.asyncio
async def test_lute_torrent_search_without_keyword_returns_structured_usage():
    runner = _make_runner(enable_legacy_bare_commands=False)

    result = await runner._handle_message(_make_event('/lute torrent search', user_id=QQ_ALLOWED_USER))

    assert result == _TORRENT_SEARCH_USAGE


@pytest.mark.asyncio
async def test_lute_torrent_search_invalid_season_returns_structured_usage():
    runner = _make_runner(enable_legacy_bare_commands=False)

    result = await runner._handle_message(_make_event('/lute torrent search shogun --season nope', user_id=QQ_ALLOWED_USER))

    assert result == _TORRENT_SEARCH_USAGE


@pytest.mark.asyncio
async def test_lute_torrent_stream_uses_movie_search_script(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_script_backend(command, **kwargs):
        assert command[1].endswith('movie_search.py')
        assert command[2:] == ['stream', '42', '--country', 'US', '--json']
        return LuteResponse(text='Torrent stream result')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_script_backend', _fake_call_script_backend)

    result = await runner._handle_message(_make_event('/lute torrent stream 42 --country US', user_id=QQ_ALLOWED_USER))

    assert result == 'Torrent stream result'


@pytest.mark.asyncio
async def test_lute_torrent_stream_without_id_returns_structured_usage():
    runner = _make_runner(enable_legacy_bare_commands=False)

    result = await runner._handle_message(_make_event('/lute torrent stream', user_id=QQ_ALLOWED_USER))

    assert result == _TORRENT_STREAM_USAGE


@pytest.mark.asyncio
async def test_lute_torrent_fallback_uses_movie_search_script_explicitly(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_script_backend(command, **kwargs):
        assert command[1].endswith('movie_search.py')
        assert command[2:] == ['fallback', 'shogun s01e05', '--json']
        return LuteResponse(text='Fallback result')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_script_backend', _fake_call_script_backend)

    result = await runner._handle_message(_make_event('/lute torrent fallback shogun s01e05', user_id=QQ_ALLOWED_USER))

    assert result == 'Fallback result'


@pytest.mark.asyncio
async def test_lute_torrent_fallback_without_keyword_returns_structured_usage():
    runner = _make_runner(enable_legacy_bare_commands=False)

    result = await runner._handle_message(_make_event('/lute torrent fallback', user_id=QQ_ALLOWED_USER))

    assert result == _TORRENT_FALLBACK_USAGE


@pytest.mark.asyncio
async def test_lute_torrent_analyze_uses_script_backend(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_script_backend(command, **kwargs):
        assert command[1].endswith('torrent_analyze.py')
        assert command[2:] == ['analyze', 'magnet:?xt=urn:btih:abc', '--json']
        return LuteResponse(text='Torrent analyze result')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_script_backend', _fake_call_script_backend)

    result = await runner._handle_message(_make_event('/lute torrent analyze magnet:?xt=urn:btih:abc', user_id=QQ_ALLOWED_USER))

    assert result == 'Torrent analyze result'


@pytest.mark.asyncio
async def test_lute_torrent_analyze_without_target_returns_structured_usage():
    runner = _make_runner(enable_legacy_bare_commands=False)

    result = await runner._handle_message(_make_event('/lute torrent analyze', user_id=QQ_ALLOWED_USER))

    assert result == _TORRENT_ANALYZE_USAGE


@pytest.mark.asyncio
async def test_lute_book_search_uses_script_backend(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_script_backend(command, **kwargs):
        assert command[1].endswith('zlibrary.py')
        assert command[2:] == ['search', 'domain-driven design', '--count', '5', '--json']
        return LuteResponse(text='Book search result')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_script_backend', _fake_call_script_backend)

    result = await runner._handle_message(_make_event('/lute book search domain-driven design', user_id=QQ_ALLOWED_USER))

    assert result == 'Book search result'


@pytest.mark.asyncio
async def test_lute_book_search_without_keyword_returns_structured_usage():
    runner = _make_runner(enable_legacy_bare_commands=False)

    result = await runner._handle_message(_make_event('/lute book search', user_id=QQ_ALLOWED_USER))

    assert result == _BOOK_SEARCH_USAGE


@pytest.mark.asyncio
async def test_lute_book_recent_uses_script_backend(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_script_backend(command, **kwargs):
        assert command[1].endswith('zlibrary.py')
        assert command[2:] == ['recent', '--count', '5', '--json']
        return LuteResponse(text='Book recent result')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_script_backend', _fake_call_script_backend)

    result = await runner._handle_message(_make_event('/lute book recent', user_id=QQ_ALLOWED_USER))

    assert result == 'Book recent result'


@pytest.mark.asyncio
async def test_lute_book_limits_uses_script_backend(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_script_backend(command, **kwargs):
        assert command[1].endswith('zlibrary.py')
        assert command[2:] == ['limits', '--json']
        return LuteResponse(text='Book limits result')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_script_backend', _fake_call_script_backend)

    result = await runner._handle_message(_make_event('/lute book limits', user_id=QQ_ALLOWED_USER))

    assert result == 'Book limits result'


@pytest.mark.asyncio
async def test_lute_book_metadata_uses_script_backend(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_script_backend(command, **kwargs):
        assert command[1].endswith('zlibrary.py')
        assert command[2:] == ['metadata', '--book-id', '12345', '--book-hash', 'abc123', '--json']
        return LuteResponse(text='Book metadata result')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_script_backend', _fake_call_script_backend)

    result = await runner._handle_message(_make_event('/lute book metadata 12345 --hash abc123', user_id=QQ_ALLOWED_USER))

    assert result == 'Book metadata result'


@pytest.mark.asyncio
async def test_lute_book_metadata_without_hash_returns_structured_usage():
    runner = _make_runner(enable_legacy_bare_commands=False)

    result = await runner._handle_message(_make_event('/lute book metadata 12345', user_id=QQ_ALLOWED_USER))

    assert result == _BOOK_METADATA_USAGE


@pytest.mark.asyncio
async def test_lute_utility_weather_uses_script_backend(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_script_backend(command, **kwargs):
        assert command[1].endswith('uapi_toolbox.py')
        assert command[2:] == ['--json', 'weather', '北京']
        return LuteResponse(text='Weather result')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_script_backend', _fake_call_script_backend)

    result = await runner._handle_message(_make_event('/lute utility weather 北京', user_id=QQ_ALLOWED_USER))

    assert result == 'Weather result'


@pytest.mark.asyncio
async def test_lute_utility_qr_uses_script_backend(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_script_backend(command, **kwargs):
        assert command[1].endswith('uapi_toolbox.py')
        assert command[2:] == ['--json', 'qrcode', 'https://example.com']
        return LuteResponse(text='QR result')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_script_backend', _fake_call_script_backend)

    result = await runner._handle_message(_make_event('/lute utility qr https://example.com', user_id=QQ_ALLOWED_USER))

    assert result == 'QR result'


@pytest.mark.asyncio
async def test_lute_utility_github_uses_script_backend(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_script_backend(command, **kwargs):
        assert command[1].endswith('uapi_toolbox.py')
        assert command[2:] == ['--json', 'github', 'owner/repo']
        return LuteResponse(text='GitHub result')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_script_backend', _fake_call_script_backend)

    result = await runner._handle_message(_make_event('/lute utility github owner/repo', user_id=QQ_ALLOWED_USER))

    assert result == 'GitHub result'


@pytest.mark.asyncio
async def test_lute_utility_whois_uses_script_backend(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_script_backend(command, **kwargs):
        assert command[1].endswith('uapi_toolbox.py')
        assert command[2:] == ['--json', 'whois', 'example.com']
        return LuteResponse(text='WHOIS result')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_script_backend', _fake_call_script_backend)

    result = await runner._handle_message(_make_event('/lute utility whois example.com', user_id=QQ_ALLOWED_USER))

    assert result == 'WHOIS result'


@pytest.mark.asyncio
async def test_lute_utility_news_uses_script_backend(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_script_backend(command, **kwargs):
        assert command[1].endswith('uapi_toolbox.py')
        assert command[2:] == ['--json', 'news']
        return LuteResponse(text='News result')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_script_backend', _fake_call_script_backend)

    result = await runner._handle_message(_make_event('/lute utility news', user_id=QQ_ALLOWED_USER))

    assert result == 'News result'


@pytest.mark.asyncio
async def test_lute_feed_add_is_denied_for_non_admin_users():
    runner = _make_runner(enable_legacy_bare_commands=False)

    result = await runner._handle_message(_make_event('/lute feed add https://example.com/rss --title 示例订阅', user_id=QQ_ALLOWED_USER))

    assert result == ''


@pytest.mark.asyncio
async def test_lute_feed_add_uses_script_backend_for_admin(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_script_backend(command, **kwargs):
        assert command[1].endswith('feed_watcher.py')
        assert command[2:] == ['add', '--url', 'https://example.com/rss', '--title', '示例订阅']
        return LuteResponse(text='Feed add result')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_script_backend', _fake_call_script_backend)

    result = await runner._handle_message(_make_event('/lute feed add https://example.com/rss --title 示例订阅', user_id=QQ_ADMIN))

    assert result == 'Feed add result'


@pytest.mark.asyncio
async def test_lute_feed_add_without_url_returns_structured_usage_for_admin():
    runner = _make_runner(enable_legacy_bare_commands=False)

    result = await runner._handle_message(_make_event('/lute feed add', user_id=QQ_ADMIN))

    assert result == _FEED_ADD_USAGE


@pytest.mark.asyncio
async def test_lute_pixiv_download_is_denied_for_non_admin_users():
    runner = _make_runner(enable_legacy_bare_commands=False)

    result = await runner._handle_message(_make_event('/lute pixiv download 1000', user_id=QQ_ALLOWED_USER))

    assert result == ''


@pytest.mark.asyncio
async def test_lute_pixiv_download_uses_script_backend_for_admin(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_script_backend(command, **kwargs):
        assert command[1].endswith('pixiv.py')
        assert command[2:] == ['download', '--id', '1000', '--json']
        return LuteResponse(text='Pixiv download result')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_script_backend', _fake_call_script_backend)

    result = await runner._handle_message(_make_event('/lute pixiv download 1000', user_id=QQ_ADMIN))

    assert result == 'Pixiv download result'


@pytest.mark.asyncio
async def test_lute_pixiv_download_without_id_returns_structured_usage_for_admin():
    runner = _make_runner(enable_legacy_bare_commands=False)

    result = await runner._handle_message(_make_event('/lute pixiv download', user_id=QQ_ADMIN))

    assert result == _PIXIV_DOWNLOAD_USAGE


@pytest.mark.asyncio
async def test_lute_service_runtime_status_uses_script_backend_for_admin(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_script_backend(command, **kwargs):
        assert command[1].endswith('astrbot_apis.py')
        assert command[2:] == ['runtime-status', '--json']
        return LuteResponse(text='Service runtime result')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_script_backend', _fake_call_script_backend)

    result = await runner._handle_message(_make_event('/lute service runtime status', user_id=QQ_ADMIN))

    assert result == 'Service runtime result'


@pytest.mark.asyncio
async def test_lute_service_runtime_show_alias_uses_script_backend_for_admin(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_script_backend(command, **kwargs):
        assert command[1].endswith('astrbot_apis.py')
        assert command[2:] == ['runtime-status', '--json']
        return LuteResponse(text='Service runtime result')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_script_backend', _fake_call_script_backend)

    result = await runner._handle_message(_make_event('/lute service runtime show', user_id=QQ_ADMIN))

    assert result == 'Service runtime result'


@pytest.mark.asyncio
async def test_lute_service_match_refuses_sensitive_candidate_without_explicit_scope(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    monkeypatch.setattr(
        'gateway.qqbot_lute_handlers._load_service_catalog',
        lambda: (
            [
                {'name': '普通壁纸', 'site': 'safe.example', 'api_type': 'image', 'enabled': True, 'keywords': ['wallpaper']},
                {'name': '敏感壁纸', 'site': 'sensitive.example', 'api_type': 'image', 'enabled': True, 'keywords': ['wallpaper', 'nsfw']},
            ],
            ['nsfw'],
        ),
    )

    result = await runner._handle_message(_make_event('/lute service match wallpaper', user_id=QQ_ADMIN))

    assert result == 'Refusing to run a sensitive service entry without --scope sensitive'


@pytest.mark.asyncio
async def test_lute_service_match_allows_sensitive_candidate_with_explicit_scope(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    monkeypatch.setattr(
        'gateway.qqbot_lute_handlers._load_service_catalog',
        lambda: (
            [
                {'name': '敏感壁纸', 'site': 'sensitive.example', 'api_type': 'image', 'enabled': True, 'keywords': ['wallpaper', 'nsfw']},
            ],
            ['nsfw'],
        ),
    )

    async def _fake_call_script_backend(command, **kwargs):
        assert command[1].endswith('astrbot_apis.py')
        assert command[2:] == ['match-api', 'wallpaper', '--json']
        return LuteResponse(text='Service match result')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_script_backend', _fake_call_script_backend)

    result = await runner._handle_message(_make_event('/lute service match wallpaper --scope sensitive', user_id=QQ_ADMIN))

    assert result == 'Service match result'


@pytest.mark.asyncio
async def test_lute_group_info_uses_tool_backend_for_admin(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_tool_backend(tool_name, args, **kwargs):
        assert tool_name == 'qq_get_group_info'
        assert args == {'group_id': '1004'}
        return LuteResponse(text='Group info result')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_tool_backend', _fake_call_tool_backend)

    result = await runner._handle_message(_make_event('/lute group info 1004', user_id=QQ_ADMIN))

    assert result == 'Usage: /lute group info'


@pytest.mark.asyncio
async def test_lute_group_info_defaults_to_current_group_id(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_tool_backend(tool_name, args, **kwargs):
        assert tool_name == 'qq_get_group_info'
        assert args == {'group_id': '1004'}
        return LuteResponse(text='Group info result')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_tool_backend', _fake_call_tool_backend)

    result = await runner._handle_message(_make_group_event('/lute group info', user_id=QQ_ADMIN))

    assert result == 'Group info result'


@pytest.mark.asyncio
async def test_lute_group_info_rejects_cross_group_target_in_group_context():
    runner = _make_runner(enable_legacy_bare_commands=False)

    result = await runner._handle_message(_make_group_event('/lute group info 1016', user_id=QQ_ADMIN))

    assert result == 'Usage: /lute group info'


@pytest.mark.asyncio
async def test_lute_group_admin_show_defaults_to_current_group_id_and_formats_summary(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_tool_backend(tool_name, args, **kwargs):
        assert tool_name == 'qq_group_admin_get_config'
        assert args == {'group_id': '1004'}
        return LuteResponse(
            text='Loaded qqadmin config for group 1004.',
            payload={
                'success': True,
                'group_id': '1004',
                'config': {
                    'builtin_ban': True,
                    'custom_ban_words': ['广告', 'spam'],
                    'word_ban_time': 600,
                    'spamming_ban_time': 900,
                    'join_switch': True,
                    'join_accept_words': ['答案'],
                    'join_reject_words': ['广告'],
                    'block_ids': ['1013'],
                    'join_welcome': '欢迎入群',
                    'leave_notify': True,
                    'leave_block': False,
                    'curfew_enabled': True,
                    'curfew_start': '23:00',
                    'curfew_end': '06:00',
                },
            },
        )

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_tool_backend', _fake_call_tool_backend)

    result = await runner._handle_message(_make_group_event('/lute group admin show', user_id=QQ_ADMIN))

    assert result == (
        'QQAdmin config for current group 1004\n'
        'Moderation: builtin=on, words=2, word-ban=600, spam-ban=900\n'
        'Join: switch=on, accept=1, reject=1, blacklist=1, welcome=set\n'
        'Leave: notify=on, block=off, kick-block=off\n'
        'Curfew: enabled 23:00-06:00'
    )


@pytest.mark.asyncio
async def test_lute_group_admin_status_alias_uses_same_summary(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_tool_backend(tool_name, args, **kwargs):
        assert tool_name == 'qq_group_admin_get_config'
        return LuteResponse(
            text='raw',
            payload={
                'group_id': '1004',
                'config': {
                    'builtin_ban': False,
                    'custom_ban_words': [],
                    'word_ban_time': 0,
                    'spamming_ban_time': 0,
                    'join_switch': False,
                    'join_accept_words': [],
                    'join_reject_words': [],
                    'block_ids': [],
                    'join_welcome': '',
                    'leave_notify': False,
                    'leave_block': False,
                    'kick_block': True,
                    'curfew_enabled': False,
                    'curfew_start': '',
                    'curfew_end': '',
                },
            },
        )

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_tool_backend', _fake_call_tool_backend)

    result = await runner._handle_message(_make_group_event('/lute group admin status', user_id=QQ_ADMIN))

    assert result == (
        'QQAdmin config for current group 1004\n'
        'Moderation: builtin=off, words=0, word-ban=0, spam-ban=0\n'
        'Join: switch=off, accept=0, reject=0, blacklist=0, welcome=clear\n'
        'Leave: notify=off, block=off, kick-block=on\n'
        'Curfew: disabled'
    )


@pytest.mark.asyncio
async def test_lute_group_admin_show_requires_current_group_context():
    runner = _make_runner(enable_legacy_bare_commands=False)

    result = await runner._handle_message(_make_event('/lute group admin show', user_id=QQ_ADMIN))

    assert result == _GROUP_ADMIN_SHOW_USAGE


@pytest.mark.asyncio
async def test_lute_group_admin_reset_uses_backend_for_current_group(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_tool_backend(tool_name, args, **kwargs):
        assert tool_name == 'qq_group_admin_reset_config'
        assert args == {'group_id': '1004'}
        return LuteResponse(text='Reset qqadmin config for group 1004.')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_tool_backend', _fake_call_tool_backend)

    result = await runner._handle_message(_make_group_event('/lute group admin reset', user_id=QQ_ADMIN))

    assert result == 'Reset qqadmin config for group 1004.'


@pytest.mark.asyncio
async def test_lute_group_admin_moderation_show_formats_current_group_slice(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_tool_backend(tool_name, args, **kwargs):
        assert tool_name == 'qq_group_admin_get_config'
        assert args == {'group_id': '1004'}
        return LuteResponse(
            text='Loaded qqadmin config for group 1004.',
            payload={
                'success': True,
                'group_id': '1004',
                'config': {
                    'builtin_ban': False,
                    'custom_ban_words': ['广告', 'spam'],
                    'word_ban_time': 300,
                    'spamming_ban_time': 1200,
                },
            },
        )

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_tool_backend', _fake_call_tool_backend)

    result = await runner._handle_message(_make_group_event('/lute group admin moderation show', user_id=QQ_ADMIN))

    assert result == (
        'Moderation config for current group 1004\n'
        'builtin: off\n'
        'words (2): 广告, spam\n'
        'word-ban: 300\n'
        'spam-ban: 1200'
    )


@pytest.mark.asyncio
async def test_lute_group_admin_moderation_builtin_on_updates_config(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_tool_backend(tool_name, args, **kwargs):
        assert tool_name == 'qq_group_admin_update_config'
        assert args == {'group_id': '1004', 'updates': {'builtin_ban': True}}
        return LuteResponse(text='Updated qqadmin config for group 1004.')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_tool_backend', _fake_call_tool_backend)

    result = await runner._handle_message(_make_group_event('/lute group admin moderation builtin on', user_id=QQ_ADMIN))

    assert result == 'Updated qqadmin config for group 1004.'


@pytest.mark.asyncio
async def test_lute_group_admin_moderation_builtin_off_updates_config(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_tool_backend(tool_name, args, **kwargs):
        assert tool_name == 'qq_group_admin_update_config'
        assert args == {'group_id': '1004', 'updates': {'builtin_ban': False}}
        return LuteResponse(text='Updated qqadmin config for group 1004.')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_tool_backend', _fake_call_tool_backend)

    result = await runner._handle_message(_make_group_event('/lute group admin moderation builtin off', user_id=QQ_ADMIN))

    assert result == 'Updated qqadmin config for group 1004.'


@pytest.mark.asyncio
async def test_lute_group_admin_moderation_builtin_requires_on_or_off():
    runner = _make_runner(enable_legacy_bare_commands=False)

    result = await runner._handle_message(_make_group_event('/lute group admin moderation builtin maybe', user_id=QQ_ADMIN))

    assert result == _GROUP_ADMIN_MODERATION_USAGE


@pytest.mark.asyncio
async def test_lute_group_admin_moderation_words_set_updates_config(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_tool_backend(tool_name, args, **kwargs):
        assert tool_name == 'qq_group_admin_update_config'
        assert args == {'group_id': '1004', 'updates': {'custom_ban_words': ['广告', 'spam', '刷屏']}}
        return LuteResponse(text='Updated qqadmin config for group 1004.')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_tool_backend', _fake_call_tool_backend)

    result = await runner._handle_message(_make_group_event('/lute group admin moderation words set 广告 spam 广告 刷屏', user_id=QQ_ADMIN))

    assert result == 'Updated qqadmin config for group 1004.'


@pytest.mark.asyncio
async def test_lute_group_admin_moderation_words_add_reads_then_updates_config(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)
    calls = []

    async def _fake_call_tool_backend(tool_name, args, **kwargs):
        calls.append((tool_name, args))
        if tool_name == 'qq_group_admin_get_config':
            return LuteResponse(
                text='Loaded qqadmin config for group 1004.',
                payload={
                    'success': True,
                    'group_id': '1004',
                    'config': {'custom_ban_words': ['广告', 'spam']},
                },
            )
        assert tool_name == 'qq_group_admin_update_config'
        assert args == {'group_id': '1004', 'updates': {'custom_ban_words': ['广告', 'spam', '刷屏', '引流']}}
        return LuteResponse(text='Updated qqadmin config for group 1004.')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_tool_backend', _fake_call_tool_backend)

    result = await runner._handle_message(_make_group_event('/lute group admin moderation words add spam 刷屏 引流', user_id=QQ_ADMIN))

    assert [name for name, _ in calls] == ['qq_group_admin_get_config', 'qq_group_admin_update_config']
    assert result == 'Updated qqadmin config for group 1004.'


@pytest.mark.asyncio
async def test_lute_group_admin_moderation_words_remove_reads_then_updates_config(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)
    calls = []

    async def _fake_call_tool_backend(tool_name, args, **kwargs):
        calls.append((tool_name, args))
        if tool_name == 'qq_group_admin_get_config':
            return LuteResponse(
                text='Loaded qqadmin config for group 1004.',
                payload={
                    'success': True,
                    'group_id': '1004',
                    'config': {'custom_ban_words': ['广告', 'spam', '刷屏']},
                },
            )
        assert tool_name == 'qq_group_admin_update_config'
        assert args == {'group_id': '1004', 'updates': {'custom_ban_words': ['广告']}}
        return LuteResponse(text='Updated qqadmin config for group 1004.')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_tool_backend', _fake_call_tool_backend)

    result = await runner._handle_message(_make_group_event('/lute group admin moderation words remove spam 不存在 刷屏 spam', user_id=QQ_ADMIN))

    assert [name for name, _ in calls] == ['qq_group_admin_get_config', 'qq_group_admin_update_config']
    assert result == 'Updated qqadmin config for group 1004.'


@pytest.mark.asyncio
async def test_lute_group_admin_moderation_word_ban_updates_config(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_tool_backend(tool_name, args, **kwargs):
        assert tool_name == 'qq_group_admin_update_config'
        assert args == {'group_id': '1004', 'updates': {'word_ban_time': 600}}
        return LuteResponse(text='Updated qqadmin config for group 1004.')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_tool_backend', _fake_call_tool_backend)

    result = await runner._handle_message(_make_group_event('/lute group admin moderation word-ban 600', user_id=QQ_ADMIN))

    assert result == 'Updated qqadmin config for group 1004.'


@pytest.mark.asyncio
async def test_lute_group_admin_moderation_spam_ban_updates_config(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_tool_backend(tool_name, args, **kwargs):
        assert tool_name == 'qq_group_admin_update_config'
        assert args == {'group_id': '1004', 'updates': {'spamming_ban_time': 1200}}
        return LuteResponse(text='Updated qqadmin config for group 1004.')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_tool_backend', _fake_call_tool_backend)

    result = await runner._handle_message(_make_group_event('/lute group admin moderation spam-ban 1200', user_id=QQ_ADMIN))

    assert result == 'Updated qqadmin config for group 1004.'


@pytest.mark.asyncio
async def test_lute_group_admin_moderation_word_ban_requires_integer():
    runner = _make_runner(enable_legacy_bare_commands=False)

    result = await runner._handle_message(_make_group_event('/lute group admin moderation word-ban abc', user_id=QQ_ADMIN))

    assert result == _GROUP_ADMIN_MODERATION_USAGE


@pytest.mark.asyncio
async def test_lute_group_admin_moderation_spam_ban_requires_integer():
    runner = _make_runner(enable_legacy_bare_commands=False)

    result = await runner._handle_message(_make_group_event('/lute group admin moderation spam-ban abc', user_id=QQ_ADMIN))

    assert result == _GROUP_ADMIN_MODERATION_USAGE


@pytest.mark.asyncio
async def test_lute_group_admin_moderation_words_requires_terms():
    runner = _make_runner(enable_legacy_bare_commands=False)

    result = await runner._handle_message(_make_group_event('/lute group admin moderation words add', user_id=QQ_ADMIN))

    assert result == _GROUP_ADMIN_MODERATION_USAGE


@pytest.mark.asyncio
async def test_lute_group_admin_join_show_formats_current_group_slice(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_tool_backend(tool_name, args, **kwargs):
        assert tool_name == 'qq_group_admin_get_config'
        assert args == {'group_id': '1004'}
        return LuteResponse(
            text='Loaded qqadmin config for group 1004.',
            payload={
                'success': True,
                'group_id': '1004',
                'config': {
                    'join_switch': True,
                    'join_accept_words': ['答案'],
                    'join_reject_words': ['广告'],
                    'join_no_match_reject': True,
                    'reject_word_block': False,
                    'block_ids': ['1013'],
                    'join_min_level': 2,
                    'join_max_time': 3,
                    'join_ban_time': 600,
                    'join_welcome': '欢迎入群',
                },
            },
        )

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_tool_backend', _fake_call_tool_backend)

    result = await runner._handle_message(_make_group_event('/lute group admin join show', user_id=QQ_ADMIN))

    assert result == (
        'Join config for current group 1004\n'
        'switch: on\n'
        'accept (1): 答案\n'
        'reject (1): 广告\n'
        'no-match-reject: on\n'
        'reject-word-block: off\n'
        'blacklist (1): 1013\n'
        'min-level: 2\n'
        'max-attempts: 3\n'
        'welcome: 欢迎入群\n'
        'join-ban: 600'
    )


@pytest.mark.asyncio
async def test_lute_group_admin_join_switch_on_updates_config(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_tool_backend(tool_name, args, **kwargs):
        assert tool_name == 'qq_group_admin_update_config'
        assert args == {'group_id': '1004', 'updates': {'join_switch': True}}
        return LuteResponse(text='Updated qqadmin config for group 1004.')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_tool_backend', _fake_call_tool_backend)

    result = await runner._handle_message(_make_group_event('/lute group admin join switch on', user_id=QQ_ADMIN))

    assert result == 'Updated qqadmin config for group 1004.'


@pytest.mark.asyncio
async def test_lute_group_admin_join_accept_add_reads_then_updates_config(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)
    calls = []

    async def _fake_call_tool_backend(tool_name, args, **kwargs):
        calls.append((tool_name, args))
        if tool_name == 'qq_group_admin_get_config':
            return LuteResponse(
                text='Loaded qqadmin config for group 1004.',
                payload={
                    'success': True,
                    'group_id': '1004',
                    'config': {'join_accept_words': ['答案']},
                },
            )
        assert tool_name == 'qq_group_admin_update_config'
        assert args == {'group_id': '1004', 'updates': {'join_accept_words': ['答案', '口令', '暗号']}}
        return LuteResponse(text='Updated qqadmin config for group 1004.')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_tool_backend', _fake_call_tool_backend)

    result = await runner._handle_message(_make_group_event('/lute group admin join accept add 答案 口令 暗号', user_id=QQ_ADMIN))

    assert [name for name, _ in calls] == ['qq_group_admin_get_config', 'qq_group_admin_update_config']
    assert result == 'Updated qqadmin config for group 1004.'


@pytest.mark.asyncio
async def test_lute_group_admin_join_reject_remove_reads_then_updates_config(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)
    calls = []

    async def _fake_call_tool_backend(tool_name, args, **kwargs):
        calls.append((tool_name, args))
        if tool_name == 'qq_group_admin_get_config':
            return LuteResponse(
                text='Loaded qqadmin config for group 1004.',
                payload={
                    'success': True,
                    'group_id': '1004',
                    'config': {'join_reject_words': ['广告', '引流']},
                },
            )
        assert tool_name == 'qq_group_admin_update_config'
        assert args == {'group_id': '1004', 'updates': {'join_reject_words': ['引流']}}
        return LuteResponse(text='Updated qqadmin config for group 1004.')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_tool_backend', _fake_call_tool_backend)

    result = await runner._handle_message(_make_group_event('/lute group admin join reject remove 广告 不存在', user_id=QQ_ADMIN))

    assert [name for name, _ in calls] == ['qq_group_admin_get_config', 'qq_group_admin_update_config']
    assert result == 'Updated qqadmin config for group 1004.'


@pytest.mark.asyncio
async def test_lute_group_admin_join_blacklist_list_formats_ids(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_tool_backend(tool_name, args, **kwargs):
        assert tool_name == 'qq_group_admin_blacklist'
        assert args == {'group_id': '1004', 'action': 'list'}
        return LuteResponse(text='Join blacklist for current group 1004 (2): 1013, 1017')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_tool_backend', _fake_call_tool_backend)

    result = await runner._handle_message(_make_group_event('/lute group admin join blacklist list', user_id=QQ_ADMIN))

    assert result == 'Join blacklist for current group 1004 (2): 1013, 1017'


@pytest.mark.asyncio
async def test_lute_group_admin_join_blacklist_add_uses_direct_backend_tool(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)
    calls = []

    async def _fake_call_tool_backend(tool_name, args, **kwargs):
        calls.append((tool_name, args))
        assert tool_name == 'qq_group_admin_blacklist'
        assert args == {'group_id': '1004', 'action': 'add', 'user_ids': ['1013', '1017', '1018']}
        return LuteResponse(text='Updated qqadmin blacklist for group 1004.')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_tool_backend', _fake_call_tool_backend)

    result = await runner._handle_message(_make_group_event('/lute group admin join blacklist add 1013 1017 1018', user_id=QQ_ADMIN))

    assert [name for name, _ in calls] == ['qq_group_admin_blacklist']
    assert result == 'Updated qqadmin blacklist for group 1004.'


@pytest.mark.asyncio
async def test_lute_group_admin_join_welcome_set_updates_config(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_tool_backend(tool_name, args, **kwargs):
        assert tool_name == 'qq_group_admin_update_config'
        assert args == {'group_id': '1004', 'updates': {'join_welcome': '欢迎加入本群'}}
        return LuteResponse(text='Updated qqadmin config for group 1004.')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_tool_backend', _fake_call_tool_backend)

    result = await runner._handle_message(_make_group_event('/lute group admin join welcome set 欢迎加入本群', user_id=QQ_ADMIN))

    assert result == 'Updated qqadmin config for group 1004.'


@pytest.mark.asyncio
async def test_lute_group_admin_join_welcome_clear_updates_empty_string(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_tool_backend(tool_name, args, **kwargs):
        assert tool_name == 'qq_group_admin_update_config'
        assert args == {'group_id': '1004', 'updates': {'join_welcome': ''}}
        return LuteResponse(text='Updated qqadmin config for group 1004.')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_tool_backend', _fake_call_tool_backend)

    result = await runner._handle_message(_make_group_event('/lute group admin join welcome clear', user_id=QQ_ADMIN))

    assert result == 'Updated qqadmin config for group 1004.'


@pytest.mark.asyncio
async def test_lute_group_admin_join_min_level_updates_config(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_tool_backend(tool_name, args, **kwargs):
        assert tool_name == 'qq_group_admin_update_config'
        assert args == {'group_id': '1004', 'updates': {'join_min_level': 3}}
        return LuteResponse(text='Updated qqadmin config for group 1004.')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_tool_backend', _fake_call_tool_backend)

    result = await runner._handle_message(_make_group_event('/lute group admin join min-level 3', user_id=QQ_ADMIN))

    assert result == 'Updated qqadmin config for group 1004.'


@pytest.mark.asyncio
async def test_lute_group_admin_join_max_attempts_updates_join_max_time(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_tool_backend(tool_name, args, **kwargs):
        assert tool_name == 'qq_group_admin_update_config'
        assert args == {'group_id': '1004', 'updates': {'join_max_time': 2}}
        return LuteResponse(text='Updated qqadmin config for group 1004.')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_tool_backend', _fake_call_tool_backend)

    result = await runner._handle_message(_make_group_event('/lute group admin join max-attempts 2', user_id=QQ_ADMIN))

    assert result == 'Updated qqadmin config for group 1004.'


@pytest.mark.asyncio
async def test_lute_group_admin_join_ban_updates_join_ban_time(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_tool_backend(tool_name, args, **kwargs):
        assert tool_name == 'qq_group_admin_update_config'
        assert args == {'group_id': '1004', 'updates': {'join_ban_time': 45}}
        return LuteResponse(text='Updated qqadmin config for group 1004.')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_tool_backend', _fake_call_tool_backend)

    result = await runner._handle_message(_make_group_event('/lute group admin join ban 45', user_id=QQ_ADMIN))

    assert result == 'Updated qqadmin config for group 1004.'


@pytest.mark.asyncio
async def test_lute_group_admin_leave_notify_on_updates_config(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_tool_backend(tool_name, args, **kwargs):
        assert tool_name == 'qq_group_admin_update_config'
        assert args == {'group_id': '1004', 'updates': {'leave_notify': True}}
        return LuteResponse(text='Updated qqadmin config for group 1004.')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_tool_backend', _fake_call_tool_backend)

    result = await runner._handle_message(_make_group_event('/lute group admin leave notify on', user_id=QQ_ADMIN))

    assert result == 'Updated qqadmin config for group 1004.'


@pytest.mark.asyncio
async def test_lute_group_admin_leave_block_off_updates_config(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_tool_backend(tool_name, args, **kwargs):
        assert tool_name == 'qq_group_admin_update_config'
        assert args == {'group_id': '1004', 'updates': {'leave_block': False}}
        return LuteResponse(text='leave block ok')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_tool_backend', _fake_call_tool_backend)

    result = await runner._handle_message(_make_group_event('/lute group admin leave block off', user_id=QQ_ADMIN))

    assert result == 'leave block ok'


@pytest.mark.asyncio
async def test_lute_group_admin_leave_kick_block_updates_config(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_tool_backend(tool_name, args, **kwargs):
        assert tool_name == 'qq_group_admin_update_config'
        assert args == {'group_id': '1004', 'updates': {'kick_block': True}}
        return LuteResponse(text='leave kick-block ok')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_tool_backend', _fake_call_tool_backend)

    result = await runner._handle_message(_make_group_event('/lute group admin leave kick-block on', user_id=QQ_ADMIN))

    assert result == 'leave kick-block ok'


@pytest.mark.asyncio
async def test_lute_group_admin_leave_requires_on_or_off():
    runner = _make_runner(enable_legacy_bare_commands=False)

    result = await runner._handle_message(_make_group_event('/lute group admin leave notify maybe', user_id=QQ_ADMIN))

    assert result == _GROUP_ADMIN_LEAVE_USAGE


@pytest.mark.asyncio
async def test_lute_group_admin_curfew_show_formats_current_group_slice(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_tool_backend(tool_name, args, **kwargs):
        assert tool_name == 'qq_group_admin_get_config'
        assert args == {'group_id': '1004'}
        return LuteResponse(
            text='Loaded qqadmin config for group 1004.',
            payload={
                'success': True,
                'group_id': '1004',
                'config': {
                    'curfew_enabled': True,
                    'curfew_start': '23:00',
                    'curfew_end': '06:00',
                },
            },
        )

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_tool_backend', _fake_call_tool_backend)

    result = await runner._handle_message(_make_group_event('/lute group admin curfew show', user_id=QQ_ADMIN))

    assert result == 'Curfew config for current group 1004\nenabled: on\nstart: 23:00\nend: 06:00'


@pytest.mark.asyncio
async def test_lute_group_admin_curfew_set_updates_config(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_tool_backend(tool_name, args, **kwargs):
        assert tool_name == 'qq_group_admin_update_config'
        assert args == {
            'group_id': '1004',
            'updates': {'curfew_enabled': True, 'curfew_start': '23:00', 'curfew_end': '06:00'},
        }
        return LuteResponse(text='Updated qqadmin config for group 1004.')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_tool_backend', _fake_call_tool_backend)

    result = await runner._handle_message(_make_group_event('/lute group admin curfew set 23:00 06:00', user_id=QQ_ADMIN))

    assert result == 'Updated qqadmin config for group 1004.'


@pytest.mark.asyncio
async def test_lute_group_admin_curfew_clear_updates_config(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_tool_backend(tool_name, args, **kwargs):
        assert tool_name == 'qq_group_admin_update_config'
        assert args == {
            'group_id': '1004',
            'updates': {'curfew_enabled': False, 'curfew_start': '', 'curfew_end': ''},
        }
        return LuteResponse(text='Updated qqadmin config for group 1004.')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_tool_backend', _fake_call_tool_backend)

    result = await runner._handle_message(_make_group_event('/lute group admin curfew clear', user_id=QQ_ADMIN))

    assert result == 'Updated qqadmin config for group 1004.'


@pytest.mark.asyncio
async def test_lute_group_admin_curfew_set_requires_valid_hhmm():
    runner = _make_runner(enable_legacy_bare_commands=False)

    result = await runner._handle_message(_make_group_event('/lute group admin curfew set 23 06:00', user_id=QQ_ADMIN))

    assert result == _GROUP_ADMIN_CURFEW_USAGE


@pytest.mark.asyncio
async def test_lute_group_admin_cleanup_preview_uses_tool_backend(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_tool_backend(tool_name, args, **kwargs):
        assert tool_name == 'qq_group_member_cleanup_preview'
        assert args == {'group_id': '1004', 'inactive_days': 45, 'max_level': 1}
        return LuteResponse(text='preview ok')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_tool_backend', _fake_call_tool_backend)

    result = await runner._handle_message(_make_group_event('/lute group admin cleanup preview --inactive-days 45 --max-level 1', user_id=QQ_ADMIN))

    assert result == 'preview ok'


@pytest.mark.asyncio
async def test_lute_group_admin_cleanup_apply_uses_tool_backend(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_tool_backend(tool_name, args, **kwargs):
        assert tool_name == 'qq_group_member_cleanup_apply'
        assert args == {
            'group_id': '1004',
            'user_ids': ['1006', '1019'],
            'inactive_days': 45,
            'max_level': 1,
            'reject_add_request': True,
        }
        return LuteResponse(text='apply ok')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_tool_backend', _fake_call_tool_backend)

    result = await runner._handle_message(_make_group_event('/lute group admin cleanup apply 1006 1019 --inactive-days 45 --max-level 1 --reject-add-request', user_id=QQ_ADMIN))

    assert result == 'apply ok'


@pytest.mark.asyncio
async def test_lute_group_admin_cleanup_apply_without_user_ids_defaults_to_all_candidates(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_tool_backend(tool_name, args, **kwargs):
        assert tool_name == 'qq_group_member_cleanup_apply'
        assert args == {
            'group_id': '1004',
            'user_ids': [],
            'inactive_days': 45,
            'max_level': 1,
            'reject_add_request': False,
        }
        return LuteResponse(text='apply all ok')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_tool_backend', _fake_call_tool_backend)

    result = await runner._handle_message(_make_group_event('/lute group admin cleanup apply --inactive-days 45 --max-level 1', user_id=QQ_ADMIN))

    assert result == 'apply all ok'


@pytest.mark.asyncio
async def test_lute_group_admin_cleanup_preview_requires_integer_options():
    runner = _make_runner(enable_legacy_bare_commands=False)

    result = await runner._handle_message(_make_group_event('/lute group admin cleanup preview --inactive-days abc', user_id=QQ_ADMIN))

    assert result == _GROUP_ADMIN_CLEANUP_USAGE




@pytest.mark.asyncio
async def test_lute_group_admin_ai_card_preview_uses_tool_backend(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_tool_backend(tool_name, args, **kwargs):
        assert tool_name == 'qq_group_ai_set_card'
        assert args == {'group_id': '1004', 'user_id': '1010', 'history_count': 120, 'apply': False}
        return LuteResponse(text='card preview ok')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_tool_backend', _fake_call_tool_backend)

    result = await runner._handle_message(_make_group_event('/lute group admin ai gname 1010 --history-count 120', user_id=QQ_ADMIN))

    assert result == 'card preview ok'


@pytest.mark.asyncio
async def test_lute_group_admin_ai_card_apply_uses_tool_backend(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_tool_backend(tool_name, args, **kwargs):
        assert tool_name == 'qq_group_ai_set_card'
        assert args == {'group_id': '1004', 'user_id': '1010', 'history_count': 120, 'apply': True}
        return LuteResponse(text='card apply ok')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_tool_backend', _fake_call_tool_backend)

    result = await runner._handle_message(_make_group_event('/lute group admin ai gname apply 1010 --history-count 120', user_id=QQ_ADMIN))

    assert result == 'card apply ok'


@pytest.mark.asyncio
async def test_lute_group_admin_manual_gname_uses_tool_backend(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_tool_backend(tool_name, args, **kwargs):
        assert tool_name == 'qq_group_admin_set_gname'
        assert args == {'group_id': '1004', 'user_id': '1010', 'gname': '新昵称'}
        return LuteResponse(text='manual gname ok')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_tool_backend', _fake_call_tool_backend)

    result = await runner._handle_message(_make_group_event('/lute group admin gname 1010 新昵称', user_id=QQ_ADMIN))

    assert result == 'manual gname ok'


@pytest.mark.asyncio
async def test_lute_group_admin_manual_title_uses_tool_backend(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_tool_backend(tool_name, args, **kwargs):
        assert tool_name == 'qq_group_admin_set_title'
        assert args == {'group_id': '1004', 'user_id': '1010', 'title': '新头衔'}
        return LuteResponse(text='manual title ok')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_tool_backend', _fake_call_tool_backend)

    result = await runner._handle_message(_make_group_event('/lute group admin title 1010 新头衔', user_id=QQ_ADMIN))

    assert result == 'manual title ok'


@pytest.mark.asyncio
async def test_lute_group_admin_ai_title_preview_uses_tool_backend(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_tool_backend(tool_name, args, **kwargs):
        assert tool_name == 'qq_group_ai_set_title'
        assert args == {'group_id': '1004', 'user_id': '1010', 'history_count': 80, 'apply': False}
        return LuteResponse(text='title preview ok')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_tool_backend', _fake_call_tool_backend)

    result = await runner._handle_message(_make_group_event('/lute group admin ai title 1010 --history-count 80', user_id=QQ_ADMIN))

    assert result == 'title preview ok'


@pytest.mark.asyncio
async def test_lute_group_admin_ai_title_apply_uses_tool_backend(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_tool_backend(tool_name, args, **kwargs):
        assert tool_name == 'qq_group_ai_set_title'
        assert args == {'group_id': '1004', 'user_id': '1010', 'history_count': 80, 'apply': True}
        return LuteResponse(text='title apply ok')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_tool_backend', _fake_call_tool_backend)

    result = await runner._handle_message(_make_group_event('/lute group admin ai title apply 1010 --history-count 80', user_id=QQ_ADMIN))

    assert result == 'title apply ok'


@pytest.mark.asyncio
async def test_lute_group_admin_ai_requires_integer_history_count():
    runner = _make_runner(enable_legacy_bare_commands=False)

    result = await runner._handle_message(_make_group_event('/lute group admin ai gname 1010 --history-count abc', user_id=QQ_ADMIN))

    assert result == _GROUP_ADMIN_AI_USAGE


@pytest.mark.asyncio
async def test_lute_group_admin_remains_admin_only():
    runner = _make_runner(enable_legacy_bare_commands=False)
    runner.config.qqbot.access.allow_groups = ['1004']
    runner.config.qqbot.access.group_user_allowlist = {'1004': [QQ_ALLOWED_USER]}

    result = await runner._handle_message(_make_group_event('/lute group admin show', user_id=QQ_ALLOWED_USER))

    assert result == ''


@pytest.mark.asyncio
async def test_lute_group_member_admin_uses_tool_backend_for_admin(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_tool_backend(tool_name, args, **kwargs):
        assert tool_name == 'qq_set_group_admin'
        assert args == {'group_id': '1004', 'user_id': '1010', 'enable': True}
        return LuteResponse(text='Group member admin result')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_tool_backend', _fake_call_tool_backend)

    result = await runner._handle_message(_make_group_event('/lute group member admin 1010 on', user_id=QQ_ADMIN))

    assert result == 'Group member admin result'


@pytest.mark.asyncio
async def test_lute_group_member_card_uses_tool_backend_for_admin(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_tool_backend(tool_name, args, **kwargs):
        assert tool_name == 'qq_set_group_card'
        assert args == {'group_id': '1004', 'user_id': '1010', 'card': '新名片'}
        return LuteResponse(text='Group member card result')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_tool_backend', _fake_call_tool_backend)

    result = await runner._handle_message(_make_group_event('/lute group member card 1010 新名片', user_id=QQ_ADMIN))

    assert result == 'Group member card result'


@pytest.mark.asyncio
async def test_lute_group_member_title_uses_tool_backend_for_admin(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_tool_backend(tool_name, args, **kwargs):
        assert tool_name == 'qq_set_group_special_title'
        assert args == {'group_id': '1004', 'user_id': '1010', 'special_title': '头衔'}
        return LuteResponse(text='Group member title result')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_tool_backend', _fake_call_tool_backend)

    result = await runner._handle_message(_make_group_event('/lute group member title 1010 头衔', user_id=QQ_ADMIN))

    assert result == 'Group member title result'


@pytest.mark.asyncio
async def test_lute_group_member_admin_requires_on_or_off():
    runner = _make_runner(enable_legacy_bare_commands=False)

    result = await runner._handle_message(_make_group_event('/lute group member admin 1010 maybe', user_id=QQ_ADMIN))

    assert result == _GROUP_MEMBER_USAGE


@pytest.mark.asyncio
async def test_lute_group_name_uses_tool_backend_for_admin(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_tool_backend(tool_name, args, **kwargs):
        assert tool_name == 'qq_set_group_name'
        assert args == {'group_id': '1004', 'group_name': '新群名'}
        return LuteResponse(text='Group name result')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_tool_backend', _fake_call_tool_backend)

    result = await runner._handle_message(_make_group_event('/lute group name 新群名', user_id=QQ_ADMIN))

    assert result == 'Group name result'


@pytest.mark.asyncio
async def test_lute_group_portrait_is_not_exposed_anymore(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_tool_backend(*args, **kwargs):
        raise AssertionError('group portrait should not call tool backend')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_tool_backend', _fake_call_tool_backend)

    result = await runner._handle_message(_make_group_event('/lute group portrait /tmp/group.png', user_id=QQ_ADMIN))

    assert result == 'Unknown /lute verb: portrait\nTry: /lute help group'


@pytest.mark.asyncio
async def test_lute_group_whole_ban_uses_tool_backend_for_admin(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_tool_backend(tool_name, args, **kwargs):
        assert tool_name == 'qq_set_group_whole_ban'
        assert args == {'group_id': '1004', 'enable': True}
        return LuteResponse(text='Group whole-ban result')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_tool_backend', _fake_call_tool_backend)

    result = await runner._handle_message(_make_group_event('/lute group whole-ban on', user_id=QQ_ADMIN))

    assert result == 'Group whole-ban result'


@pytest.mark.asyncio
async def test_lute_group_sign_uses_tool_backend_for_admin(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_tool_backend(tool_name, args, **kwargs):
        assert tool_name == 'qq_set_group_sign'
        assert args == {'group_id': '1004'}
        return LuteResponse(text='Group sign result')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_tool_backend', _fake_call_tool_backend)

    result = await runner._handle_message(_make_group_event('/lute group sign', user_id=QQ_ADMIN))

    assert result == 'Group sign result'


@pytest.mark.asyncio
async def test_lute_group_at_all_uses_tool_backend_for_admin(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_tool_backend(tool_name, args, **kwargs):
        assert tool_name == 'qq_get_group_at_all_remain'
        assert args == {'group_id': '1004'}
        return LuteResponse(text='Bot account remaining @all count in group 1004 — group: 20, bot-self: None, can_at_all: True')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_tool_backend', _fake_call_tool_backend)

    result = await runner._handle_message(_make_group_event('/lute group at-all', user_id=QQ_ADMIN))

    assert 'Bot account remaining @all count' in result


@pytest.mark.asyncio
async def test_lute_group_whole_ban_requires_on_or_off():
    runner = _make_runner(enable_legacy_bare_commands=False)

    result = await runner._handle_message(_make_group_event('/lute group whole-ban maybe', user_id=QQ_ADMIN))

    assert result == 'Usage: /lute group whole-ban on|off'


@pytest.mark.asyncio
async def test_lute_group_essence_list_uses_tool_backend_for_admin(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_tool_backend(tool_name, args, **kwargs):
        assert tool_name == 'qq_get_essence_msg_list'
        assert args == {'group_id': '1004'}
        return LuteResponse(text='Group essence list result')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_tool_backend', _fake_call_tool_backend)

    result = await runner._handle_message(_make_group_event('/lute group essence list', user_id=QQ_ADMIN))

    assert result == 'Group essence list result'


@pytest.mark.asyncio
async def test_lute_group_essence_add_uses_tool_backend_for_admin(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_tool_backend(tool_name, args, **kwargs):
        assert tool_name == 'qq_set_essence_msg'
        assert args == {'message_id': '777'}
        return LuteResponse(text='Group essence add result')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_tool_backend', _fake_call_tool_backend)

    result = await runner._handle_message(_make_group_event('/lute group essence add 777', user_id=QQ_ADMIN))

    assert result == 'Group essence add result'


@pytest.mark.asyncio
async def test_lute_group_essence_add_defaults_to_replied_message_id(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_tool_backend(tool_name, args, **kwargs):
        assert tool_name == 'qq_set_essence_msg'
        assert args == {'message_id': '888'}
        return LuteResponse(text='Group essence add result')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_tool_backend', _fake_call_tool_backend)

    result = await runner._handle_message(
        _make_group_event('/lute group essence add', user_id=QQ_ADMIN, reply_to_message_id='888', reply_to_text='quoted')
    )

    assert result == 'Group essence add result'


@pytest.mark.asyncio
async def test_lute_group_essence_remove_uses_tool_backend_for_admin(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_tool_backend(tool_name, args, **kwargs):
        assert tool_name == 'qq_delete_essence_msg'
        assert args == {'message_id': '777'}
        return LuteResponse(text='Group essence remove result')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_tool_backend', _fake_call_tool_backend)

    result = await runner._handle_message(_make_group_event('/lute group essence remove 777', user_id=QQ_ADMIN))

    assert result == 'Group essence remove result'


@pytest.mark.asyncio
async def test_lute_group_essence_returns_usage_for_invalid_action():
    runner = _make_runner(enable_legacy_bare_commands=False)

    result = await runner._handle_message(_make_group_event('/lute group essence maybe', user_id=QQ_ADMIN))

    assert result == _GROUP_ESSENCE_USAGE


@pytest.mark.asyncio
async def test_lute_group_react_add_uses_tool_backend_for_admin(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_tool_backend(tool_name, args, **kwargs):
        assert tool_name == 'qq_set_msg_emoji_like'
        assert args == {'message_id': '777', 'emoji_id': '66', 'set': True}
        return LuteResponse(text='Group react add result')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_tool_backend', _fake_call_tool_backend)

    result = await runner._handle_message(_make_group_event('/lute group react add 66 777', user_id=QQ_ADMIN))

    assert result == 'Group react add result'


@pytest.mark.asyncio
async def test_lute_group_react_remove_uses_tool_backend_for_admin(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_tool_backend(tool_name, args, **kwargs):
        assert tool_name == 'qq_set_msg_emoji_like'
        assert args == {'message_id': '777', 'emoji_id': '66', 'set': False}
        return LuteResponse(text='Group react remove result')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_tool_backend', _fake_call_tool_backend)

    result = await runner._handle_message(_make_group_event('/lute group react remove 66 777', user_id=QQ_ADMIN))

    assert result == 'Group react remove result'


@pytest.mark.asyncio
async def test_lute_group_react_add_defaults_to_replied_message_id(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_tool_backend(tool_name, args, **kwargs):
        assert tool_name == 'qq_set_msg_emoji_like'
        assert args == {'message_id': '888', 'emoji_id': '66', 'set': True}
        return LuteResponse(text='Group react add result')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_tool_backend', _fake_call_tool_backend)

    result = await runner._handle_message(
        _make_group_event('/lute group react add 66', user_id=QQ_ADMIN, reply_to_message_id='888', reply_to_text='quoted')
    )

    assert result == 'Group react add result'


@pytest.mark.asyncio
async def test_lute_group_react_returns_usage_when_message_id_missing_without_reply():
    runner = _make_runner(enable_legacy_bare_commands=False)

    result = await runner._handle_message(_make_group_event('/lute group react add 66', user_id=QQ_ADMIN))

    assert result == _GROUP_REACT_USAGE


@pytest.mark.asyncio
async def test_lute_group_react_requires_current_group_context():
    runner = _make_runner(enable_legacy_bare_commands=False)

    result = await runner._handle_message(_make_event('/lute group react add 66 777', user_id=QQ_ADMIN))

    assert result == _GROUP_REACT_USAGE


@pytest.mark.asyncio
async def test_lute_group_read_uses_tool_backend_for_admin(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_tool_backend(tool_name, args, **kwargs):
        assert tool_name == 'qq_mark_msg_as_read'
        assert args == {'chat_type': 'group', 'target_id': '1004'}
        return LuteResponse(text='Group read result')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_tool_backend', _fake_call_tool_backend)

    result = await runner._handle_message(_make_group_event('/lute group read', user_id=QQ_ADMIN))

    assert result == 'Group read result'


@pytest.mark.asyncio
async def test_lute_group_read_requires_current_group_context():
    runner = _make_runner(enable_legacy_bare_commands=False)

    result = await runner._handle_message(_make_event('/lute group read', user_id=QQ_ADMIN))

    assert result == _GROUP_READ_USAGE


@pytest.mark.asyncio
async def test_lute_qq_message_send_private_uses_tool_backend_for_admin(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_tool_backend(tool_name, args, **kwargs):
        assert tool_name == 'qq_send_message'
        assert args == {'chat_type': 'private', 'target_id': '1003', 'message': '你好'}
        return LuteResponse(text='QQ message send private result')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_tool_backend', _fake_call_tool_backend)

    result = await runner._handle_message(_make_event('/lute qq message send private 1003 你好', user_id=QQ_ADMIN))

    assert result == 'QQ message send private result'


@pytest.mark.asyncio
async def test_lute_qq_message_send_group_uses_tool_backend_for_admin(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_tool_backend(tool_name, args, **kwargs):
        assert tool_name == 'qq_send_message'
        assert args == {'chat_type': 'group', 'target_id': '1004', 'message': '公告'}
        return LuteResponse(text='QQ message send group result')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_tool_backend', _fake_call_tool_backend)

    result = await runner._handle_message(_make_event('/lute qq message send group 1004 公告', user_id=QQ_ADMIN))

    assert result == 'QQ message send group result'


@pytest.mark.asyncio
async def test_lute_qq_message_forward_to_user_uses_tool_backend_for_admin(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_tool_backend(tool_name, args, **kwargs):
        assert tool_name == 'qq_forward_message'
        assert args == {'chat_type': 'private', 'target_id': '1003', 'message_id': '777'}
        return LuteResponse(text='QQ message forward user result')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_tool_backend', _fake_call_tool_backend)

    result = await runner._handle_message(_make_event('/lute qq message forward 777 --to-user 1003', user_id=QQ_ADMIN))

    assert result == 'QQ message forward user result'


@pytest.mark.asyncio
async def test_lute_qq_message_forward_to_group_uses_tool_backend_for_admin(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_tool_backend(tool_name, args, **kwargs):
        assert tool_name == 'qq_forward_message'
        assert args == {'chat_type': 'group', 'target_id': '1004', 'message_id': '777'}
        return LuteResponse(text='QQ message forward group result')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_tool_backend', _fake_call_tool_backend)

    result = await runner._handle_message(_make_event('/lute qq message forward 777 --to-group 1004', user_id=QQ_ADMIN))

    assert result == 'QQ message forward group result'


@pytest.mark.asyncio
async def test_lute_qq_message_merge_to_user_uses_tool_backend_for_admin(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_tool_backend(tool_name, args, **kwargs):
        assert tool_name == 'qq_forward_messages'
        assert args == {'chat_type': 'private', 'target_id': '1003', 'messages': ['777', '888']}
        return LuteResponse(text='QQ message merge user result')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_tool_backend', _fake_call_tool_backend)

    result = await runner._handle_message(_make_event('/lute qq message merge 777 888 --to-user 1003', user_id=QQ_ADMIN))

    assert result == 'QQ message merge user result'


@pytest.mark.asyncio
async def test_lute_qq_message_merge_to_group_uses_tool_backend_for_admin(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_tool_backend(tool_name, args, **kwargs):
        assert tool_name == 'qq_forward_messages'
        assert args == {'chat_type': 'group', 'target_id': '1004', 'messages': ['777', '888']}
        return LuteResponse(text='QQ message merge group result')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_tool_backend', _fake_call_tool_backend)

    result = await runner._handle_message(_make_event('/lute qq message merge 777 888 --to-group 1004', user_id=QQ_ADMIN))

    assert result == 'QQ message merge group result'


@pytest.mark.asyncio
async def test_lute_qq_message_forward_requires_explicit_single_target_selector():
    runner = _make_runner(enable_legacy_bare_commands=False)

    result = await runner._handle_message(_make_event('/lute qq message forward 777', user_id=QQ_ADMIN))

    assert result == _QQ_MESSAGE_FORWARD_USAGE


@pytest.mark.asyncio
async def test_lute_qq_message_merge_requires_explicit_single_target_selector():
    runner = _make_runner(enable_legacy_bare_commands=False)

    result = await runner._handle_message(_make_event('/lute qq message merge 777 888', user_id=QQ_ADMIN))

    assert result == _QQ_MESSAGE_MERGE_USAGE


@pytest.mark.asyncio
async def test_lute_qq_message_send_returns_usage_for_invalid_target_kind():
    runner = _make_runner(enable_legacy_bare_commands=False)

    result = await runner._handle_message(_make_event('/lute qq message send room 1 hi', user_id=QQ_ADMIN))

    assert result == _QQ_MESSAGE_SEND_USAGE


@pytest.mark.asyncio
async def test_lute_qq_file_list_uses_tool_backend_for_admin(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_tool_backend(tool_name, args, **kwargs):
        assert tool_name == 'qq_get_group_root_files'
        assert args == {'group_id': '1004'}
        return LuteResponse(text='QQ file list result')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_tool_backend', _fake_call_tool_backend)

    result = await runner._handle_message(_make_event('/lute qq file list 1004', user_id=QQ_ADMIN))

    assert result == 'QQ file list result'


@pytest.mark.asyncio
async def test_lute_qq_file_all_uses_tool_backend_for_admin(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_tool_backend(tool_name, args, **kwargs):
        assert tool_name == 'qq_get_all_group_files'
        assert args == {'group_id': '1004'}
        return LuteResponse(text='QQ file all result')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_tool_backend', _fake_call_tool_backend)

    result = await runner._handle_message(_make_event('/lute qq file all 1004', user_id=QQ_ADMIN))

    assert result == 'QQ file all result'


@pytest.mark.asyncio
async def test_lute_qq_file_detail_uses_tool_backend_for_admin(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_tool_backend(tool_name, args, **kwargs):
        assert tool_name == 'qq_get_group_file_detail'
        assert args == {'group_id': '1004', 'file_id': 'file123'}
        return LuteResponse(text='QQ file detail result')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_tool_backend', _fake_call_tool_backend)

    result = await runner._handle_message(_make_event('/lute qq file detail 1004 file123', user_id=QQ_ADMIN))

    assert result == 'QQ file detail result'


@pytest.mark.asyncio
async def test_lute_qq_file_url_uses_tool_backend_for_admin(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_tool_backend(tool_name, args, **kwargs):
        assert tool_name == 'qq_get_group_file_url'
        assert args == {'group_id': '1004', 'file_id': 'file123'}
        return LuteResponse(text='QQ file url result')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_tool_backend', _fake_call_tool_backend)

    result = await runner._handle_message(_make_event('/lute qq file url 1004 file123', user_id=QQ_ADMIN))

    assert result == 'QQ file url result'


@pytest.mark.asyncio
async def test_lute_qq_file_list_returns_usage_when_group_id_missing():
    runner = _make_runner(enable_legacy_bare_commands=False)

    result = await runner._handle_message(_make_event('/lute qq file list', user_id=QQ_ADMIN))

    assert result == _QQ_FILE_USAGE


@pytest.mark.asyncio
async def test_lute_qq_file_mkdir_uses_tool_backend_for_admin(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_tool_backend(tool_name, args, **kwargs):
        assert tool_name == 'qq_create_group_file_folder'
        assert args == {'group_id': '1004', 'folder_name': '新目录'}
        return LuteResponse(text='QQ file mkdir result')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_tool_backend', _fake_call_tool_backend)

    result = await runner._handle_message(_make_event('/lute qq file mkdir 1004 新目录', user_id=QQ_ADMIN))

    assert result == 'QQ file mkdir result'


@pytest.mark.asyncio
async def test_lute_qq_file_delete_uses_tool_backend_for_admin(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_tool_backend(tool_name, args, **kwargs):
        assert tool_name == 'qq_delete_group_file'
        assert args == {'group_id': '1004', 'file_id': 'file123'}
        return LuteResponse(text='QQ file delete result')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_tool_backend', _fake_call_tool_backend)

    result = await runner._handle_message(_make_event('/lute qq file delete 1004 file123', user_id=QQ_ADMIN))

    assert result == 'QQ file delete result'


@pytest.mark.asyncio
async def test_lute_qq_file_upload_group_is_not_exposed_anymore(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_tool_backend(*args, **kwargs):
        raise AssertionError('qq file upload should not call tool backend')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_tool_backend', _fake_call_tool_backend)

    result = await runner._handle_message(_make_event('/lute qq file upload group 1004 /tmp/report.txt --name report.txt', user_id=QQ_ADMIN))

    assert result == _QQ_FILE_USAGE


@pytest.mark.asyncio
async def test_lute_qq_file_download_uses_tool_backend_for_admin(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_tool_backend(tool_name, args, **kwargs):
        assert tool_name == 'qq_download_file'
        assert args == {'url': 'https://example.com/a.zip'}
        return LuteResponse(text='QQ file download result')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_tool_backend', _fake_call_tool_backend)

    result = await runner._handle_message(_make_event('/lute qq file download https://example.com/a.zip', user_id=QQ_ADMIN))

    assert result == 'QQ file download result'


@pytest.mark.asyncio
async def test_lute_qq_file_upload_group_requires_name_option():
    runner = _make_runner(enable_legacy_bare_commands=False)

    result = await runner._handle_message(_make_event('/lute qq file upload group 1004 /tmp/report.txt', user_id=QQ_ADMIN))

    assert result == _QQ_FILE_USAGE


@pytest.mark.asyncio
async def test_lute_qq_request_is_not_exposed_anymore(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_tool_backend(*args, **kwargs):
        raise AssertionError('qq request should not call tool backend')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_tool_backend', _fake_call_tool_backend)

    result = await runner._handle_message(_make_event('/lute qq request friend reject flag_123', user_id=QQ_ADMIN))

    assert result == 'Unknown /lute verb: request\nTry: /lute help qq'


@pytest.mark.asyncio
async def test_lute_qq_contact_is_not_exposed_anymore(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_tool_backend(*args, **kwargs):
        raise AssertionError('qq contact should not call tool backend')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_tool_backend', _fake_call_tool_backend)

    result = await runner._handle_message(_make_event('/lute qq contact friend list', user_id=QQ_ADMIN))

    assert result == 'Unknown /lute verb: contact\nTry: /lute help qq'


@pytest.mark.asyncio
async def test_lute_group_send_private_uses_tool_backend_for_admin(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_tool_backend(tool_name, args, **kwargs):
        assert tool_name == 'qq_send_message'
        assert args == {'chat_type': 'private', 'target_id': '1003', 'content': '你好'}
        return LuteResponse(text='Group send result')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_tool_backend', _fake_call_tool_backend)

    result = await runner._handle_message(_make_event('/lute group send private 1003 你好', user_id=QQ_ADMIN))

    assert result == 'Unknown /lute verb: send\nTry: /lute help group'


@pytest.mark.asyncio
async def test_lute_group_file_upload_uses_tool_backend_for_admin(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_tool_backend(tool_name, args, **kwargs):
        assert tool_name == 'qq_upload_file'
        assert args == {'chat_type': 'group', 'target_id': '1004', 'file': '/tmp/report.txt'}
        return LuteResponse(text='Group file upload result')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_tool_backend', _fake_call_tool_backend)

    result = await runner._handle_message(_make_event('/lute group file upload 1004 /tmp/report.txt', user_id=QQ_ADMIN))

    assert result == 'Unknown /lute verb: file\nTry: /lute help group'


@pytest.mark.asyncio
async def test_lute_group_mute_uses_tool_backend_for_admin(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_tool_backend(tool_name, args, **kwargs):
        assert tool_name == 'qq_mute_group_member'
        assert args == {'group_id': '1004', 'user_id': '1010', 'duration_seconds': 37}
        return LuteResponse(text='Group mute result')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_tool_backend', _fake_call_tool_backend)

    result = await runner._handle_message(_make_group_event('/lute group mute 1010 37', user_id=QQ_ADMIN))

    assert result == 'Group mute result'


@pytest.mark.asyncio
async def test_lute_group_mute_requires_numeric_seconds():
    runner = _make_runner(enable_legacy_bare_commands=False)

    result = await runner._handle_message(_make_group_event('/lute group mute 1010 nope', user_id=QQ_ADMIN))

    assert result == _GROUP_MUTE_USAGE


@pytest.mark.asyncio
async def test_lute_group_kick_requires_target_user_id():
    runner = _make_runner(enable_legacy_bare_commands=False)

    result = await runner._handle_message(_make_group_event('/lute group kick', user_id=QQ_ADMIN))

    assert result == _GROUP_KICK_USAGE


@pytest.mark.asyncio
async def test_lute_group_history_group_uses_tool_backend_for_admin(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_tool_backend(tool_name, args, **kwargs):
        assert tool_name == 'qq_get_group_msg_history'
        assert args == {'group_id': '1004', 'count': 20}
        return LuteResponse(text='Group history result')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_tool_backend', _fake_call_tool_backend)

    result = await runner._handle_message(_make_event('/lute group history group 1004', user_id=QQ_ADMIN))

    assert result == 'Usage: /lute group history [--count 20]'


@pytest.mark.asyncio
async def test_lute_group_history_friend_uses_tool_backend_for_admin(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_tool_backend(tool_name, args, **kwargs):
        assert tool_name == 'qq_get_friend_msg_history'
        assert args == {'user_id': '1003', 'count': 5}
        return LuteResponse(text='Friend history result')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_tool_backend', _fake_call_tool_backend)

    result = await runner._handle_message(_make_event('/lute group history friend 1003 --count 5', user_id=QQ_ADMIN))

    assert result == 'Usage: /lute group history [--count 20]'


@pytest.mark.asyncio
async def test_lute_group_honor_uses_tool_backend_for_admin(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_tool_backend(tool_name, args, **kwargs):
        assert tool_name == 'qq_get_group_honor_info'
        assert args == {'group_id': '1004', 'type': 'all'}
        return LuteResponse(text='Group honor result')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_tool_backend', _fake_call_tool_backend)

    result = await runner._handle_message(_make_event('/lute group honor 1004', user_id=QQ_ADMIN))

    assert result == 'Usage: /lute group honor [--type all]'


@pytest.mark.asyncio
async def test_lute_group_poke_private_uses_tool_backend_for_admin(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_tool_backend(tool_name, args, **kwargs):
        assert tool_name == 'qq_poke'
        assert args == {'user_id': '1003'}
        return LuteResponse(text='Group poke result')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_tool_backend', _fake_call_tool_backend)

    result = await runner._handle_message(_make_event('/lute group poke private 1003', user_id=QQ_ADMIN))

    assert result == 'Usage: /lute group poke <qq_id>'


@pytest.mark.asyncio
async def test_lute_group_recall_uses_tool_backend_for_admin(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_tool_backend(tool_name, args, **kwargs):
        assert tool_name == 'qq_recall_message'
        assert args == {'message_id': '777'}
        return LuteResponse(text='Group recall result')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_tool_backend', _fake_call_tool_backend)

    result = await runner._handle_message(_make_event('/lute group recall 777', user_id=QQ_ADMIN))

    assert result == 'Group recall result'


@pytest.mark.asyncio
async def test_lute_group_recall_defaults_to_replied_message_id_in_group(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_tool_backend(tool_name, args, **kwargs):
        assert tool_name == 'qq_recall_message'
        assert args == {'message_id': '888'}
        return LuteResponse(text='Group recall result')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_tool_backend', _fake_call_tool_backend)

    result = await runner._handle_message(
        _make_group_event('/lute group recall', user_id=QQ_ADMIN, reply_to_message_id='888', reply_to_text='quoted')
    )

    assert result == 'Group recall result'


@pytest.mark.asyncio
async def test_lute_group_recall_requires_message_id_without_reply():
    runner = _make_runner(enable_legacy_bare_commands=False)

    result = await runner._handle_message(_make_group_event('/lute group recall', user_id=QQ_ADMIN))

    assert result == _GROUP_RECALL_USAGE


@pytest.mark.asyncio
async def test_lute_group_file_list_defaults_to_current_group_id(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_tool_backend(tool_name, args, **kwargs):
        assert tool_name == 'qq_get_group_root_files'
        assert args == {'group_id': '1004'}
        return LuteResponse(text='Group file list result')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_tool_backend', _fake_call_tool_backend)

    result = await runner._handle_message(_make_group_event('/lute group file list', user_id=QQ_ADMIN))

    assert result == 'Unknown /lute verb: file\nTry: /lute help group'


@pytest.mark.asyncio
async def test_lute_group_file_url_rejects_cross_group_target_in_group_context():
    runner = _make_runner(enable_legacy_bare_commands=False)

    result = await runner._handle_message(_make_group_event('/lute group file url 1016 file123', user_id=QQ_ADMIN))

    assert result == 'Unknown /lute verb: file\nTry: /lute help group'


@pytest.mark.asyncio
async def test_lute_group_notice_list_defaults_to_current_group_id(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_tool_backend(tool_name, args, **kwargs):
        assert tool_name == 'qq_get_group_notice'
        assert args == {'group_id': '1004'}
        return LuteResponse(text='Group notice list result')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_tool_backend', _fake_call_tool_backend)

    result = await runner._handle_message(_make_group_event('/lute group notice list', user_id=QQ_ADMIN))

    assert result == 'Group notice list result'


@pytest.mark.asyncio
async def test_lute_group_notice_send_supports_image_option(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_tool_backend(tool_name, args, **kwargs):
        assert tool_name == 'qq_send_group_notice'
        assert args == {'group_id': '1004', 'content': 'hello notice', 'image_url': 'https://example.com/a.png'}
        return LuteResponse(text='Group notice with image result')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_tool_backend', _fake_call_tool_backend)

    result = await runner._handle_message(_make_group_event('/lute group notice send hello notice --image https://example.com/a.png', user_id=QQ_ADMIN))

    assert result == 'Group notice with image result'


@pytest.mark.asyncio
async def test_lute_group_notice_returns_structured_usage_for_invalid_action():
    runner = _make_runner(enable_legacy_bare_commands=False)

    result = await runner._handle_message(_make_group_event('/lute group notice maybe', user_id=QQ_ADMIN))

    assert result == _GROUP_NOTICE_USAGE


@pytest.mark.asyncio
async def test_lute_group_notice_clone_text_only_to_current_group(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)
    calls = []

    async def _fake_call_tool_backend(tool_name, args, **kwargs):
        calls.append((tool_name, args))
        if tool_name == 'qq_get_group_notice_detail':
            assert args == {'group_id': '1020', 'notice_id': 'notice123'}
            return LuteResponse(
                text='detail',
                payload={
                    'success': True,
                    'content': '源公告文本',
                    'image_count': 0,
                    'images': [],
                },
            )
        if tool_name == 'qq_send_group_notice':
            assert args == {'group_id': '1004', 'content': '源公告文本'}
            return LuteResponse(text='Notice cloned')
        raise AssertionError(tool_name)

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_tool_backend', _fake_call_tool_backend)

    result = await runner._handle_message(_make_group_event('/lute group notice clone 1020 notice123', user_id=QQ_ADMIN))

    assert result == 'Notice cloned'
    assert [name for name, _ in calls] == ['qq_get_group_notice_detail', 'qq_send_group_notice']


@pytest.mark.asyncio
async def test_lute_group_notice_clone_requires_reply_image_or_manual_image_for_image_notice(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_tool_backend(tool_name, args, **kwargs):
        assert tool_name == 'qq_get_group_notice_detail'
        return LuteResponse(
            text='detail',
            payload={
                'success': True,
                'content': '带图公告文本',
                'image_count': 1,
                'images': [{'id': 'img-1', 'width': '640', 'height': '480'}],
            },
        )

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_tool_backend', _fake_call_tool_backend)

    result = await runner._handle_message(_make_group_event('/lute group notice clone 1020 notice123', user_id=QQ_ADMIN))

    assert 'Source notice includes 1 image(s).' in result
    assert '--image <url_or_path>' in result
    assert 'reply to an image message' in result.lower()
    assert '带图公告文本' in result


@pytest.mark.asyncio
async def test_lute_group_notice_clone_uses_referenced_reply_image_for_image_notice(monkeypatch, tmp_path):
    runner = _make_runner(enable_legacy_bare_commands=False)
    calls = []
    cached_image = tmp_path / 'quoted.png'
    cached_image.write_bytes(b'quoted-image')

    async def _fake_call_tool_backend(tool_name, args, **kwargs):
        calls.append((tool_name, args))
        if tool_name == 'qq_get_group_notice_detail':
            return LuteResponse(
                text='detail',
                payload={
                    'success': True,
                    'content': '带图公告文本',
                    'image_count': 1,
                    'images': [{'id': 'img-1', 'width': '640', 'height': '480'}],
                },
            )
        if tool_name == 'qq_send_group_notice':
            assert args == {
                'group_id': '1004',
                'content': '带图公告文本',
                'image_url': str(cached_image),
            }
            return LuteResponse(text='Notice cloned with referenced image')
        raise AssertionError(tool_name)

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_tool_backend', _fake_call_tool_backend)

    result = await runner._handle_message(
        _make_group_event(
            '/lute group notice clone 1020 notice123',
            user_id=QQ_ADMIN,
            reply_to_message_id='quoted-msg-1',
            referenced_media_urls=[str(cached_image)],
            referenced_media_types=['image'],
        )
    )

    assert result == 'Notice cloned with referenced image'
    assert [name for name, _ in calls] == ['qq_get_group_notice_detail', 'qq_send_group_notice']


@pytest.mark.asyncio
async def test_lute_group_notice_clone_accepts_manual_image_override(monkeypatch, tmp_path):
    runner = _make_runner(enable_legacy_bare_commands=False)
    calls = []
    cached_image = tmp_path / 'quoted.png'
    cached_image.write_bytes(b'quoted-image')

    async def _fake_call_tool_backend(tool_name, args, **kwargs):
        calls.append((tool_name, args))
        if tool_name == 'qq_get_group_notice_detail':
            return LuteResponse(
                text='detail',
                payload={
                    'success': True,
                    'content': '带图公告文本',
                    'image_count': 1,
                    'images': [{'id': 'img-1', 'width': '640', 'height': '480'}],
                },
            )
        if tool_name == 'qq_send_group_notice':
            assert args == {
                'group_id': '1004',
                'content': '带图公告文本',
                'image_url': 'file:///tmp/manual.png',
            }
            return LuteResponse(text='Notice cloned with manual image')
        raise AssertionError(tool_name)

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_tool_backend', _fake_call_tool_backend)

    result = await runner._handle_message(
        _make_group_event(
            '/lute group notice clone 1020 notice123 --image file:///tmp/manual.png',
            user_id=QQ_ADMIN,
            reply_to_message_id='quoted-msg-1',
            referenced_media_urls=[str(cached_image)],
            referenced_media_types=['image'],
        )
    )

    assert result == 'Notice cloned with manual image'
    assert [name for name, _ in calls] == ['qq_get_group_notice_detail', 'qq_send_group_notice']


@pytest.mark.asyncio
async def test_lute_comic_search_uses_script_backend_for_admin(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_script_backend(command, **kwargs):
        assert command[1].endswith('jm_mcp.py')
        assert command[2:] == ['search', '关键词', '--page', '1', '--json']
        return LuteResponse(text='Comic search result')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_script_backend', _fake_call_script_backend)

    result = await runner._handle_message(_make_event('/lute comic jm search 关键词', user_id=QQ_ADMIN))

    assert result == 'Comic search result'


@pytest.mark.asyncio
async def test_lute_comic_rank_uses_script_backend_for_admin(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_script_backend(command, **kwargs):
        assert command[1].endswith('jm_mcp.py')
        assert command[2:] == ['rank', 'month', '--page', '1', '--json']
        return LuteResponse(text='Comic rank result')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_script_backend', _fake_call_script_backend)

    result = await runner._handle_message(_make_event('/lute comic jm rank month', user_id=QQ_ADMIN))

    assert result == 'Comic rank result'


@pytest.mark.asyncio
async def test_lute_comic_rank_week_returns_usage():
    runner = _make_runner(enable_legacy_bare_commands=False)

    result = await runner._handle_message(_make_event('/lute comic jm rank week', user_id=QQ_ADMIN))

    assert result == 'Usage: /lute comic jm rank [day|month]'


@pytest.mark.asyncio
async def test_lute_comic_download_uses_script_backend_for_admin(monkeypatch):
    runner = _make_runner(enable_legacy_bare_commands=False)

    async def _fake_call_script_backend(command, **kwargs):
        assert command[1].endswith('jm_mcp.py')
        assert command[2:] == ['download', '12345', '--pack-format', 'zip', '--json']
        return LuteResponse(text='Comic download result')

    monkeypatch.setattr('gateway.qqbot_lute_handlers.call_script_backend', _fake_call_script_backend)

    result = await runner._handle_message(_make_event('/lute comic jm download 12345 --pack zip', user_id=QQ_ADMIN))

    assert result == 'Comic download result'


@pytest.mark.asyncio
async def test_lute_comic_requires_explicit_platform_selector():
    runner = _make_runner(enable_legacy_bare_commands=False)

    result = await runner._handle_message(_make_event('/lute comic search 关键词', user_id=QQ_ADMIN))

    assert result == 'Usage: /lute comic <platform> <verb> ...\nSupported platforms: jm'


@pytest.mark.asyncio
async def test_legacy_bare_help_in_approved_group_is_suppressed_when_compatibility_is_disabled():
    runner = _make_runner(enable_legacy_bare_commands=False)
    runner.config.qqbot.access.allow_groups = ['1004']
    runner.config.qqbot.access.group_user_allowlist = {'1004': [QQ_ALLOWED_USER]}

    result = await runner._handle_message(_make_group_event('/help', user_id=QQ_ALLOWED_USER))

    assert result == ''
    runner._handle_help_command.assert_not_called()
    runner._handle_message_with_agent.assert_not_called()


@pytest.mark.asyncio
async def test_non_admin_lute_group_info_is_denied():
    runner = _make_runner(enable_legacy_bare_commands=False)

    result = await runner._handle_message(_make_event('/lute group info 1004', user_id=QQ_ALLOWED_USER))

    assert result == ''


@pytest.mark.asyncio
async def test_legacy_bare_help_is_suppressed_when_compatibility_is_disabled():
    runner = _make_runner(enable_legacy_bare_commands=False)

    result = await runner._handle_message(_make_event('/help', user_id=QQ_ALLOWED_USER))

    assert result == ''
    runner._handle_help_command.assert_not_called()
    runner._handle_message_with_agent.assert_not_called()


@pytest.mark.asyncio
async def test_legacy_bare_help_still_works_when_compatibility_is_enabled():
    runner = _make_runner(enable_legacy_bare_commands=True)

    result = await runner._handle_message(_make_event('/help', user_id=QQ_ALLOWED_USER))

    assert result is not None
    assert result.startswith('QQ bot commands:')
    assert '/help' in result


@pytest.mark.asyncio
async def test_legacy_bare_ping_is_suppressed_when_compatibility_is_disabled():
    runner = _make_runner(enable_legacy_bare_commands=False)

    result = await runner._handle_message(_make_event('/ping', user_id=QQ_ALLOWED_USER))

    assert result == ''
    runner._handle_message_with_agent.assert_not_called()


@pytest.mark.asyncio
async def test_legacy_bare_ping_still_works_when_compatibility_is_enabled():
    runner = _make_runner(enable_legacy_bare_commands=True)

    result = await runner._handle_message(_make_event('/ping', user_id=QQ_ALLOWED_USER))

    assert result == 'pong'


@pytest.mark.asyncio
async def test_legacy_bare_admin_status_is_suppressed_when_compatibility_is_disabled():
    runner = _make_runner(enable_legacy_bare_commands=False)

    result = await runner._handle_message(_make_event('/admin status', user_id=QQ_ADMIN))

    assert result == ''
    runner._handle_message_with_agent.assert_not_called()


@pytest.mark.asyncio
async def test_legacy_bare_admin_status_still_works_when_compatibility_is_enabled():
    runner = _make_runner(enable_legacy_bare_commands=True)

    result = await runner._handle_message(_make_event('/admin status', user_id=QQ_ADMIN))

    assert result is not None
    assert 'QQ admin status' in result


@pytest.mark.asyncio
async def test_lute_domain_help_is_handled_before_legacy_router():
    runner = _make_runner(enable_legacy_bare_commands=False)

    result = await runner._handle_message(_make_event('/lute help bangumi', user_id=QQ_ALLOWED_USER))

    assert result is not None
    assert result.startswith('Bangumi 查询与放送表')
    assert '常用命令：' in result
    assert 'Examples:' not in result


@pytest.mark.asyncio
async def test_non_admin_lute_help_for_hidden_domain_stays_hidden():
    runner = _make_runner(enable_legacy_bare_commands=False)

    result = await runner._handle_message(_make_event('/lute help system', user_id=QQ_ALLOWED_USER))

    assert result == 'Unknown /lute section: system\nTry: /lute help'


@pytest.mark.asyncio
async def test_unknown_lute_domain_with_verb_returns_controlled_error():
    runner = _make_runner(enable_legacy_bare_commands=False)

    result = await runner._handle_message(_make_event('/lute mystery ping', user_id=QQ_ALLOWED_USER))

    assert result == 'Unknown /lute section: mystery\nTry: /lute help'


@pytest.mark.asyncio
async def test_unknown_lute_verb_for_known_domain_returns_controlled_error():
    runner = _make_runner(enable_legacy_bare_commands=False)

    result = await runner._handle_message(_make_event('/lute bangumi wrongverb', user_id=QQ_ALLOWED_USER))

    assert result == 'Unknown /lute verb: wrongverb\nTry: /lute help bangumi'


@pytest.mark.asyncio
async def test_lute_system_status_routes_to_admin_status_for_admin():
    runner = _make_runner(enable_legacy_bare_commands=False)

    result = await runner._handle_message(_make_event('/lute system status', user_id=QQ_ADMIN))

    assert result is not None
    assert 'QQ admin status' in result
    runner._handle_message_with_agent.assert_not_called()


@pytest.mark.asyncio
async def test_lute_system_status_is_denied_for_non_admin_users():
    runner = _make_runner(enable_legacy_bare_commands=False)

    result = await runner._handle_message(_make_event('/lute system status', user_id=QQ_ALLOWED_USER))

    assert result == ''


@pytest.mark.asyncio
async def test_malformed_lute_command_returns_controlled_usage_error():
    runner = _make_runner(enable_legacy_bare_commands=False)

    result = await runner._handle_message(_make_event('/lute "unterminated', user_id=QQ_ALLOWED_USER))

    assert result is not None
    assert result.startswith('Invalid /lute command:')
    assert result.endswith('Try: /lute help')


@pytest.mark.asyncio
async def test_non_command_free_text_still_uses_agent_path():
    runner = _make_runner(enable_legacy_bare_commands=False)
    runner.config.qqbot.capabilities.llm_users = [QQ_ALLOWED_USER]

    result = await runner._handle_message(_make_event('hello there', user_id=QQ_ALLOWED_USER))

    assert result == 'agent path'
    runner._handle_message_with_agent.assert_awaited_once()


@pytest.mark.asyncio
async def test_lute_config_allow_user_add_updates_live_runner_config(tmp_path, monkeypatch):
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
    runner = _make_runner(enable_legacy_bare_commands=False)

    result = await runner._handle_message(_make_event('/lute config allow user add 1013', user_id=QQ_ADMIN))

    assert result is not None
    assert '1013' in result
    assert '1013' in runner.config.qqbot.access.allow_users


@pytest.mark.asyncio
async def test_lute_config_allow_group_bootstraps_current_group_and_updates_live_runner_config(tmp_path, monkeypatch):
    _write_profile_config(
        tmp_path,
        '''
        qqbot:
          enabled: true
          platform: napcat
          access:
            admins:
              - "1003"
        ''',
    )
    monkeypatch.setenv('HERMES_HOME', str(tmp_path / '.hermes'))
    runner = _make_runner(enable_legacy_bare_commands=False)
    runner.config.qqbot.access.allow_groups = []

    result = await runner._handle_message(_make_group_event('/lute config allow group', user_id=QQ_ADMIN))

    assert result == 'QQ group 1004 added to allow_groups.'
    assert '1004' in runner.config.qqbot.access.allow_groups


@pytest.mark.asyncio
async def test_lute_group_commands_require_current_group_allowlist_for_admin_bootstrap(tmp_path, monkeypatch):
    _write_profile_config(
        tmp_path,
        '''
        qqbot:
          enabled: true
          platform: napcat
          access:
            admins:
              - "1003"
        ''',
    )
    monkeypatch.setenv('HERMES_HOME', str(tmp_path / '.hermes'))
    runner = _make_runner(enable_legacy_bare_commands=False)
    runner.config.qqbot.access.allow_groups = []

    result = await runner._handle_message(_make_group_event('/lute group admin show', user_id=QQ_ADMIN))

    assert result == 'Current QQ group 1004 is not allowlisted. Run /lute config allow group first.'


@pytest.mark.asyncio
async def test_lute_config_allow_group_user_requires_group_bootstrap_first(tmp_path, monkeypatch):
    _write_profile_config(
        tmp_path,
        '''
        qqbot:
          enabled: true
          platform: napcat
          access:
            admins:
              - "1003"
        ''',
    )
    monkeypatch.setenv('HERMES_HOME', str(tmp_path / '.hermes'))
    runner = _make_runner(enable_legacy_bare_commands=False)
    runner.config.qqbot.access.allow_groups = []

    result = await runner._handle_message(_make_group_event('/lute config allow group-user add 1010', user_id=QQ_ADMIN))

    assert result == 'Current QQ group 1004 is not allowlisted. Run /lute config allow group first.'


@pytest.mark.asyncio
async def test_lute_config_allow_group_user_shortcut_updates_current_group_mapping(tmp_path, monkeypatch):
    _write_profile_config(
        tmp_path,
        '''
        qqbot:
          enabled: true
          platform: napcat
          access:
            admins:
              - "1003"
            allow_groups:
              - "1004"
        ''',
    )
    monkeypatch.setenv('HERMES_HOME', str(tmp_path / '.hermes'))
    runner = _make_runner(enable_legacy_bare_commands=False)

    result = await runner._handle_message(_make_group_event('/lute config allow group-user add 1010', user_id=QQ_ADMIN))

    assert result == 'QQ user 1010 added to group_user_allowlist[1004].'
    assert runner.config.qqbot.access.group_user_allowlist == {'1004': ['1010']}
