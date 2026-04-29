from __future__ import annotations

import shlex
from copy import deepcopy

from gateway.qqbot_config import QQBotCLIConfig
from gateway.qqbot_policy import QQBotPolicy
from gateway.qqbot_lute_types import LuteDomainSpec, LuteInvocation, LuteVerbSpec
from gateway.view.types import ViewSpec


_DEFAULT_DOMAINS: dict[str, LuteDomainSpec] = {
    'epic': LuteDomainSpec(
        name='epic',
        summary='本周 Epic 免费游戏',
        task_label='领免费游戏 / Epic 本周免费',
        task_summary='快速看本周可领和下周预告。',
        examples=['/lute epic', '/lute epic weekly'],
        verbs={'weekly': LuteVerbSpec(name='weekly', summary='查看本周免费', example='/lute epic weekly')},
    ),
    'bangumi': LuteDomainSpec(
        name='bangumi',
        summary='Bangumi 查询与放送表',
        task_label='看番剧 / 今天放送',
        task_summary='今天放送、题材搜索和条目详情。',
        examples=['/lute bangumi today', '/lute bangumi search [<关键词>] [--tag <标签>] [--meta-tag <公共标签>] [--year <年份>] [--image]', '/lute bangumi subject 1 [more_ids...] [--image]'],
        verbs={
            'today': LuteVerbSpec(name='today', summary='今日放送', example='/lute bangumi today'),
            'search': LuteVerbSpec(name='search', summary='搜索番剧', example='/lute bangumi search [<关键词>] [--tag <标签>] [--meta-tag <公共标签>] [--year <年份>] [--image]'),
            'subject': LuteVerbSpec(name='subject', summary='查看条目详情', example='/lute bangumi subject 1 [more_ids...] [--image]'),
        },
    ),
    'bili': LuteDomainSpec(
        name='bili',
        summary='B 站视频总结',
        task_label='总结 B 站视频',
        task_summary='输入 BV 号或链接，快速看视频主要讲了什么。',
        examples=['/lute bili read BV1GJ411x7h7'],
        verbs={'read': LuteVerbSpec(name='read', summary='总结视频', example='/lute bili read BV1GJ411x7h7')},
    ),
    'feed': LuteDomainSpec(
        name='feed',
        summary='订阅与热榜动态',
        task_label='看订阅 / 热榜动态',
        task_summary='查看已有订阅、拉取内容、扫描更新。',
        examples=['/lute feed list', '/lute feed fetch 7'],
        verbs={
            'list': LuteVerbSpec(name='list', summary='列出现有订阅', example='/lute feed list'),
            'fetch': LuteVerbSpec(name='fetch', summary='抓取指定订阅', example='/lute feed fetch 7 [refresh|--refresh]'),
            'scan': LuteVerbSpec(name='scan', summary='扫描更新', example='/lute feed scan', access='admin'),
            'add': LuteVerbSpec(name='add', summary='添加订阅', example='/lute feed add https://example.com/rss --title 示例订阅', access='admin'),
            'remove': LuteVerbSpec(name='remove', summary='删除订阅', example='/lute feed remove 7', access='admin'),
        },
    ),
    'ai-news': LuteDomainSpec(
        name='ai-news',
        summary='AI 早报',
        task_label='生成 AI 每日早报',
        task_summary='聚合高质量 AI RSS，输出精排 PDF 早报。',
        examples=['/lute ai-news daily', '/lute AI早报', '/lute AI日报'],
        verbs={
            'daily': LuteVerbSpec(name='daily', summary='生成今日 AI 早报 PDF', example='/lute ai-news daily'),
        },
    ),
    'image': LuteDomainSpec(
        name='image',
        summary='图片反向搜索',
        task_label='以图搜图',
        task_summary='给图片 URL 或本地路径，查来源和标题。',
        examples=['/lute image search https://example.com/image.jpg'],
        verbs={'search': LuteVerbSpec(name='search', summary='反向搜图', example='/lute image search https://example.com/image.jpg')},
    ),
    'pixiv': LuteDomainSpec(
        name='pixiv',
        summary='Pixiv 浏览与排行',
        task_label='找 Pixiv 作品',
        task_summary='搜作品、看排行、看详情。',
        examples=['/lute pixiv search 初音ミク', '/lute pixiv rank day'],
        verbs={
            'search': LuteVerbSpec(name='search', summary='搜索作品', example='/lute pixiv search 初音ミク'),
            'rank': LuteVerbSpec(name='rank', summary='查看排行榜', example='/lute pixiv rank day'),
            'illust': LuteVerbSpec(name='illust', summary='查看作品详情', example='/lute pixiv illust 1000'),
            'related': LuteVerbSpec(name='related', summary='查看相关作品', example='/lute pixiv related 1000'),
            'download': LuteVerbSpec(name='download', summary='下载作品', example='/lute pixiv download 1000', access='admin'),
        },
    ),
    'torrent': LuteDomainSpec(
        name='torrent',
        summary='影视资源搜索与分析',
        task_label='搜影视资源',
        task_summary='走 TorrentClaw fast path；fallback 需显式触发。',
        examples=['/lute torrent search shogun s01e05', '/lute torrent analyze <magnet_or_hash>'],
        verbs={
            'search': LuteVerbSpec(name='search', summary='搜索资源', example='/lute torrent search shogun s01e05'),
            'stream': LuteVerbSpec(name='stream', summary='查看可观看平台', example='/lute torrent stream 42 --country US'),
            'fallback': LuteVerbSpec(name='fallback', summary='显式使用备用搜索', example='/lute torrent fallback shogun s01e05'),
            'analyze': LuteVerbSpec(name='analyze', summary='分析磁链或哈希', example='/lute torrent analyze <magnet_or_hash>'),
        },
    ),
    'book': LuteDomainSpec(
        name='book',
        summary='电子书搜索与元数据查询',
        task_label='搜电子书',
        task_summary='搜索电子书、看最近新增和下载额度。',
        examples=['/lute book search domain-driven design', '/lute book recent', '/lute book limits'],
        verbs={
            'search': LuteVerbSpec(name='search', summary='搜索电子书', example='/lute book search domain-driven design'),
            'recent': LuteVerbSpec(name='recent', summary='查看近期新增', example='/lute book recent'),
            'limits': LuteVerbSpec(name='limits', summary='查看下载额度', example='/lute book limits'),
            'metadata': LuteVerbSpec(name='metadata', summary='查看书籍元数据', example='/lute book metadata 1001 --hash 1002'),
        },
    ),
    'utility': LuteDomainSpec(
        name='utility',
        summary='日常实用工具',
        task_label='查天气 / 二维码 / 常用工具',
        task_summary='按 lookup/profile/media/fun 分组的常用工具。',
        examples=['/lute utility weather 北京', '/lute utility qr https://example.com', '/lute utility whois example.com', '/lute utility news'],
        verbs={
            'weather': LuteVerbSpec(name='weather', summary='查天气', example='/lute utility weather 北京', help_group='lookup'),
            'whois': LuteVerbSpec(name='whois', summary='查域名 WHOIS', example='/lute utility whois example.com', help_group='lookup'),
            'github': LuteVerbSpec(name='github', summary='查询 GitHub 仓库信息', example='/lute utility github owner/repo', help_group='profile'),
            'qr': LuteVerbSpec(name='qr', summary='生成二维码', example='/lute utility qr https://example.com', help_group='media'),
            'news': LuteVerbSpec(name='news', summary='查看每日新闻', example='/lute utility news', help_group='media'),
        },
    ),
    'group': LuteDomainSpec(
        name='group',
        summary='本群管理',
        access='admin',
        visible_in_help=False,
        task_label='本群管理',
        task_summary='仅在当前群内查看成员、公告、历史与管理操作。跨群/私聊消息和文件操作不属于本分区，统一走 /lute qq message、/lute qq file。',
        examples=['/lute group info', '/lute group member list', '/lute group admin show'],
        verbs={
            'info': LuteVerbSpec(name='info', summary='查看当前群信息', example='/lute group info', access='admin'),
            'member': LuteVerbSpec(name='member', summary='查看或管理当前群成员', example='/lute group member list', access='admin'),
            'notice': LuteVerbSpec(name='notice', summary='发送或查看当前群公告', example='/lute group notice list', access='admin'),
            'history': LuteVerbSpec(name='history', summary='查看当前群历史消息', example='/lute group history', access='admin'),
            'honor': LuteVerbSpec(name='honor', summary='查看当前群荣誉信息', example='/lute group honor', access='admin'),
            'name': LuteVerbSpec(name='name', summary='修改当前群名称', example='/lute group name 新群名', access='admin'),
            'whole-ban': LuteVerbSpec(name='whole-ban', summary='开关全群禁言', example='/lute group whole-ban on', access='admin'),
            'sign': LuteVerbSpec(name='sign', summary='当前群打卡', example='/lute group sign', access='admin'),
            'at-all': LuteVerbSpec(name='at-all', summary='查看 bot 账号在当前群的 @全体剩余次数', example='/lute group at-all', access='admin'),
            'essence': LuteVerbSpec(name='essence', summary='管理当前群精华消息', example='/lute group essence list', access='admin'),
            'react': LuteVerbSpec(name='react', summary='管理当前群消息表情回应', example='/lute group react add 66', access='admin'),
            'read': LuteVerbSpec(name='read', summary='标记当前群消息已读', example='/lute group read', access='admin'),
            'poke': LuteVerbSpec(name='poke', summary='戳一戳当前群成员', example='/lute group poke <qq_id>', access='admin'),
            'recall': LuteVerbSpec(name='recall', summary='撤回消息', example='/lute group recall', access='admin'),
            'mute': LuteVerbSpec(name='mute', summary='禁言当前群成员', example='/lute group mute <qq_id> 600', access='admin'),
            'kick': LuteVerbSpec(name='kick', summary='移出当前群成员', example='/lute group kick <qq_id>', access='admin'),
            'admin': LuteVerbSpec(name='admin', summary='查看或调整本群 qqadmin 自动化配置', example='/lute group admin show', access='admin'),
        },
    ),
    'system': LuteDomainSpec(
        name='system',
        summary='运行状态与重载',
        access='admin',
        visible_in_help=False,
        task_label='系统状态',
        task_summary='查看 runtime、当前状态和重载配置。',
        examples=['/lute system status', '/lute system runtime show'],
        verbs={
            'show': LuteVerbSpec(name='show', summary='查看系统状态（status 别名）', example='/lute system show', access='admin'),
            'status': LuteVerbSpec(name='status', summary='查看系统状态', example='/lute system status', access='admin'),
            'runtime': LuteVerbSpec(name='runtime', summary='查看 runtime 配置', example='/lute system runtime show', access='admin'),
            'stats': LuteVerbSpec(name='stats', summary='查看 view/delivery 统计', example='/lute system stats show', access='admin'),
            'logs': LuteVerbSpec(name='logs', summary='查看最近 view 事件日志', example='/lute system logs', access='admin'),
            'reload': LuteVerbSpec(name='reload', summary='重载 QQ bot 配置', example='/lute system reload', access='admin'),
        },
    ),
    'config': LuteDomainSpec(
        name='config',
        summary='权限与配置变更',
        access='admin',
        visible_in_help=False,
        task_label='权限配置',
        task_summary='allowlist、group-user、LLM 授权与兼容开关。',
        examples=['/lute config allow user add 1003', '/lute config llm grant 1003'],
        verbs={
            'allow': LuteVerbSpec(name='allow', summary='修改 QQ allowlist', example='/lute config allow user add 1003', access='admin'),
            'llm': LuteVerbSpec(name='llm', summary='授予或撤销 LLM 权限', example='/lute config llm grant 1003', access='admin'),
            'feature': LuteVerbSpec(name='feature', summary='控制普通用户功能开关', example='/lute config feature list', access='admin'),
        },
    ),
    'service': LuteDomainSpec(
        name='service',
        summary='聚合服务集成中心',
        access='admin',
        visible_in_help=False,
        task_label='服务集成',
        task_summary='AstrBot 聚合 API 的目录、执行与 runtime。',
        examples=['/lute service list image --scope safe', '/lute service search 壁纸 --scope safe'],
        verbs={
            'list': LuteVerbSpec(name='list', summary='列出服务 API', example='/lute service list image --scope safe', access='admin'),
            'search': LuteVerbSpec(name='search', summary='搜索服务 API', example='/lute service search 壁纸 --type image --scope safe', access='admin'),
            'show': LuteVerbSpec(name='show', summary='查看服务 API 详情', example='/lute service show 艺术字 --scope safe', access='admin'),
            'status': LuteVerbSpec(name='status', summary='查看服务 API 详情（show 别名）', example='/lute service status 艺术字 --scope safe', access='admin'),
            'run': LuteVerbSpec(name='run', summary='执行指定服务 API', example='/lute service run 艺术字 hello', access='admin'),
            'match': LuteVerbSpec(name='match', summary='按文本匹配并执行服务 API', example='/lute service match 艺术字 hello', access='admin'),
            'runtime': LuteVerbSpec(name='runtime', summary='查看或控制服务 runtime', example='/lute service runtime show', access='admin'),
        },
    ),
    'comic': LuteDomainSpec(
        name='comic',
        summary='漫画与显式内容工具',
        access='admin',
        visible_in_help=False,
        task_label='漫画工具',
        task_summary='按平台接入漫画搜索、详情、排行与下载；当前仅接入 JM。',
        examples=['/lute comic jm search 关键词'],
        verbs={
            'jm': LuteVerbSpec(name='jm', summary='使用 JM 平台', example='/lute comic jm search 关键词', access='admin'),
        },
    ),
    'qq': LuteDomainSpec(
        name='qq',
        summary='跨会话 QQ 管理入口',
        access='admin',
        visible_in_help=False,
        task_label='QQ 平台管理',
        task_summary='处理跨会话 QQ 消息发送与转发、群文件浏览与管理。',
        examples=[
            '/lute qq message send private 1003 你好',
            '/lute qq message forward 777 --to-user 1003',
            '/lute qq message merge 777 888 --to-group 1004',
            '/lute qq file list 1004',
            '/lute qq file download https://example.com/a.zip',
        ],
        verbs={
            'message': LuteVerbSpec(name='message', summary='跨会话 QQ 消息发送与转发', example='/lute qq message send private 1003 你好', access='admin'),
            'file': LuteVerbSpec(name='file', summary='群文件浏览与管理', example='/lute qq file list 1004', access='admin'),
        },
    ),
}

_ROOT_COMMANDS: dict[str, LuteVerbSpec] = {
    'help': LuteVerbSpec(name='help', summary='查看总菜单或某个分区帮助', example='/lute help'),
    'menu': LuteVerbSpec(name='menu', summary='显示任务菜单', example='/lute menu'),
    'ping': LuteVerbSpec(name='ping', summary='快速检查 Lute 是否在线', example='/lute ping'),
    'status': LuteVerbSpec(name='status', summary='查看面向用户的精简运行状态', example='/lute status'),
    'show': LuteVerbSpec(name='show', summary='查看面向用户的精简运行状态（status 别名）', example='/lute show'),
}

_HELP_AREAS: tuple[dict[str, object], ...] = (
    {
        'title': '🤖 AI 助手',
        'features': (
            ('📊群分析', '智能群聊日常分析', '/lute analyze', 'user'),
            ('👤用户画像', '分析群友公开互动画像', '/lute profile @用户', 'user'),
            ('🧂锐评', '让露特来一句犀利点评', '/lute roast <内容>', 'user'),
            ('❓这是什么', '识别图片或解释陌生事物', '/lute what', 'user'),
            ('💬和露特对话', '直接和露特聊天', '/lute chat <内容>', 'user'),
        ),
    },
    {
        'title': '🛠 实用工具',
        'features': (
            ('🌦天气', '查询城市天气', '/lute weather 北京', 'user'),
            ('🗓节假日查询', '查询节假日和调休安排', '/lute holiday', 'user'),
            ('🎲随机数', '生成随机数或帮你做选择', '/lute random <最小值> <最大值>', 'user'),
            ('🔐加解密', '佛曰、熊曰等趣味加解密', '/lute crypto fo encode <文本>', 'user'),
            ('🔳二维码生成/解析', '生成或识别二维码', '/lute qr <文本或图片>', 'user'),
        ),
    },
    {
        'title': '🎮 娱乐休闲',
        'features': (
            ('🆓Epic', '查看本周 Epic 免费游戏', '/lute epic', 'user'),
            ('🍀今日运势', '看今天的运势签', '/lute luck', 'user'),
            ('🍗疯狂星期四', '生成 KFC 疯狂星期四文案', '/lute kfc', 'user'),
            ('🤒发病小作文', '生成发病文学小作文', '/lute rave <主题>', 'user'),
        ),
    },
    {
        'title': '🖼 多媒体',
        'features': (
            ('🎨图片生成', '生成或改图', '/lute image generate <描述>', 'user'),
            ('🔤图片转字符画', '把图片转成字符画', '/lute ascii <图片>', 'user'),
            ('🖼搜图', '提供 URL 或图片查来源', '/lute image search <图片URL或图片>', 'user'),
            ('🎞视频截图', '从视频中截取画面', '/lute video shot <视频>', 'user'),
        ),
    },
    {
        'title': '🔍 信息查询',
        'features': (
            ('📺B站总结', '总结 B 站视频', '/lute bili read <BV号或链接>', 'user'),
            ('🛡CVE', '查询漏洞编号和风险信息', '/lute cve <cve编号>', 'user'),
            ('🌐WHOIS', '查询域名注册信息', '/lute whois <域名>', 'user'),
        ),
    },
    {
        'title': '📦 资源搜索',
        'features': (
            ('🎨Pixiv', '搜索 Pixiv 作品', '/lute pixiv', 'user'),
            ('📺番剧', '搜索番剧和条目', '/lute bangumi', 'user'),
            ('🎬影视', '搜索影视资源', '/lute torrent', 'user'),
            ('📄论文', '搜索论文资料', '/lute paper', 'user'),
            ('📚电子书', '搜索电子书资源', '/lute book', 'user'),
            ('🐙项目', '搜索 GitHub 项目', '/lute github', 'user'),
            ('🎭表情包', '搜索表情包/GIF', '/lute gif', 'user'),
            ('📖漫画', '搜索漫画资源', '/lute comic jm', 'admin'),
        ),
    },
    {
        'title': '🔔 订阅通知',
        'features': (
            ('📰RSS订阅', '查看和拉取 RSS 订阅', '/lute feed', 'user'),
            ('🔥知乎热搜', '查看知乎热搜', '/lute 知乎热搜', 'user'),
            ('📺B站热搜', '查看 B 站热搜', '/lute B站热搜', 'user'),
            ('🤖AI早报', '生成高质量 AI 每日早报 PDF', '/lute AI早报', 'user'),
            ('🐙GitHub趋势', '查看 GitHub Trending', '/lute github趋势', 'user'),
        ),
    },
    {
        'title': '👤 个人中心',
        'features': (
            ('🪪个人资料', '查看个人资料和状态', '/lute me', 'user'),
            ('✅每日签到', '领取每日互动奖励', '/lute sign', 'user'),
            ('📅签到记录', '查看历史签到记录', '/lute sign history', 'user'),
            ('🎒背包', '查看和露特互动获得的道具', '/lute bag', 'user'),
            ('💗好感度', '查看和露特的好感度', '/lute affection', 'user'),
        ),
    },
    {
        'title': '🛡 群管功能',
        'access': 'admin',
        'features': (
            ('📋群信息', '查看当前群信息', '/lute group info', 'admin'),
            ('👥群成员', '查看或管理当前群成员', '/lute group member', 'admin'),
            ('📢群公告', '发送或查看当前群公告', '/lute group notice', 'admin'),
            ('🕘群历史', '查看当前群历史消息', '/lute group history', 'admin'),
            ('🤖群管自动化', '查看或调整本群自动管理配置', '/lute group admin', 'admin'),
            ('💬QQ消息', '跨会话 QQ 消息发送与转发', '/lute qq message', 'admin'),
            ('📁群文件', '群文件浏览与管理', '/lute qq file', 'admin'),
        ),
    },
    {
        'title': '⚙️ 系统设置',
        'access': 'admin',
        'features': (
            ('🖥系统状态', '查看系统状态', '/lute system status', 'admin'),
            ('🔄系统重载', '重载 QQ bot 配置', '/lute system reload', 'admin'),
            ('🤖模型配置', '查看或调整模型相关配置', '/lute config model', 'admin'),
            ('🔀功能开关', '控制开启哪些功能板块', '/lute config feature', 'admin'),
            ('🏷版本信息', '查看 Lute 和运行环境版本', '/lute system version', 'admin'),
            ('📜日志查看', '查看最近运行日志', '/lute system logs', 'admin'),
            ('🔧权限配置', '管理用户、群组和命令访问范围', '/lute config allow', 'admin'),
            ('🧠LLM授权', '管理用户 LLM 权限', '/lute config llm', 'admin'),
        ),
    },
)


def build_default_lute_root_commands(cli: QQBotCLIConfig | None = None) -> dict[str, LuteVerbSpec]:
    del cli
    return {name: deepcopy(spec) for name, spec in _ROOT_COMMANDS.items()}


def _format_example(example: str, cli: QQBotCLIConfig) -> str:
    return example.replace('/lute', f'/{cli.root_command}', 1) if example.startswith('/lute') else example


def _effective_domain_spec(base: LuteDomainSpec, cli: QQBotCLIConfig) -> LuteDomainSpec | None:
    override = cli.domains.get(base.name)
    spec = deepcopy(base)
    if override is None:
        return spec
    if not override.enabled:
        return None

    if base.access != 'admin':
        spec.access = override.access
        spec.visible_in_help = override.visible_in_help
    return spec


def parse_lute_invocation(text: str, cli: QQBotCLIConfig | None = None) -> LuteInvocation:
    cli = cli or QQBotCLIConfig()
    parts = shlex.split(text)
    if not parts:
        raise ValueError('Empty /lute command')

    root = parts[0].lstrip('/').lower()
    expected_roots = {cli.root_command.lower(), *(alias.lower() for alias in cli.aliases)}
    if root not in expected_roots:
        raise ValueError(f'Not a /{cli.root_command} command: {text}')

    command = ''
    domain = parts[1].lower() if len(parts) > 1 else ''
    verb = parts[2].lower() if len(parts) > 2 and not parts[2].startswith('--') else ''
    raw_tail = parts[3:] if verb else parts[2:]

    if domain in _ROOT_COMMANDS:
        command = domain
        domain = parts[2].lower() if len(parts) > 2 and not parts[2].startswith('--') else ''
        verb = ''
        raw_tail = parts[3:] if domain else parts[2:]

    args: list[str] = []
    options: dict[str, str | bool] = {}
    i = 0
    while i < len(raw_tail):
        token = raw_tail[i]
        if token.startswith('--'):
            key = token[2:].replace('-', '_')
            if i + 1 < len(raw_tail) and not raw_tail[i + 1].startswith('--'):
                options[key] = raw_tail[i + 1]
                i += 2
                continue
            options[key] = True
            i += 1
            continue
        args.append(token)
        i += 1

    return LuteInvocation(
        root=cli.root_command,
        domain=domain,
        verb=verb,
        command=command,
        args=args,
        options=options,
        raw_text=text,
    )


def build_default_lute_registry(cli: QQBotCLIConfig | None = None) -> dict[str, LuteDomainSpec]:
    cli = cli or QQBotCLIConfig()
    registry: dict[str, LuteDomainSpec] = {}
    for name, spec in _DEFAULT_DOMAINS.items():
        effective = _effective_domain_spec(spec, cli)
        if effective is not None:
            registry[name] = effective
    return registry


def _can_view_lute_domain(
    spec: LuteDomainSpec,
    *,
    is_admin: bool,
    policy: QQBotPolicy | None,
    user_id: str | None,
) -> bool:
    if policy is not None:
        return policy.can_view_lute_domain_help(spec, user_id)
    return is_admin or (spec.access != 'admin' and spec.visible_in_help)


def _help_area_rows(
    area: dict[str, object],
    *,
    registry: dict[str, LuteDomainSpec],
    root_commands: dict[str, LuteVerbSpec],
    cli: QQBotCLIConfig,
    is_admin: bool,
    policy: QQBotPolicy | None,
    user_id: str | None,
) -> list[tuple[str, str]]:
    del registry, root_commands, policy, user_id
    rows: list[tuple[str, str]] = []
    for raw_feature in area.get('features', ()):
        if not isinstance(raw_feature, tuple) or len(raw_feature) < 4:
            continue
        name = str(raw_feature[0]).strip()
        description = str(raw_feature[1]).strip()
        syntax = str(raw_feature[2]).strip()
        access = str(raw_feature[3]).strip().lower()
        if access == 'admin' and not is_admin:
            continue
        if name and syntax:
            rows.append((f'{name}: {description}', _format_example(syntax, cli)))
    return rows


def _split_menu_icon(label: str) -> tuple[str, str]:
    label = str(label).strip()
    if not label:
        return '', ''
    if len(label) >= 2 and label[1] == '\ufe0f':
        return label[:2], label[2:].strip() or label
    first = label[0]
    if not first.isascii() and len(label) > 1:
        return first, label[1:].strip() or label
    return '', label


def _menu_command_payload(label: str, syntax: str) -> dict[str, str]:
    raw_name, description = (label.split(':', 1) + [''])[:2] if ':' in label else (label, '')
    icon, name = _split_menu_icon(raw_name)
    return {
        'icon': icon,
        'name': name.strip() or raw_name.strip(),
        'syntax': str(syntax).strip(),
        'description': description.strip(),
    }


def build_lute_root_help_view(
    registry: dict[str, LuteDomainSpec],
    *,
    is_admin: bool,
    cli: QQBotCLIConfig | None = None,
    policy: QQBotPolicy | None = None,
    user_id: str | None = None,
) -> ViewSpec:
    """Build the default image-capable root Lute help/menu view.

    The text renderer remains available through ``render_lute_help``. This view is
    used only by the runtime send path so the stable root menu can be rendered to
    a cached JPEG and re-rendered automatically when the content hash changes.
    """

    cli = cli or QQBotCLIConfig()
    root_commands = build_default_lute_root_commands(cli)
    fallback = _render_lute_root_help(registry, is_admin=is_admin, cli=cli, policy=policy, user_id=user_id)
    modules: list[dict[str, object]] = [
        {
            'title': '🧭 基础说明',
            'commands': [
                {
                    'icon': '📖',
                    'name': '打开菜单',
                    'syntax': f'/{cli.root_command} / /{cli.root_command} menu / /{cli.root_command} help',
                    'description': '这三个入口都会打开同一份露特菜单',
                },
                {
                    'icon': '🔎',
                    'name': '二级菜单',
                    'syntax': f'/{cli.root_command} help <模块>',
                    'description': '查看某个模块的详细命令',
                },
            ],
        }
    ]
    for area in _HELP_AREAS:
        if str(area.get('access') or 'user').lower() == 'admin' and not is_admin:
            continue
        rows = _help_area_rows(
            area,
            registry=registry,
            root_commands=root_commands,
            cli=cli,
            is_admin=is_admin,
            policy=policy,
            user_id=user_id,
        )
        if rows:
            modules.append(
                {
                    'title': str(area['title']),
                    'commands': [_menu_command_payload(label, syntax) for label, syntax in rows],
                }
            )
    help_menu_config = getattr(getattr(getattr(policy, 'config', None), 'view', None), 'help_menu', None)
    if help_menu_config is None:
        help_menu_payload = {
            'quality': 70,
            'scale_factor': 1.5,
            'min_width': 1024,
            'min_height': 720,
            'max_width': 1480,
            'max_height': 4300,
        }
    else:
        help_menu_payload = {
            'quality': help_menu_config.quality,
            'scale_factor': help_menu_config.scale_factor,
            'min_width': help_menu_config.min_width,
            'min_height': help_menu_config.min_height,
            'max_width': help_menu_config.max_width,
            'max_height': help_menu_config.max_height,
        }
    return ViewSpec(
        kind='image',
        template='help.usage-card',
        data={
            'variant': 'help-menu',
            'title': 'Lute Help',
            'subtitle': f'按功能区查看命令；详细帮助用 /{cli.root_command} help <模块>',
            'avatar_text': '露特',
            'badge_text': f'{sum(len(module["commands"]) for module in modules)} 条命令',
            'help_menu': help_menu_payload,
            'modules': modules,
        },
        fallback_text=fallback,
        cache_policy=None,
        telemetry_tags={'domain': cli.root_command, 'verb': 'help'},
    )


def _description_for_domain_example(spec: LuteDomainSpec, example: str) -> str:
    try:
        parts = shlex.split(example)
    except ValueError:
        parts = example.split()
    if len(parts) >= 3:
        verb = str(parts[2]).strip().lower()
        verb_spec = spec.verbs.get(verb)
        if verb_spec is not None:
            return verb_spec.summary
    return spec.task_label or spec.summary


def _render_lute_root_help(
    registry: dict[str, LuteDomainSpec],
    *,
    is_admin: bool,
    cli: QQBotCLIConfig,
    policy: QQBotPolicy | None,
    user_id: str | None,
) -> str:
    root_commands = build_default_lute_root_commands(cli)
    lines = [
        'Lute Help',
        f'用法: /{cli.root_command} <功能|命令> [参数] [--选项]',
        f'详细帮助: /{cli.root_command} help <模块>',
        '',
        '🧭 基础说明',
        f'/{cli.root_command}、/{cli.root_command} menu、/{cli.root_command} help 都会打开同一份露特菜单。',
        f'- /{cli.root_command} / /{cli.root_command} menu / /{cli.root_command} help: 打开露特菜单',
        f'- /{cli.root_command} help <模块>: 查看某个模块的二级菜单',
        '',
    ]
    for area in _HELP_AREAS:
        if str(area.get('access') or 'user').lower() == 'admin' and not is_admin:
            continue
        title = str(area['title'])
        lines.append(title)
        rows = _help_area_rows(
            area,
            registry=registry,
            root_commands=root_commands,
            cli=cli,
            is_admin=is_admin,
            policy=policy,
            user_id=user_id,
        )
        if rows:
            for label, syntax in rows:
                lines.append(label)
                lines.append(f'- {syntax}')
        else:
            lines.append('  （暂未开放）')
        lines.append('')
    return '\n'.join(line for line in lines).strip()


def _visible_domain_verbs(
    spec: LuteDomainSpec,
    *,
    is_admin: bool,
    policy: QQBotPolicy | None,
    user_id: str | None,
) -> list[tuple[str, LuteVerbSpec]]:
    visible_verbs: list[tuple[str, LuteVerbSpec]] = []
    for verb_name, verb_spec in spec.verbs.items():
        if policy is not None:
            if not policy.can_access_lute_verb(spec, verb_spec, user_id):
                continue
        elif not is_admin and (spec.access == 'admin' or verb_spec.access == 'admin'):
            continue
        visible_verbs.append((verb_name, verb_spec))
    return visible_verbs


def _render_lute_domain_help(
    spec: LuteDomainSpec,
    *,
    is_admin: bool,
    cli: QQBotCLIConfig,
    policy: QQBotPolicy | None,
    user_id: str | None,
) -> str:
    del cli
    lines = [spec.summary]
    if spec.task_summary:
        lines.append(spec.task_summary)
    elif spec.task_label:
        lines.append(spec.task_label)

    visible_verbs = _visible_domain_verbs(spec, is_admin=is_admin, policy=policy, user_id=user_id)
    if visible_verbs:
        lines.append('')
        lines.append('常用命令：')
        if spec.name == 'utility':
            utility_group_order = ('lookup', 'profile', 'media', 'fun')
            grouped: dict[str, list[tuple[str, LuteVerbSpec]]] = {group: [] for group in utility_group_order}
            for verb_name, verb_spec in visible_verbs:
                group = verb_spec.help_group or 'lookup'
                grouped.setdefault(group, []).append((verb_name, verb_spec))
            for group in utility_group_order:
                group_items = grouped.get(group, [])
                if not group_items:
                    continue
                lines.append(f'{group}:')
                for verb_name, verb_spec in group_items:
                    lines.append(f'- {verb_name}: {verb_spec.summary}')
        else:
            for verb_name, verb_spec in visible_verbs:
                lines.append(f'- {verb_name}: {verb_spec.summary}')
    return '\n'.join(lines)


def render_lute_help(
    registry: dict[str, LuteDomainSpec],
    is_admin: bool,
    domain: str | None = None,
    cli: QQBotCLIConfig | None = None,
    policy: QQBotPolicy | None = None,
    user_id: str | None = None,
) -> str:
    cli = cli or QQBotCLIConfig()

    if domain:
        spec = registry.get(domain.lower())
        if spec is None:
            return f'Unknown /{cli.root_command} section: {domain}\nTry: /{cli.root_command} help'
        if policy is not None:
            if not policy.can_view_lute_domain_help(spec, user_id):
                return f'Unknown /{cli.root_command} section: {domain}\nTry: /{cli.root_command} help'
        elif not is_admin and (spec.access == 'admin' or not spec.visible_in_help):
            return f'Unknown /{cli.root_command} section: {domain}\nTry: /{cli.root_command} help'
        return _render_lute_domain_help(spec, is_admin=is_admin, cli=cli, policy=policy, user_id=user_id)

    return _render_lute_root_help(registry, is_admin=is_admin, cli=cli, policy=policy, user_id=user_id)
