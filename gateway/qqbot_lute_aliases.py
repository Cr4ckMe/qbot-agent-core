from __future__ import annotations

from dataclasses import dataclass, replace

from gateway.qqbot_lute_types import LuteInvocation


@dataclass(frozen=True)
class LuteCommandAlias:
    """User-friendly Lute command mapped to a canonical QQBot Lute command."""

    name: str
    source_domain: str
    source_verb: str = ''
    source_command: str = ''
    source_args: tuple[str, ...] = ()
    allow_args: bool = False
    allow_options: bool = False
    target_domain: str = ''
    target_verb: str = ''
    target_args: tuple[str, ...] = ()
    target_options: tuple[tuple[str, str | bool], ...] = ()
    summary: str = ''


def _alias_key(name: str) -> str:
    return str(name).strip().casefold()


_HIGH_LEVEL_ALIASES: tuple[LuteCommandAlias, ...] = (
    LuteCommandAlias(
        name='知乎热搜',
        source_domain='知乎热搜',
        allow_options=True,
        target_domain='feed',
        target_verb='fetch',
        target_args=('6',),
        summary='查看知乎热搜',
    ),
    LuteCommandAlias(
        name='知乎热搜刷新',
        source_domain='知乎热搜',
        source_verb='refresh',
        target_domain='feed',
        target_verb='fetch',
        target_args=('6',),
        target_options=(('refresh', True),),
        summary='刷新并查看知乎热搜',
    ),
    LuteCommandAlias(
        name='B站热搜',
        source_domain='B站热搜',
        allow_options=True,
        target_domain='feed',
        target_verb='fetch',
        target_args=('7',),
        summary='查看 B 站热搜',
    ),
    LuteCommandAlias(
        name='B站热搜刷新',
        source_domain='B站热搜',
        source_verb='refresh',
        target_domain='feed',
        target_verb='fetch',
        target_args=('7',),
        target_options=(('refresh', True),),
        summary='刷新并查看 B 站热搜',
    ),
    LuteCommandAlias(
        name='AI日报',
        source_domain='AI日报',
        allow_options=True,
        target_domain='ai-news',
        target_verb='daily',
        summary='生成 AI 每日早报',
    ),
    LuteCommandAlias(
        name='AI早报',
        source_domain='AI早报',
        allow_options=True,
        target_domain='ai-news',
        target_verb='daily',
        summary='生成 AI 每日早报',
    ),
    LuteCommandAlias(
        name='GitHub Trending',
        source_domain='github',
        source_verb='trending',
        allow_options=True,
        target_domain='feed',
        target_verb='fetch',
        target_args=('GitHub Trending',),
        summary='查看 GitHub Trending',
    ),
    LuteCommandAlias(
        name='GitHub Trending Refresh',
        source_domain='github',
        source_verb='trending',
        source_args=('refresh',),
        target_domain='feed',
        target_verb='fetch',
        target_args=('GitHub Trending',),
        target_options=(('refresh', True),),
        summary='刷新并查看 GitHub Trending',
    ),
    LuteCommandAlias(
        name='GitHub趋势',
        source_domain='github趋势',
        allow_options=True,
        target_domain='feed',
        target_verb='fetch',
        target_args=('GitHub Trending',),
        summary='查看 GitHub Trending',
    ),
    LuteCommandAlias(
        name='GitHub趋势 AI Tools',
        source_domain='github趋势',
        source_verb='ai-tools',
        allow_options=True,
        target_domain='feed',
        target_verb='fetch',
        target_args=('GitHub Trending',),
        target_options=(('filter', 'ai-tools'),),
        summary='查看 GitHub Trending (AI Tools 分类)',
    ),
    LuteCommandAlias(
        name='GitHub趋势 AI Tool',
        source_domain='github趋势',
        source_verb='ai-tool',
        allow_options=True,
        target_domain='feed',
        target_verb='fetch',
        target_args=('GitHub Trending',),
        target_options=(('filter', 'ai-tools'),),
        summary='查看 GitHub Trending (AI Tools 分类)',
    ),
    LuteCommandAlias(
        name='GitHub趋势 AI Agents',
        source_domain='github趋势',
        source_verb='ai-agents',
        allow_options=True,
        target_domain='feed',
        target_verb='fetch',
        target_args=('GitHub Trending',),
        target_options=(('filter', 'ai-agents'),),
        summary='查看 GitHub Trending (AI Agents 分类)',
    ),
    LuteCommandAlias(
        name='GitHub趋势 AI Agent',
        source_domain='github趋势',
        source_verb='ai-agent',
        allow_options=True,
        target_domain='feed',
        target_verb='fetch',
        target_args=('GitHub Trending',),
        target_options=(('filter', 'ai-agents'),),
        summary='查看 GitHub Trending (AI Agents 分类)',
    ),
    LuteCommandAlias(
        name='GitHub Trending AI Tools',
        source_domain='github',
        source_verb='trending',
        source_args=('ai-tools',),
        allow_options=True,
        target_domain='feed',
        target_verb='fetch',
        target_args=('GitHub Trending',),
        target_options=(('filter', 'ai-tools'),),
        summary='查看 GitHub Trending (AI Tools 分类)',
    ),
    LuteCommandAlias(
        name='GitHub Trending AI Tool',
        source_domain='github',
        source_verb='trending',
        source_args=('ai-tool',),
        allow_options=True,
        target_domain='feed',
        target_verb='fetch',
        target_args=('GitHub Trending',),
        target_options=(('filter', 'ai-tools'),),
        summary='查看 GitHub Trending (AI Tools 分类)',
    ),
    LuteCommandAlias(
        name='GitHub Trending AI Agents',
        source_domain='github',
        source_verb='trending',
        source_args=('ai-agents',),
        allow_options=True,
        target_domain='feed',
        target_verb='fetch',
        target_args=('GitHub Trending',),
        target_options=(('filter', 'ai-agents'),),
        summary='查看 GitHub Trending (AI Agents 分类)',
    ),
    LuteCommandAlias(
        name='GitHub Trending AI Agent',
        source_domain='github',
        source_verb='trending',
        source_args=('ai-agent',),
        allow_options=True,
        target_domain='feed',
        target_verb='fetch',
        target_args=('GitHub Trending',),
        target_options=(('filter', 'ai-agents'),),
        summary='查看 GitHub Trending (AI Agents 分类)',
    ),
)


def _matches_alias(invocation: LuteInvocation, alias: LuteCommandAlias) -> bool:
    if _alias_key(invocation.command) != _alias_key(alias.source_command):
        return False
    if _alias_key(invocation.domain) != _alias_key(alias.source_domain):
        return False
    if _alias_key(invocation.verb) != _alias_key(alias.source_verb):
        return False
    if alias.source_args:
        if tuple(_alias_key(arg) for arg in invocation.args) != tuple(_alias_key(arg) for arg in alias.source_args):
            return False
    elif invocation.args and not alias.allow_args:
        return False
    if invocation.options and not alias.allow_options:
        return False
    return True


def resolve_lute_command_alias(invocation: LuteInvocation) -> LuteInvocation | None:
    """Resolve a high-level Lute alias to its canonical QQBot Lute invocation.

    This supports both bare unknown-domain shortcuts such as ``/lute 知乎热搜`` and
    more readable high-level command shapes such as ``/lute github trending`` or
    Chinese-style aliases such as ``/lute GitHub趋势``. Dispatch-time resolution
    preserves the central registry/policy path by remapping only the invocation
    remapping only the invocation fields, after which normal domain/verb
    authorization and handlers run unchanged.
    """

    for alias in _HIGH_LEVEL_ALIASES:
        if not _matches_alias(invocation, alias):
            continue
        target_options = dict(invocation.options)
        target_options.update(dict(alias.target_options))
        return replace(
            invocation,
            domain=alias.target_domain,
            verb=alias.target_verb,
            command='',
            args=list(alias.target_args),
            options=target_options,
        )
    return None
