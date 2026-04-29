from gateway.qqbot_config import QQBotCLIConfig, QQBotCLIDomainConfig, QQBotConfig, QQBotHelpMenuRenderConfig, QQBotViewConfig
from gateway.qqbot_lute_aliases import resolve_lute_command_alias
from gateway.qqbot_policy import QQBotPolicy
from gateway.qqbot_lute_registry import (
    build_default_lute_registry,
    build_default_lute_root_commands,
    build_lute_root_help_view,
    parse_lute_invocation,
    render_lute_help,
)
from gateway.qqbot_lute_usage import LuteUsageEntry, render_single_usage, render_usage_list


def test_parse_lute_invocation_handles_quoted_args_and_options():
    invocation = parse_lute_invocation('/lute utility weather "New York" --days 3')

    assert invocation.root == 'lute'
    assert invocation.domain == 'utility'
    assert invocation.verb == 'weather'
    assert invocation.args == ['New York']
    assert invocation.options == {'days': '3'}


def test_parse_lute_invocation_treats_help_as_root_level_command():
    invocation = parse_lute_invocation('/lute help bangumi')

    assert invocation.root == 'lute'
    assert invocation.command == 'help'
    assert invocation.domain == 'bangumi'
    assert invocation.verb == ''


def test_parse_lute_invocation_treats_menu_as_root_level_command():
    invocation = parse_lute_invocation('/lute menu bangumi')

    assert invocation.root == 'lute'
    assert invocation.command == 'menu'
    assert invocation.domain == 'bangumi'
    assert invocation.verb == ''


def test_build_default_lute_root_commands_contains_core_commands():
    commands = build_default_lute_root_commands()

    assert list(commands) == ['help', 'menu', 'ping', 'status', 'show']
    assert commands['help'].summary
    assert commands['ping'].example == '/lute ping'


def test_parse_lute_invocation_accepts_configured_root_and_aliases():
    cli = QQBotCLIConfig(root_command='harp', aliases=['lyre'])

    root_invocation = parse_lute_invocation('/harp help bangumi', cli)
    alias_invocation = parse_lute_invocation('/lyre utility weather 北京', cli)

    assert root_invocation.root == 'harp'
    assert root_invocation.command == 'help'
    assert root_invocation.domain == 'bangumi'
    assert alias_invocation.root == 'harp'
    assert alias_invocation.domain == 'utility'
    assert alias_invocation.verb == 'weather'
    assert alias_invocation.args == ['北京']


def test_parse_lute_invocation_accepts_high_level_chinese_command_alias_domain():
    invocation = parse_lute_invocation('/lute 知乎热搜')

    assert invocation.root == 'lute'
    assert invocation.domain == '知乎热搜'
    assert invocation.verb == ''
    assert invocation.args == []


def test_resolve_lute_command_alias_maps_to_canonical_command_and_clears_options():
    invocation = parse_lute_invocation('/lute B站热搜 --unused value')

    resolved = resolve_lute_command_alias(invocation)

    assert resolved is not None
    assert resolved.domain == 'feed'
    assert resolved.verb == 'fetch'
    assert resolved.args == ['7']
    assert resolved.options == {}


def test_resolve_lute_command_alias_rejects_non_bare_alias_form():
    invocation = parse_lute_invocation('/lute 知乎热搜 extra')

    assert resolve_lute_command_alias(invocation) is None


def test_resolve_lute_command_alias_maps_github_trending_to_feed_subscription_title():
    invocation = parse_lute_invocation('/lute github trending')

    resolved = resolve_lute_command_alias(invocation)

    assert resolved is not None
    assert resolved.domain == 'feed'
    assert resolved.verb == 'fetch'
    assert resolved.args == ['GitHub Trending']
    assert resolved.options == {}


def test_resolve_lute_command_alias_maps_github_trending_refresh_to_feed_refresh():
    invocation = parse_lute_invocation('/lute github trending refresh')

    resolved = resolve_lute_command_alias(invocation)

    assert resolved is not None
    assert resolved.domain == 'feed'
    assert resolved.verb == 'fetch'
    assert resolved.args == ['GitHub Trending']
    assert resolved.options == {'refresh': True}


def test_resolve_lute_command_alias_rejects_github_trending_refresh_with_extra_args():
    invocation = parse_lute_invocation('/lute github trending refresh extra')

    assert resolve_lute_command_alias(invocation) is None


def test_resolve_lute_command_alias_maps_github_trending_chinese_alias_case_insensitively():
    invocation = parse_lute_invocation('/lute GitHub趋势')

    resolved = resolve_lute_command_alias(invocation)

    assert resolved is not None
    assert resolved.domain == 'feed'
    assert resolved.verb == 'fetch'
    assert resolved.args == ['GitHub Trending']
    assert resolved.options == {}


def test_resolve_lute_command_alias_rejects_github_trending_with_extra_args():
    invocation = parse_lute_invocation('/lute github trending today')

    assert resolve_lute_command_alias(invocation) is None


def test_resolve_github_trending_ai_tools_filter_via_chinese_alias():
    invocation = parse_lute_invocation('/lute github趋势 ai-tools')

    resolved = resolve_lute_command_alias(invocation)

    assert resolved is not None
    assert resolved.domain == 'feed'
    assert resolved.verb == 'fetch'
    assert resolved.args == ['GitHub Trending']
    assert resolved.options == {'filter': 'ai-tools'}


def test_resolve_github_trending_ai_agents_filter_via_chinese_alias():
    invocation = parse_lute_invocation('/lute github趋势 ai-agents')

    resolved = resolve_lute_command_alias(invocation)

    assert resolved is not None
    assert resolved.domain == 'feed'
    assert resolved.verb == 'fetch'
    assert resolved.args == ['GitHub Trending']
    assert resolved.options == {'filter': 'ai-agents'}


def test_resolve_github_trending_ai_tool_no_trailing_s():
    invocation = parse_lute_invocation('/lute github趋势 ai-tool')

    resolved = resolve_lute_command_alias(invocation)

    assert resolved is not None
    assert resolved.domain == 'feed'
    assert resolved.verb == 'fetch'
    assert resolved.args == ['GitHub Trending']
    assert resolved.options == {'filter': 'ai-tools'}


def test_resolve_github_trending_ai_agent_no_trailing_s():
    invocation = parse_lute_invocation('/lute github趋势 ai-agent')

    resolved = resolve_lute_command_alias(invocation)

    assert resolved is not None
    assert resolved.domain == 'feed'
    assert resolved.verb == 'fetch'
    assert resolved.args == ['GitHub Trending']
    assert resolved.options == {'filter': 'ai-agents'}


def test_resolve_github_trending_ai_tools_case_insensitive():
    invocation = parse_lute_invocation('/lute GitHub趋势 AI-TOOLS')

    resolved = resolve_lute_command_alias(invocation)

    assert resolved is not None
    assert resolved.domain == 'feed'
    assert resolved.verb == 'fetch'
    assert resolved.args == ['GitHub Trending']
    assert resolved.options == {'filter': 'ai-tools'}


def test_resolve_github_trending_english_ai_tools_filter():
    invocation = parse_lute_invocation('/lute github trending ai-tools')

    resolved = resolve_lute_command_alias(invocation)

    assert resolved is not None
    assert resolved.domain == 'feed'
    assert resolved.verb == 'fetch'
    assert resolved.args == ['GitHub Trending']
    assert resolved.options == {'filter': 'ai-tools'}


def test_resolve_github_trending_english_ai_agents_filter():
    invocation = parse_lute_invocation('/lute github trending ai-agents')

    resolved = resolve_lute_command_alias(invocation)

    assert resolved is not None
    assert resolved.domain == 'feed'
    assert resolved.verb == 'fetch'
    assert resolved.args == ['GitHub Trending']
    assert resolved.options == {'filter': 'ai-agents'}


def test_resolve_github_trending_chinese_alias_still_works_without_filter():
    invocation = parse_lute_invocation('/lute github趋势')

    resolved = resolve_lute_command_alias(invocation)

    assert resolved is not None
    assert resolved.domain == 'feed'
    assert resolved.verb == 'fetch'
    assert resolved.args == ['GitHub Trending']
    assert resolved.options == {}
    assert 'filter' not in resolved.options


def test_render_lute_root_help_is_function_area_first_for_normal_users():
    registry = build_default_lute_registry()

    text = render_lute_help(registry, is_admin=False)

    expected_areas = [
        '🧭 基础说明',
        '🤖 AI 助手',
        '🛠 实用工具',
        '🎮 娱乐休闲',
        '🖼 多媒体',
        '🔍 信息查询',
        '📦 资源搜索',
        '🔔 订阅通知',
        '👤 个人中心',
    ]
    positions = [text.index(area) for area in expected_areas]
    assert positions == sorted(positions)
    assert '用法: /lute <功能|命令> [参数] [--选项]' in text
    assert '/lute、/lute menu、/lute help 都会打开同一份露特菜单。' in text
    assert '- /lute / /lute menu / /lute help: 打开露特菜单' in text
    assert '- /lute help <模块>: 查看某个模块的二级菜单' in text
    assert '🛡 群管功能' not in text
    assert '⚙️ 系统设置' not in text
    assert '管理员可见' not in text
    assert '管理菜单' not in text

    assert '📊群分析: 智能群聊日常分析' in text
    assert '- /lute analyze' in text
    assert '👤用户画像: 分析群友公开互动画像' in text
    assert '- /lute profile @用户' in text
    assert '🧂锐评: 让露特来一句犀利点评' in text
    assert '- /lute roast <内容>' in text
    assert '❓这是什么: 识别图片或解释陌生事物' in text
    assert '- /lute what' in text
    assert '💬和露特对话: 直接和露特聊天' in text
    assert '- /lute chat <内容>' in text

    assert '🗓节假日查询: 查询节假日和调休安排' in text
    assert '- /lute holiday' in text
    assert '🎲随机数: 生成随机数或帮你做选择' in text
    assert '- /lute random <最小值> <最大值>' in text
    assert '🔐加解密: 佛曰、熊曰等趣味加解密' in text
    assert '- /lute crypto fo encode <文本>' in text
    assert '🔳二维码生成/解析: 生成或识别二维码' in text
    assert '- /lute qr <文本或图片>' in text

    assert '🆓Epic: 查看本周 Epic 免费游戏' in text
    assert '🍀今日运势: 看今天的运势签' in text
    assert '🍗疯狂星期四: 生成 KFC 疯狂星期四文案' in text
    assert '🤒发病小作文: 生成发病文学小作文' in text

    assert '🎨图片生成: 生成或改图' in text
    assert '🔤图片转字符画: 把图片转成字符画' in text
    assert '🖼搜图: 提供 URL 或图片查来源' in text
    assert '🎞视频截图: 从视频中截取画面' in text
    assert '📺B站总结: 总结 B 站视频' not in text[text.index('🖼 多媒体'):text.index('🔍 信息查询')]

    info_section = text[text.index('🔍 信息查询'):text.index('📦 资源搜索')]
    assert '📺B站总结: 总结 B 站视频' in info_section
    assert '🛡CVE: 查询漏洞编号和风险信息' in text
    assert '- /lute cve <cve编号>' in text
    assert '🌐WHOIS: 查询域名注册信息' in info_section
    assert '- /lute whois <域名>' in text
    assert '📺番剧条目' not in info_section
    assert '🐙GitHub仓库' not in info_section
    assert '🎭表情包: 搜索表情包/GIF' in text
    assert '- /lute pixiv' in text
    assert '- /lute pixiv search 初音ミク' not in text
    assert '- /lute bangumi' in text
    assert '- /lute bangumi search <关键词>' not in text
    assert '- /lute torrent' in text
    assert '- /lute paper' in text
    assert '- /lute book' in text
    assert '- /lute github' in text
    assert '- /lute gif' in text
    assert '🤖AI早报: 生成高质量 AI 每日早报 PDF' in text
    assert '- /lute AI早报' in text
    assert '🎒背包: 查看和露特互动获得的道具' in text
    assert '💗好感度: 查看和露特的好感度' in text
    feed_section = text[text.index('🔔 订阅通知'):text.index('👤 个人中心')]
    assert '📰RSS订阅: 查看和拉取 RSS 订阅' in feed_section
    assert '- /lute feed' in feed_section
    assert '- /lute feed list' not in feed_section
    assert '🔥知乎热搜: 查看知乎热搜' in feed_section
    assert '- /lute 知乎热搜' in feed_section
    assert '📺B站热搜: 查看 B 站热搜' in feed_section
    assert '- /lute B站热搜' in feed_section
    assert '🐙GitHub趋势: 查看 GitHub Trending' in feed_section
    assert '- /lute github趋势' in feed_section
    assert '- /lute github trending' not in feed_section
    assert '/lute hot zhihu' not in text
    assert '/lute hot bili' not in text


def test_build_lute_root_help_view_uses_image_menu_shape_for_normal_users():
    registry = build_default_lute_registry()

    view = build_lute_root_help_view(registry, is_admin=False)

    assert view.kind == 'image'
    assert view.template == 'help.usage-card'
    assert view.data['variant'] == 'help-menu'
    assert view.data['title'] == 'Lute Help'
    assert view.data['subtitle'] == '按功能区查看命令；详细帮助用 /lute help <模块>'
    assert view.data['avatar_text'] == '露特'
    assert view.data['badge_text'].endswith('条命令')
    assert 'help <模块>' not in view.data['badge_text']
    assert view.data['help_menu'] == {
        'quality': 70,
        'scale_factor': 1.5,
        'min_width': 1024,
        'min_height': 720,
        'max_width': 1480,
        'max_height': 4300,
    }
    assert 'identity_label' not in view.data
    assert 'description' not in view.data
    assert 'notice' not in view.data
    assert view.fallback_text == render_lute_help(registry, is_admin=False)
    assert view.cache_policy is None
    assert view.telemetry_tags == {'domain': 'lute', 'verb': 'help'}
    modules = view.data['modules']
    assert [module['title'] for module in modules] == [
        '🧭 基础说明',
        '🤖 AI 助手',
        '🛠 实用工具',
        '🎮 娱乐休闲',
        '🖼 多媒体',
        '🔍 信息查询',
        '📦 资源搜索',
        '🔔 订阅通知',
        '👤 个人中心',
    ]
    assert len(modules[0]['commands']) == 2
    assert modules[0]['commands'][0]['name'] == '打开菜单'
    assert modules[0]['commands'][0]['description'].startswith('这三个入口都会打开同一份露特菜单')
    assert modules[0]['commands'][0]['syntax'] == '/lute / /lute menu / /lute help'
    assert modules[0]['commands'][1]['name'] == '二级菜单'
    assert modules[0]['commands'][1]['syntax'] == '/lute help <模块>'
    assert all(module.get('commands') for module in modules)
    assert all('syntax' in command and 'description' in command for module in modules for command in module['commands'])
    assert not any('群管功能' in module['title'] for module in modules)
    feed_module = next(module for module in modules if module['title'] == '🔔 订阅通知')
    syntaxes = {command['name']: command['syntax'] for command in feed_module['commands']}
    assert syntaxes['RSS订阅'] == '/lute feed'
    assert syntaxes['知乎热搜'] == '/lute 知乎热搜'
    assert syntaxes['B站热搜'] == '/lute B站热搜'
    assert syntaxes['GitHub趋势'] == '/lute github趋势'


def test_build_lute_root_help_view_uses_policy_help_menu_render_config():
    registry = build_default_lute_registry()
    policy = QQBotPolicy(
        QQBotConfig(
            view=QQBotViewConfig(
                help_menu=QQBotHelpMenuRenderConfig(
                    quality=62,
                    scale_factor=1.25,
                    min_width=980,
                    min_height=760,
                    max_width=1520,
                    max_height=4100,
                )
            )
        )
    )

    view = build_lute_root_help_view(registry, is_admin=False, policy=policy)

    assert view.data['help_menu'] == {
        'quality': 62,
        'scale_factor': 1.25,
        'min_width': 980,
        'min_height': 760,
        'max_width': 1520,
        'max_height': 4100,
    }


def test_render_lute_domain_help_for_bangumi_uses_description_and_common_commands():
    registry = build_default_lute_registry()

    text = render_lute_help(registry, is_admin=False, domain='bangumi')

    assert text.startswith('Bangumi 查询与放送表\n今天放送、题材搜索和条目详情。')
    assert '\n\n常用命令：\n' in text
    assert '- today: 今日放送' in text
    assert '- search: 搜索番剧' in text
    assert '- subject: 查看条目详情' in text
    assert 'Access:' not in text
    assert 'Verbs:' not in text
    assert 'Examples:' not in text


def test_render_lute_domain_help_for_pixiv_contains_rank_verb():
    registry = build_default_lute_registry()

    text = render_lute_help(registry, is_admin=False, domain='pixiv')

    assert '- rank: 查看排行榜' in text
    assert '- illust: 查看作品详情' in text
    assert '- related: 查看相关作品' in text
    assert '- user:' not in text
    assert 'Examples:' not in text


def test_render_lute_domain_help_for_torrent_contains_fallback_and_analyze():
    registry = build_default_lute_registry()

    text = render_lute_help(registry, is_admin=False, domain='torrent')

    assert '- stream: 查看可观看平台' in text
    assert '- fallback: 显式使用备用搜索' in text
    assert '- analyze: 分析磁链或哈希' in text
    assert 'Examples:' not in text


def test_render_lute_domain_help_for_utility_shows_grouped_common_commands():
    registry = build_default_lute_registry()

    text = render_lute_help(registry, is_admin=False, domain='utility')

    assert text.startswith('日常实用工具\n按 lookup/profile/media/fun 分组的常用工具。')
    assert '\n\n常用命令：\n' in text
    assert 'lookup:' in text
    assert 'profile:' in text
    assert 'media:' in text
    assert 'fun:' not in text
    assert '- weather: 查天气' in text
    assert '- whois: 查域名 WHOIS' in text
    assert '- github: 查询 GitHub 仓库信息' in text
    assert '- qr: 生成二维码' in text
    assert '- news: 查看每日新闻' in text
    assert 'Verbs:' not in text
    assert 'Examples:' not in text


def test_render_lute_domain_help_for_feed_hides_admin_mutation_verbs_from_non_admin_users():
    registry = build_default_lute_registry()

    text = render_lute_help(registry, is_admin=False, domain='feed')

    assert '- list: 列出现有订阅' in text
    assert '- fetch: 抓取指定订阅' in text
    assert '- scan: 扫描更新' in text
    assert '- add:' not in text
    assert '- remove:' not in text
    assert '- export:' not in text


def test_render_lute_domain_help_for_feed_shows_admin_mutation_verbs_to_admin_users():
    registry = build_default_lute_registry()

    text = render_lute_help(registry, is_admin=True, domain='feed')

    assert '- add: 添加订阅' in text
    assert '- remove: 删除订阅' in text
    assert '- export:' not in text


def test_render_lute_domain_help_for_pixiv_hides_admin_verbs_from_non_admin_users():
    registry = build_default_lute_registry()

    text = render_lute_help(registry, is_admin=False, domain='pixiv')

    assert '- search: 搜索作品' in text
    assert '- rank: 查看排行榜' in text
    assert '- download:' not in text
    assert '- auth:' not in text
    assert '- status:' not in text
    assert '- sync:' not in text


def test_render_lute_domain_help_for_pixiv_shows_only_download_admin_verb_to_admin_users():
    registry = build_default_lute_registry()

    text = render_lute_help(registry, is_admin=True, domain='pixiv')

    assert '- download: 下载作品' in text
    assert '- auth:' not in text
    assert '- status:' not in text
    assert '- sync:' not in text


def test_render_lute_help_hides_admin_domains_for_non_admin_users():
    registry = build_default_lute_registry()

    text = render_lute_help(registry, is_admin=False)

    assert '🛡 群管功能' not in text
    assert '⚙️ 系统设置' not in text
    assert 'group' not in text.lower()
    assert 'system' not in text.lower()
    assert 'config' not in text.lower()
    assert 'service' not in text.lower()
    assert 'comic' not in text.lower()


def test_render_lute_help_shows_admin_commands_inside_function_areas_for_admin_users():
    registry = build_default_lute_registry()

    text = render_lute_help(registry, is_admin=True)

    assert '🛡 群管功能' in text
    assert '⚙️ 系统设置' in text
    assert '📋群信息: 查看当前群信息' in text
    assert '- /lute group info' in text
    assert '👥群成员: 查看或管理当前群成员' in text
    assert '- /lute group member' in text
    assert '- /lute group member list' not in text
    assert '📢群公告: 发送或查看当前群公告' in text
    assert '- /lute group notice' in text
    assert '🕘群历史: 查看当前群历史消息' in text
    assert '- /lute group history' in text
    assert '🤖群管自动化: 查看或调整本群自动管理配置' in text
    assert '- /lute group admin' in text
    assert '🏅群荣誉' not in text
    assert '🔇群禁言' not in text
    assert '👉群互动' not in text
    assert '💬QQ消息: 跨会话 QQ 消息发送与转发' in text
    assert '- /lute qq message' in text
    assert '📁群文件: 群文件浏览与管理' in text
    assert '- /lute qq file' in text
    assert '🖥系统状态: 查看系统状态' in text
    assert '- /lute system status' in text
    assert '🔄系统重载: 重载 QQ bot 配置' in text
    assert '- /lute system reload' in text
    assert '🤖模型配置: 查看或调整模型相关配置' in text
    assert '- /lute config model' in text
    assert '🔀功能开关: 控制开启哪些功能板块' in text
    assert '- /lute config feature' in text
    assert '🏷版本信息: 查看 Lute 和运行环境版本' in text
    assert '- /lute system version' in text
    assert '📜日志查看: 查看最近运行日志' in text
    assert '- /lute system logs' in text
    assert '🔧权限配置: 管理用户、群组和命令访问范围' in text
    assert '- /lute config allow' in text
    assert '- /lute config allow user add 1003' not in text
    assert '🧠LLM授权: 管理用户 LLM 权限' in text
    assert '- /lute config llm' in text
    assert '🧩服务集成' not in text
    assert '🧵服务运行时' not in text
    assert '- /lute service' not in text
    assert '📖漫画' in text
    assert '- /lute comic jm' in text


def test_render_lute_domain_help_for_group_describes_current_group_only_boundary():
    registry = build_default_lute_registry()

    text = render_lute_help(registry, is_admin=True, domain='group')

    assert text.startswith('本群管理\n仅在当前群内查看成员、公告、历史与管理操作。')
    assert '\n\n常用命令：\n' in text
    assert '仅在当前群内' in text
    assert '- info: 查看当前群信息' in text
    assert '- member: 查看或管理当前群成员' in text
    assert '- admin: 查看或调整本群 qqadmin 自动化配置' in text
    assert '/lute group info 1004' not in text
    assert '/lute qq message' in text
    assert '/lute qq file' in text
    assert '/lute qq request' not in text
    assert '/lute qq contact' not in text
    assert 'Access:' not in text
    assert 'Verbs:' not in text


def test_render_lute_domain_help_for_group_includes_admin_subtree_for_admin_users():
    registry = build_default_lute_registry()

    text = render_lute_help(registry, is_admin=True, domain='group')

    assert '- admin:' in text
    assert 'Examples:' not in text


def test_render_lute_domain_help_for_group_shows_batch1_admin_verbs():
    registry = build_default_lute_registry()

    text = render_lute_help(registry, is_admin=True, domain='group')

    assert '- name: 修改当前群名称' in text
    assert '- portrait:' not in text
    assert '- whole-ban: 开关全群禁言' in text
    assert '- sign: 当前群打卡' in text
    assert '- at-all: 查看 bot 账号在当前群的 @全体剩余次数' in text


def test_render_lute_help_shows_current_non_group_local_split_to_admins():
    registry = build_default_lute_registry()

    text = render_lute_help(registry, is_admin=True)

    assert '/lute qq message' in text
    assert '/lute qq file' in text
    assert '/lute qq request' not in text
    assert '/lute qq contact' not in text


def test_render_lute_domain_help_for_group_shows_batch2_message_ops_verbs():
    registry = build_default_lute_registry()

    text = render_lute_help(registry, is_admin=True, domain='group')

    assert '- essence: 管理当前群精华消息' in text
    assert '- react: 管理当前群消息表情回应' in text
    assert '- read: 标记当前群消息已读' in text


def test_render_lute_domain_help_for_qq_shows_message_and_file_scopes_to_admin_users():
    registry = build_default_lute_registry()

    text = render_lute_help(registry, is_admin=True, domain='qq')

    assert '- message: 跨会话 QQ 消息发送与转发' in text
    assert '- file: 群文件浏览与管理' in text
    assert 'Examples:' not in text
    assert '/lute qq message send private 1003 你好' not in text
    assert '/lute qq file list 1004' not in text


def test_render_lute_domain_help_for_qq_shows_only_message_and_file_scopes():
    registry = build_default_lute_registry()

    text = render_lute_help(registry, is_admin=True, domain='qq')

    assert '- message: 跨会话 QQ 消息发送与转发' in text
    assert '- file: 群文件浏览与管理' in text
    assert 'Examples:' not in text


def test_render_lute_domain_help_for_comic_shows_platform_entry_to_admin_users():
    registry = build_default_lute_registry()

    text = render_lute_help(registry, is_admin=True, domain='comic')

    assert '- jm: 使用 JM 平台' in text
    assert 'Examples:' not in text
    assert '/lute comic jm search 关键词' not in text
    assert '/lute comic search 关键词' not in text


def test_render_lute_help_unknown_domain_returns_controlled_error():
    registry = build_default_lute_registry()

    text = render_lute_help(registry, is_admin=False, domain='unknown')

    assert text.startswith('Unknown /lute section: unknown')
    assert '/lute help' in text


def test_build_default_lute_registry_respects_cli_domain_overrides():
    cli = QQBotCLIConfig(
        domains={
            'utility': QQBotCLIDomainConfig(enabled=False, access='user', visible_in_help=True),
            'service': QQBotCLIDomainConfig(enabled=True, access='admin', visible_in_help=False),
        }
    )

    registry = build_default_lute_registry(cli)

    assert 'utility' not in registry
    assert registry['service'].visible_in_help is False
    assert registry['service'].access == 'admin'


def test_build_default_lute_registry_does_not_de_restrict_admin_domains():
    cli = QQBotCLIConfig(
        domains={
            'group': QQBotCLIDomainConfig(enabled=True, access='user', visible_in_help=True),
        }
    )

    registry = build_default_lute_registry(cli)

    assert registry['group'].access == 'admin'
    assert registry['group'].visible_in_help is False


def test_render_lute_help_uses_configured_root_command_in_examples():
    cli = QQBotCLIConfig(root_command='harp')
    registry = build_default_lute_registry(cli)

    text = render_lute_help(registry, is_admin=False, cli=cli)

    assert '/harp analyze' in text
    assert '/lute analyze' not in text


def test_render_lute_help_shows_promoted_visible_admin_domains_to_admins():
    cli = QQBotCLIConfig(
        domains={
            'utility': QQBotCLIDomainConfig(enabled=True, access='admin', visible_in_help=True),
        }
    )
    registry = build_default_lute_registry(cli)

    text = render_lute_help(registry, is_admin=True, cli=cli)

    assert '🛠 实用工具' in text
    assert '/lute weather 北京' in text


def test_render_single_usage_returns_one_line_syntax():
    text = render_single_usage('/lute group admin curfew set <HH:MM> <HH:MM>')

    assert text == 'Usage: /lute group admin curfew set <HH:MM> <HH:MM>'


def test_render_usage_list_returns_one_syntax_per_line_with_descriptions():
    text = render_usage_list(
        [
            LuteUsageEntry('/lute group admin show', '查看本群 qqadmin 总览'),
            LuteUsageEntry('/lute group admin status', '查看本群 qqadmin 总览（show 别名）'),
            LuteUsageEntry('/lute group admin reset', '重置本群 qqadmin 配置'),
        ]
    )

    assert text == (
        'Usage:\n'
        '- /lute group admin show  查看本群 qqadmin 总览\n'
        '- /lute group admin status  查看本群 qqadmin 总览（show 别名）\n'
        '- /lute group admin reset  重置本群 qqadmin 配置'
    )
