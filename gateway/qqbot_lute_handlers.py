from __future__ import annotations

import ipaddress
import json
import os
import re
import socket
import sqlite3
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from gateway.qqbot_config import QQBotCLIConfig, QQBotViewConfig
from gateway.qqbot_lute_aliases import resolve_lute_command_alias
from gateway.qqbot_lute_backends import call_script_backend, call_tool_backend
from gateway.qqbot_lute_registry import build_lute_root_help_view, render_lute_help
from gateway.qqbot_lute_types import LuteDomainSpec, LuteInvocation, LuteResponse, LuteVerbSpec
from gateway.qqbot_lute_usage import LuteUsageEntry, render_single_usage, render_usage_list
from gateway.qqbot_policy import QQBotPolicy
from gateway.view.types import ViewCachePolicy, ViewSpec


SKILLS_ROOT = Path.home() / ".hermes" / "skills"
SKILLS_DATA_ROOT = Path.home() / ".hermes" / "skills-data"
EPICGAME_SCRIPT = SKILLS_ROOT / "gaming" / "epicgame" / "scripts" / "epicgame.py"
BILIREAD_SCRIPT = SKILLS_ROOT / "media" / "biliread" / "scripts" / "biliread.py"
FEED_WATCHER_SCRIPT = SKILLS_ROOT / "media" / "feed-watcher" / "scripts" / "feed_watcher.py"
FEED_WATCHER_DB = SKILLS_DATA_ROOT / "feed-watcher" / "feed-watcher.db"
AI_DAILY_REPORT_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "ai_daily_report.py"
UAPIPRO_TOOLBOX_SCRIPT = SKILLS_ROOT / "media" / "uapipro-toolbox" / "scripts" / "uapi_toolbox.py"
TORRENT_ANALYZE_SCRIPT = SKILLS_ROOT / "media" / "torrent-analyze" / "scripts" / "torrent_analyze.py"
MOVIE_SEARCH_SCRIPT = SKILLS_ROOT / "media" / "movie-search" / "scripts" / "movie_search.py"
REVERSE_SEARCH_SCRIPT = SKILLS_ROOT / "media" / "imgexploration" / "scripts" / "reverse_search.py"
BANGUMI_SCRIPT = SKILLS_ROOT / "media" / "bangumi" / "scripts" / "bangumi.py"
PIXIV_SCRIPT = SKILLS_ROOT / "media" / "pixiv" / "scripts" / "pixiv.py"
ZLIBRARY_SCRIPT = SKILLS_ROOT / "media" / "zlibrary" / "scripts" / "zlibrary.py"
JM_SCRIPT = SKILLS_ROOT / "media" / "jm-mcp" / "scripts" / "jm_mcp.py"
ASTRBOT_APIS_SCRIPT = SKILLS_ROOT / "media" / "astrbot-apis" / "scripts" / "astrbot_apis.py"
ASTRBOT_APIS_CONFIG = SKILLS_DATA_ROOT / "astrbot-apis" / "config.json"


def _qqbot_script_python() -> str:
    configured = str(os.getenv('QQBOT_SCRIPT_PYTHON') or '').strip()
    if configured:
        return str(Path(configured).expanduser())
    return sys.executable


@dataclass(frozen=True)
class LuteDispatchResult:
    text: str | None = None
    admin_command_text: str | None = None
    response: LuteResponse | None = None


def _lute_root_help_response(
    registry: dict[str, LuteDomainSpec],
    *,
    is_admin: bool,
    cli: QQBotCLIConfig,
    policy: QQBotPolicy,
    user_id: str | None,
) -> LuteResponse:
    view_config = getattr(getattr(policy, 'config', None), 'view', QQBotViewConfig())
    fallback = render_lute_help(registry, is_admin=is_admin, cli=cli, policy=policy, user_id=user_id)
    if not getattr(view_config, 'enabled', True) or getattr(view_config, 'help_menu_format', 'image') == 'text':
        return LuteResponse(text=fallback)
    view = build_lute_root_help_view(registry, is_admin=is_admin, cli=cli, policy=policy, user_id=user_id)
    return LuteResponse(text='', view=view)


def _render_lute_root_help_view(
    registry: dict[str, LuteDomainSpec],
    *,
    is_admin: bool,
    cli: QQBotCLIConfig,
    policy: QQBotPolicy,
    user_id: str | None,
) -> str:
    """Legacy compatibility helper for callers that still expect plain strings."""
    response = _lute_root_help_response(registry, is_admin=is_admin, cli=cli, policy=policy, user_id=user_id)
    return _render_backend_response(response, view_config=getattr(policy.config, 'view', QQBotViewConfig()))


async def dispatch_lute_invocation(
    invocation: LuteInvocation,
    *,
    policy: QQBotPolicy,
    registry: dict[str, LuteDomainSpec],
    root_commands: dict[str, LuteVerbSpec],
    cli: QQBotCLIConfig | None = None,
    user_id: str | None = None,
    stats_provider: Callable[[LuteInvocation], str] | None = None,
    logs_provider: Callable[[LuteInvocation], str] | None = None,
) -> LuteDispatchResult:
    cli = cli or QQBotCLIConfig()
    is_admin = policy.is_admin(user_id)

    if invocation.command in {"help", "menu"}:
        requested_domain = invocation.domain or None
        if requested_domain:
            return LuteDispatchResult(
                text=render_lute_help(
                    registry,
                    is_admin=is_admin,
                    domain=requested_domain,
                    cli=cli,
                    policy=policy,
                    user_id=user_id,
                )
            )
        return LuteDispatchResult(response=_lute_root_help_response(registry, is_admin=is_admin, cli=cli, policy=policy, user_id=user_id))

    if invocation.command in root_commands:
        if invocation.command == "ping":
            return LuteDispatchResult(text="Lute is online.")
        if invocation.command in {"status", "show"}:
            return LuteDispatchResult(text=_render_core_status(policy, user_id, cli))

    if not invocation.domain:
        return LuteDispatchResult(response=_lute_root_help_response(registry, is_admin=is_admin, cli=cli, policy=policy, user_id=user_id))

    domain_spec = registry.get(invocation.domain)
    if domain_spec is None:
        alias_invocation = resolve_lute_command_alias(invocation)
        if alias_invocation is not None:
            invocation = alias_invocation
            domain_spec = registry.get(invocation.domain)
    if domain_spec is None:
        return LuteDispatchResult(text=f"Unknown /{cli.root_command} section: {invocation.domain}\nTry: /{cli.root_command} help")

    if not invocation.verb:
        if domain_spec.name == "epic":
            verb_name = "weekly"
        elif domain_spec.name == "ai-news":
            verb_name = "daily"
        else:
            return LuteDispatchResult(
                text=render_lute_help(
                    registry,
                    is_admin=is_admin,
                    domain=invocation.domain,
                    cli=cli,
                    policy=policy,
                    user_id=user_id,
                )
            )
    else:
        verb_name = invocation.verb

    verb_spec = domain_spec.verbs.get(verb_name)
    if verb_spec is None:
        if domain_spec.name == 'comic':
            return LuteDispatchResult(text=f'Usage: /{cli.root_command} comic <platform> <verb> ...\nSupported platforms: jm')
        return LuteDispatchResult(
            text=f"Unknown /{cli.root_command} verb: {verb_name}\nTry: /{cli.root_command} help {invocation.domain}"
        )

    if not policy.can_access_lute_verb(domain_spec, verb_spec, user_id):
        return LuteDispatchResult(text="Permission denied: this /lute section is admin-only.")

    if domain_spec.name == "system":
        return _dispatch_system_admin(invocation, verb_name, stats_provider=stats_provider, logs_provider=logs_provider)
    if domain_spec.name == "config":
        return _dispatch_config_admin(invocation, verb_name)

    response = await _dispatch_read_only_domain(
        domain_name=domain_spec.name,
        verb_name=verb_name,
        invocation=invocation,
    )
    return LuteDispatchResult(response=response)


async def _dispatch_read_only_domain(
    *,
    domain_name: str,
    verb_name: str,
    invocation: LuteInvocation,
) -> LuteResponse:
    if domain_name == "epic" and verb_name == "weekly":
        return await call_script_backend(
            [_qqbot_script_python(), str(EPICGAME_SCRIPT), "--json"],
            allowed_executables={str(EPICGAME_SCRIPT)},
        )

    if domain_name == "bangumi":
        return await _dispatch_bangumi(invocation, verb_name)

    if domain_name == "bili" and verb_name == "read":
        query = " ".join(invocation.args).strip()
        if not query:
            return LuteResponse(text=render_single_usage(f'/{invocation.root} bili read <BV号或链接>'))
        return await call_script_backend(
            [_qqbot_script_python(), str(BILIREAD_SCRIPT), query],
            allowed_executables={str(BILIREAD_SCRIPT)},
        )

    if domain_name == "feed":
        return await _dispatch_feed(invocation, verb_name)

    if domain_name == "ai-news":
        return await _dispatch_ai_news(invocation, verb_name)

    if domain_name == "image" and verb_name == "search":
        return await _dispatch_image_search(invocation)

    if domain_name == "pixiv":
        return await _dispatch_pixiv(invocation, verb_name)

    if domain_name == "torrent":
        return await _dispatch_torrent(invocation, verb_name)

    if domain_name == "book":
        return await _dispatch_book(invocation, verb_name)

    if domain_name == "utility":
        return await _dispatch_utility(invocation, verb_name)

    if domain_name == "service":
        return await _dispatch_service(invocation, verb_name)

    if domain_name == "group":
        return await _dispatch_group(invocation, verb_name)

    if domain_name == "qq":
        return await _dispatch_qq(invocation, verb_name)

    if domain_name == "comic":
        return await _dispatch_comic(invocation, verb_name)

    return LuteResponse(text=f"/{invocation.root} {domain_name} {verb_name} is not implemented yet.")


async def _dispatch_bangumi(invocation: LuteInvocation, verb_name: str) -> LuteResponse:
    if verb_name == "today":
        return await call_script_backend(
            [_qqbot_script_python(), str(BANGUMI_SCRIPT), "today", "--json"],
            allowed_executables={str(BANGUMI_SCRIPT)},
        )

    if verb_name == "search":
        usage = _bangumi_search_usage(invocation.root)
        keyword = " ".join(invocation.args).strip()
        has_filters = any(key in invocation.options for key in ("year", "tag", "meta_tag", "meta_tags"))
        if not keyword and not has_filters:
            return LuteResponse(text=usage)
        command = [_qqbot_script_python(), str(BANGUMI_SCRIPT), "search"]
        if keyword:
            command.extend(str(part) for part in invocation.args)
        command.extend(["--subject-type", "2", "--limit", "5"])
        if "year" in invocation.options:
            try:
                year = int(str(invocation.options["year"]))
            except (TypeError, ValueError):
                return LuteResponse(text=usage)
            command.extend(["--year", str(year)])
        if "tag" in invocation.options:
            command.extend(["--tag", str(invocation.options["tag"])])
        if "meta_tag" in invocation.options:
            command.extend(["--meta-tag", str(invocation.options["meta_tag"])])
        if "meta_tags" in invocation.options:
            command.extend(["--meta-tags", str(invocation.options["meta_tags"])])
        command.append("--json")
        response = await call_script_backend(command, allowed_executables={str(BANGUMI_SCRIPT)})
        if invocation.options.get('image'):
            ids: list[str] = []
            if isinstance(response.payload, dict):
                items = response.payload.get('items') or []
                if isinstance(items, list):
                    ids = [str(item.get('id') or item.get('subject_id')) for item in items if isinstance(item, dict) and (item.get('id') or item.get('subject_id'))]
            if not ids:
                ids = [m.group(1) for m in re.finditer(r'ID:\s*(\d+)', response.text or '')]
            if ids:
                script_result = await call_script_backend(
                    [_qqbot_script_python(), str(BANGUMI_SCRIPT), 'subject-long-card', *ids],
                    allowed_executables={str(BANGUMI_SCRIPT)},
                )
                return _response_with_existing_image_view(script_result, domain='bangumi', verb='search', template='bangumi.subject-card')
        return response

    if verb_name == "subject":
        subject_ids: list[int] = []
        for token in invocation.args:
            try:
                subject_ids.append(int(str(token)))
            except (TypeError, ValueError):
                return LuteResponse(text=_bangumi_subject_usage(invocation.root))
        if not subject_ids:
            return LuteResponse(text=_bangumi_subject_usage(invocation.root))
        if invocation.options.get('image'):
            script_result = await call_script_backend(
                [_qqbot_script_python(), str(BANGUMI_SCRIPT), 'subject-long-card', *[str(x) for x in subject_ids]],
                allowed_executables={str(BANGUMI_SCRIPT)},
            )
            return _response_with_existing_image_view(script_result, domain='bangumi', verb='subject', template='bangumi.subject-card')
        return await call_script_backend(
            [_qqbot_script_python(), str(BANGUMI_SCRIPT), 'subject-text', *[str(x) for x in subject_ids], '--json'],
            allowed_executables={str(BANGUMI_SCRIPT)},
        )

    return LuteResponse(text=f"Unknown /{invocation.root} verb: {verb_name}\nTry: /{invocation.root} help bangumi")


_URL_RE = re.compile(r'https?://\S+|\b(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}(?:/\S*)?')
_EXPLICIT_URL_RE = re.compile(r'https?://\S+')
_FEED_DISPLAY_TZ = timezone(timedelta(hours=8))
_WEEKDAY_NAMES = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']


def _feed_safe_text(value: object, *, max_len: int = 240) -> str:
    text = _EXPLICIT_URL_RE.sub('', str(value or ''))
    text = re.sub(r'\s+', ' ', text).strip()
    if len(text) > max_len:
        return text[: max_len - 1].rstrip() + '…'
    return text


def _feed_safe_multiline_text(value: object, *, max_len_per_line: int = 220, max_lines: int = 6) -> str:
    raw_lines = str(value or '').splitlines() or [str(value or '')]
    cleaned_lines: list[str] = []
    for raw_line in raw_lines:
        line = _URL_RE.sub('', str(raw_line or ''))
        line = re.sub(r'\s+', ' ', line).strip()
        if not line:
            continue
        if len(line) > max_len_per_line:
            line = line[: max_len_per_line - 1].rstrip() + '…'
        cleaned_lines.append(line)
        if len(cleaned_lines) >= max_lines:
            break
    return '\n'.join(cleaned_lines)


def _feed_canonical_text(value: object) -> str:
    text = _URL_RE.sub('', str(value or ''))
    text = re.sub(r'\s+', ' ', text).strip()
    return re.sub(r'[^\w\u4e00-\u9fff]+', '', text).casefold()


def _safe_feed_fallback(text: str) -> str:
    lines = []
    for raw in str(text or '').splitlines():
        line = _URL_RE.sub('', raw)
        line = _feed_safe_text(line, max_len=500)
        if line:
            lines.append(line)
    return '\n'.join(lines).strip()


def _feed_now() -> datetime:
    return datetime.now(_FEED_DISPLAY_TZ)


def _format_feed_published(value: object) -> str:
    raw = str(value or '').strip()
    if not raw:
        return ''
    dt: datetime | None = None
    try:
        from email.utils import parsedate_to_datetime

        dt = parsedate_to_datetime(raw)
    except Exception:
        dt = None
    if dt is None:
        iso_candidate = raw.replace('Z', '+00:00') if raw.endswith('Z') else raw
        try:
            dt = datetime.fromisoformat(iso_candidate)
        except Exception:
            dt = None
    if dt is None:
        return _feed_safe_text(raw, max_len=80)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    local_dt = dt.astimezone(_FEED_DISPLAY_TZ)
    now = _feed_now()
    delta_days = (now.date() - local_dt.date()).days
    if delta_days == 0:
        return f'今天 {local_dt:%H:%M}'
    if delta_days == 1:
        return f'昨天 {local_dt:%H:%M}'
    if 2 <= delta_days <= 6:
        return f'{_WEEKDAY_NAMES[local_dt.weekday()]} {local_dt:%H:%M}'
    if delta_days == -1:
        return f'明天 {local_dt:%H:%M}'
    if local_dt.year != now.year:
        return f'{local_dt.year}年{local_dt.month}月{local_dt.day}日 {local_dt:%H:%M}'
    return f'{local_dt.month}月{local_dt.day}日 {local_dt:%H:%M}'


_FEED_INTERNAL_HOSTS = {
    'localhost',
    '127.0.0.1',
    '::1',
    '0.0.0.0',
    'rsshub',
}

_FEED_CGNAT_NETWORK = ipaddress.ip_network('100.64.0.0/10')


def _is_public_feed_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
    except Exception:
        return False
    if parsed.scheme not in {'http', 'https'}:
        return False
    host = str(parsed.hostname or '').strip().lower()
    if not host:
        return False
    if host in _FEED_INTERNAL_HOSTS:
        return False
    if host.endswith('.localhost') or host.endswith('.local') or host.endswith('.internal') or host.endswith('.docker.internal'):
        return False

    def _is_public_ip(host_value: str) -> bool:
        try:
            ip = ipaddress.ip_address(host_value)
        except ValueError:
            return False
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            return False
        if isinstance(ip, ipaddress.IPv4Address) and ip in _FEED_CGNAT_NETWORK:
            return False
        return True

    if _is_public_ip(host):
        return True
    try:
        resolved = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == 'https' else 80), proto=socket.IPPROTO_TCP)
    except Exception:
        return False
    seen_public = False
    for family, _type, _proto, _canonname, sockaddr in resolved:
        if family not in {socket.AF_INET, socket.AF_INET6}:
            continue
        ip_text = str(sockaddr[0] or '').strip()
        if not ip_text:
            continue
        if not _is_public_ip(ip_text):
            return False
        seen_public = True
    return seen_public


def _feed_entry_public_url(item: dict[str, object]) -> str:
    for key in ('link', 'url'):
        value = str(item.get(key) or '').strip()
        if _is_public_feed_url(value):
            return value
    return ''


def _repoinsider_summary_from_metadata(metadata: dict[str, object]) -> str:
    source_bits = [str(metadata.get('source') or 'RepoInsider').strip()]
    for key in ('badge', 'status'):
        value = str(metadata.get(key) or '').strip()
        if value:
            source_bits.append(value)
    lines = [f"来源: {' · '.join(part for part in source_bits if part)}"]
    field_labels = (
        ('category', '分类'),
        ('language', '语言'),
    )
    for key, label in field_labels:
        value = str(metadata.get(key) or '').strip()
        if value:
            lines.append(f'{label}: {value}')
    metrics: list[str] = []
    if metadata.get('stars'):
        metrics.append(f"Stars {str(metadata['stars']).strip()}")
    if metadata.get('pulse'):
        metrics.append(f"Pulse {str(metadata['pulse']).strip()}")
    if metadata.get('velocity'):
        metrics.append(f"增速 {str(metadata['velocity']).strip()}")
    if metrics:
        lines.append(f"指标: {' · '.join(metrics)}")
    detail_labels = (
        ('description', '简介'),
        ('why', 'Why Trending'),
        ('target_audience', 'Target Audience'),
        ('similar_projects', 'Similar Projects'),
        ('best_for', 'Best for'),
    )
    for key, label in detail_labels:
        value = str(metadata.get(key) or '').strip()
        if value:
            lines.append(f'{label}: {value}')
    return '\n'.join(lines).strip()


def _feed_summary_text(title: object, summary: object) -> str:
    safe_summary = _feed_safe_multiline_text(summary, max_len_per_line=260, max_lines=10)
    if not safe_summary:
        return ''
    canonical_title = _feed_canonical_text(title)
    canonical_summary = _feed_canonical_text(summary)
    if canonical_title and canonical_summary == canonical_title:
        return ''
    return safe_summary


def _feed_forward_target(invocation: LuteInvocation) -> tuple[str, str] | None:
    chat_type = str(invocation.current_chat_type or '').strip().lower()
    target_id = str(invocation.current_chat_id or '').strip()
    if not target_id:
        return None
    if chat_type == 'group':
        return 'group', target_id
    if chat_type in {'dm', 'private'}:
        return 'private', target_id
    return None


def _feed_forward_node(text: str) -> dict[str, object]:
    return {
        'type': 'node',
        'data': {
            'nickname': 'Hermes',
            'content': [{'type': 'text', 'data': {'text': str(text).strip()}}],
        },
    }


def _build_feed_list_forward_messages(payload: dict[str, object], *, root: str) -> tuple[list[dict[str, object]], str]:
    subscriptions = [item for item in list(payload.get('subscriptions') or []) if isinstance(item, dict)]
    lines = []
    nodes: list[dict[str, object]] = [_feed_forward_node(f'订阅列表\n共 {len(subscriptions)} 项\n可用 /{root} feed fetch <编号> 查看最新内容')]
    for item in subscriptions:
        sub_id = _feed_safe_text(item.get('id') or '?', max_len=24)
        title = _feed_safe_text(item.get('title') or '未命名订阅', max_len=80)
        status = '' if item.get('enabled', True) else '\n状态: 已停用'
        text = f'[{sub_id}] {title}{status}'.strip()
        nodes.append(_feed_forward_node(text))
        lines.append(text)
    fallback = '\n'.join(lines).strip() or '暂无订阅'
    return nodes, fallback


def _build_feed_fetch_forward_messages(payload: dict[str, object], *, root: str) -> tuple[list[dict[str, object]], str]:
    subscription = payload.get('subscription') if isinstance(payload.get('subscription'), dict) else {}
    entries = [item for item in list(payload.get('entries') or []) if isinstance(item, dict)][:15]
    title = _feed_safe_text(subscription.get('title') or '订阅内容', max_len=80)
    sub_id = _feed_safe_text(subscription.get('id') or '?', max_len=24)
    nodes: list[dict[str, object]] = [_feed_forward_node(f'[{sub_id}] {title}\n最近 {len(entries)} 条\n可用 /{root} feed scan 检查更新')]
    lines = [f'[{sub_id}] {title}']
    for idx, item in enumerate(entries, 1):
        entry_title = _feed_safe_text(item.get('title') or '未命名条目', max_len=160)
        published = _format_feed_published(item.get('published') or '')
        metadata = item.get('metadata') if isinstance(item.get('metadata'), dict) else {}
        summary_source = item.get('summary') or ''
        if metadata.get('source_kind') == 'repoinsider' and any(
            str(metadata.get(key) or '').strip()
            for key in ('badge', 'status', 'stars', 'pulse', 'velocity', 'description', 'why', 'target_audience', 'similar_projects', 'best_for')
        ):
            summary_source = _repoinsider_summary_from_metadata(metadata)
        summary = _feed_summary_text(item.get('title') or '未命名条目', summary_source)
        url = _feed_entry_public_url(item)
        entry_lines = [f'{idx}. {entry_title}']
        if published:
            entry_lines.append(f'时间: {published}')
        if summary:
            summary_lines = [line for line in str(summary).splitlines() if line.strip()]
            if summary_lines:
                if len(summary_lines) == 1:
                    entry_lines.append(f'摘要: {summary_lines[0]}')
                else:
                    entry_lines.extend(summary_lines)
        if url:
            entry_lines.append(f'链接: {url}')
        text = '\n'.join(entry_lines).strip()
        nodes.append(_feed_forward_node(text))
        lines.append(text)
    fallback = '\n\n'.join(lines).strip() or '暂无内容'
    return nodes, fallback


async def _feed_forward_response(payload: object, *, invocation: LuteInvocation, fallback_text: str, root: str, verb: str) -> LuteResponse:
    safe_fallback = _safe_feed_fallback(fallback_text)
    if not isinstance(payload, dict):
        return LuteResponse(text=safe_fallback)
    kind = str(payload.get('kind') or '').strip().lower()
    if kind == 'feed_list':
        messages, generated_fallback = _build_feed_list_forward_messages(payload, root=root)
    elif kind == 'feed_fetch':
        messages, generated_fallback = _build_feed_fetch_forward_messages(payload, root=root)
    else:
        return LuteResponse(text=safe_fallback)
    fallback = generated_fallback or safe_fallback
    target = _feed_forward_target(invocation)
    if target is None:
        return LuteResponse(text=fallback)
    chat_type, target_id = target
    tool_response = await call_tool_backend(
        'qq_forward_messages',
        {'chat_type': chat_type, 'target_id': target_id, 'messages': messages},
        allowed_tool_names={'qq_forward_messages'},
    )
    payload_data = tool_response.payload if isinstance(tool_response.payload, dict) else {}
    if payload_data.get('success'):
        return LuteResponse(text='', telemetry_events=list(tool_response.telemetry_events or []))
    return LuteResponse(text=fallback, telemetry_events=list(tool_response.telemetry_events or []))


async def _dispatch_feed(
    invocation: LuteInvocation,
    verb_name: str,
) -> LuteResponse:
    if verb_name == "list":
        response = await call_script_backend(
            [_qqbot_script_python(), str(FEED_WATCHER_SCRIPT), "list", "--json"],
            allowed_executables={str(FEED_WATCHER_SCRIPT)},
        )
        if response.payload is not None:
            return await _feed_forward_response(response.payload, invocation=invocation, fallback_text=response.text, root=invocation.root, verb=verb_name)
        return LuteResponse(text=_safe_feed_fallback(response.text))

    if verb_name == "fetch":
        fetch_args = list(invocation.args)
        refresh_requested = bool(invocation.options.get("refresh"))
        if fetch_args and str(fetch_args[-1]).strip().casefold() == 'refresh':
            refresh_requested = True
            fetch_args = fetch_args[:-1]
        raw_target = " ".join(fetch_args).strip()
        fetch_invocation = replace(invocation, args=fetch_args)
        sub_id = _parse_required_int_arg(fetch_invocation, usage="Usage: /lute feed fetch <subscription_id>")
        if sub_id is None and raw_target:
            sub_id = _resolve_feed_subscription_target(raw_target)
        if sub_id is None:
            return LuteResponse(text=_feed_fetch_usage(invocation.root))
        command = [_qqbot_script_python(), str(FEED_WATCHER_SCRIPT), "fetch", "--id", str(sub_id), "--limit", "15", "--timeout", "12"]
        if refresh_requested:
            command.append("--refresh")
        filter_value = invocation.options.get("filter")
        if filter_value:
            command.extend(["--filter", str(filter_value)])
        command.append("--json")
        response = await call_script_backend(
            command,
            allowed_executables={str(FEED_WATCHER_SCRIPT)},
            timeout_sec=25,
        )
        if response.payload is not None:
            return await _feed_forward_response(response.payload, invocation=invocation, fallback_text=response.text, root=invocation.root, verb=verb_name)
        return LuteResponse(text=_safe_feed_fallback(response.text))

    if verb_name == "scan":
        return await call_script_backend(
            [_qqbot_script_python(), str(FEED_WATCHER_SCRIPT), "scan", "--limit", "5", "--timeout", "8", "--deadline", "38"],
            allowed_executables={str(FEED_WATCHER_SCRIPT)},
            timeout_sec=55,
        )

    if verb_name == "add":
        if not invocation.args:
            return LuteResponse(text=_feed_add_usage(invocation.root))
        command = [_qqbot_script_python(), str(FEED_WATCHER_SCRIPT), "add", "--url", str(invocation.args[0]).strip()]
        if "title" in invocation.options:
            command.extend(["--title", str(invocation.options["title"])])
        if "interval" in invocation.options:
            command.extend(["--interval", str(invocation.options["interval"])])
        if "tags" in invocation.options:
            command.extend(["--tags", str(invocation.options["tags"])])
        return await call_script_backend(command, allowed_executables={str(FEED_WATCHER_SCRIPT)})

    if verb_name == "remove":
        sub_id = _parse_required_int_arg(invocation, usage=_feed_remove_usage(invocation.root))
        if sub_id is None:
            return LuteResponse(text=_feed_remove_usage(invocation.root))
        return await call_script_backend(
            [_qqbot_script_python(), str(FEED_WATCHER_SCRIPT), "remove", "--id", str(sub_id)],
            allowed_executables={str(FEED_WATCHER_SCRIPT)},
        )

    return LuteResponse(text=f"Unknown /{invocation.root} verb: {verb_name}\nTry: /{invocation.root} help feed")


def _resolve_feed_subscription_target(raw_target: str) -> int | None:
    target = raw_target.strip()
    if not target:
        return None
    if not FEED_WATCHER_DB.exists():
        return None

    try:
        conn = sqlite3.connect(FEED_WATCHER_DB)
        conn.row_factory = sqlite3.Row
    except sqlite3.Error:
        return None

    normalized = target.casefold()
    try:
        rows = list(conn.execute("SELECT id, title, tags FROM subscriptions ORDER BY id ASC"))
    except sqlite3.Error:
        conn.close()
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass

    for row in rows:
        title = str(row["title"] or "").strip()
        if title and title.casefold() == normalized:
            return int(row["id"])

    for row in rows:
        title = str(row["title"] or "").strip()
        tags = str(row["tags"] or "").strip()
        if title and normalized in title.casefold():
            return int(row["id"])
        if tags:
            tag_items = [item.strip().casefold() for item in tags.split(",") if item.strip()]
            if normalized in tag_items:
                return int(row["id"])
    return None


async def _dispatch_ai_news(invocation: LuteInvocation, verb_name: str) -> LuteResponse:
    if verb_name not in {'daily', 'today'}:
        return LuteResponse(text=f"Unknown /{invocation.root} verb: {verb_name}\nTry: /{invocation.root} help ai-news")
    return await call_script_backend(
        [_qqbot_script_python(), str(AI_DAILY_REPORT_SCRIPT), 'generate', '--format', 'pdf', '--json'],
        allowed_executables={str(AI_DAILY_REPORT_SCRIPT)},
        timeout_sec=180,
    )


async def _dispatch_image_search(invocation: LuteInvocation) -> LuteResponse:
    source = " ".join(invocation.args).strip()
    if not source:
        return LuteResponse(text=render_single_usage(f'/{invocation.root} image search <图片URL或本地路径>'))

    if source.startswith("base64://") or source.startswith("data:image"):
        return LuteResponse(text=render_single_usage(f'/{invocation.root} image search <图片URL或本地路径>'))

    source_flag = "--image-url"
    if not source.startswith(("http://", "https://")):
        source_flag = "--image-path"

    return await call_script_backend(
        [_qqbot_script_python(), str(REVERSE_SEARCH_SCRIPT), source_flag, source, "--json"],
        allowed_executables={str(REVERSE_SEARCH_SCRIPT)},
    )


async def _dispatch_pixiv(invocation: LuteInvocation, verb_name: str) -> LuteResponse:
    if verb_name == "search":
        keyword = " ".join(invocation.args).strip()
        if not keyword:
            return LuteResponse(text=_pixiv_search_usage(invocation.root))
        return await call_script_backend(
            [_qqbot_script_python(), str(PIXIV_SCRIPT), "search", "--keyword", keyword, "--json"],
            allowed_executables={str(PIXIV_SCRIPT)},
        )

    if verb_name == "rank":
        mode = str(invocation.args[0]).strip() if invocation.args else "day"
        return await call_script_backend(
            [_qqbot_script_python(), str(PIXIV_SCRIPT), "rank", "--type", mode, "--json"],
            allowed_executables={str(PIXIV_SCRIPT)},
        )

    if verb_name == "illust":
        illust_id = _parse_required_int_arg(invocation, usage=_pixiv_illust_usage(invocation.root))
        if illust_id is None:
            return LuteResponse(text=_pixiv_illust_usage(invocation.root))
        return await call_script_backend(
            [_qqbot_script_python(), str(PIXIV_SCRIPT), "illust", "--id", str(illust_id), "--json"],
            allowed_executables={str(PIXIV_SCRIPT)},
        )

    if verb_name == "related":
        illust_id = _parse_required_int_arg(invocation, usage=_pixiv_related_usage(invocation.root))
        if illust_id is None:
            return LuteResponse(text=_pixiv_related_usage(invocation.root))
        return await call_script_backend(
            [_qqbot_script_python(), str(PIXIV_SCRIPT), "related", "--id", str(illust_id), "--limit", "5", "--json"],
            allowed_executables={str(PIXIV_SCRIPT)},
        )

    if verb_name == "download":
        illust_id = _parse_required_int_arg(invocation, usage=_pixiv_download_usage(invocation.root))
        if illust_id is None:
            return LuteResponse(text=_pixiv_download_usage(invocation.root))
        return await call_script_backend(
            [_qqbot_script_python(), str(PIXIV_SCRIPT), "download", "--id", str(illust_id), "--json"],
            allowed_executables={str(PIXIV_SCRIPT)},
        )

    return LuteResponse(text=f"Unknown /{invocation.root} verb: {verb_name}\nTry: /{invocation.root} help pixiv")


async def _dispatch_torrent(invocation: LuteInvocation, verb_name: str) -> LuteResponse:
    query = " ".join(invocation.args).strip()
    if verb_name == "search":
        if not query:
            return LuteResponse(text=_torrent_search_usage(invocation.root))
        command = [
            _qqbot_script_python(),
            str(MOVIE_SEARCH_SCRIPT),
            "search",
            query,
            "--limit",
            "5",
            "--availability",
            "available",
        ]
        if "season" in invocation.options:
            try:
                season = int(str(invocation.options["season"]))
            except (TypeError, ValueError):
                return LuteResponse(text=_torrent_search_usage(invocation.root))
            command.extend(["--season", str(season)])
        if "episode" in invocation.options:
            try:
                episode = int(str(invocation.options["episode"]))
            except (TypeError, ValueError):
                return LuteResponse(text=_torrent_search_usage(invocation.root))
            command.extend(["--episode", str(episode)])
        if "quality" in invocation.options:
            command.extend(["--quality", str(invocation.options["quality"])])
        if "language" in invocation.options:
            command.extend(["--language", str(invocation.options["language"])])
        command.extend(["--compact", "--json"])
        return await call_script_backend(command, allowed_executables={str(MOVIE_SEARCH_SCRIPT)})

    if verb_name == "stream":
        content_id = _parse_required_int_arg(invocation, usage=_torrent_stream_usage(invocation.root))
        if content_id is None:
            return LuteResponse(text=_torrent_stream_usage(invocation.root))
        country = str(invocation.options.get("country") or "US").strip().upper() or "US"
        return await call_script_backend(
            [_qqbot_script_python(), str(MOVIE_SEARCH_SCRIPT), "stream", str(content_id), "--country", country, "--json"],
            allowed_executables={str(MOVIE_SEARCH_SCRIPT)},
        )

    if verb_name == "fallback":
        if not query:
            return LuteResponse(text=_torrent_fallback_usage(invocation.root))
        return await call_script_backend(
            [_qqbot_script_python(), str(MOVIE_SEARCH_SCRIPT), "fallback", query, "--json"],
            allowed_executables={str(MOVIE_SEARCH_SCRIPT)},
        )

    if verb_name == "analyze":
        if not query:
            return LuteResponse(text=_torrent_analyze_usage(invocation.root))
        return await call_script_backend(
            [_qqbot_script_python(), str(TORRENT_ANALYZE_SCRIPT), "analyze", query, "--json"],
            allowed_executables={str(TORRENT_ANALYZE_SCRIPT)},
        )

    return LuteResponse(text=f"Unknown /{invocation.root} verb: {verb_name}\nTry: /{invocation.root} help torrent")


async def _dispatch_book(invocation: LuteInvocation, verb_name: str) -> LuteResponse:
    if verb_name == "search":
        query = " ".join(invocation.args).strip()
        if not query:
            return LuteResponse(text=_book_search_usage(invocation.root))
        return await call_script_backend(
            [_qqbot_script_python(), str(ZLIBRARY_SCRIPT), 'search', query, '--count', '5', '--json'],
            allowed_executables={str(ZLIBRARY_SCRIPT)},
        )

    if verb_name == "recent":
        return await call_script_backend(
            [_qqbot_script_python(), str(ZLIBRARY_SCRIPT), 'recent', '--count', '5', '--json'],
            allowed_executables={str(ZLIBRARY_SCRIPT)},
        )

    if verb_name == "limits":
        return await call_script_backend(
            [_qqbot_script_python(), str(ZLIBRARY_SCRIPT), 'limits', '--json'],
            allowed_executables={str(ZLIBRARY_SCRIPT)},
        )

    if verb_name == "metadata":
        if not invocation.args:
            return LuteResponse(text=_book_metadata_usage(invocation.root))
        book_id = str(invocation.args[0]).strip()
        book_hash = str(invocation.options.get('hash') or '').strip()
        if not book_id or not book_hash:
            return LuteResponse(text=_book_metadata_usage(invocation.root))
        return await call_script_backend(
            [_qqbot_script_python(), str(ZLIBRARY_SCRIPT), 'metadata', '--book-id', book_id, '--book-hash', book_hash, '--json'],
            allowed_executables={str(ZLIBRARY_SCRIPT)},
        )

    return LuteResponse(text=f"Unknown /{invocation.root} verb: {verb_name}\nTry: /{invocation.root} help book")


async def _dispatch_utility(invocation: LuteInvocation, verb_name: str) -> LuteResponse:
    if verb_name == "weather":
        city = " ".join(invocation.args).strip()
        if not city:
            return LuteResponse(text="Usage: /lute utility weather <城市>")
        return await call_script_backend(
            [_qqbot_script_python(), str(UAPIPRO_TOOLBOX_SCRIPT), "--json", "weather", city],
            allowed_executables={str(UAPIPRO_TOOLBOX_SCRIPT)},
        )

    if verb_name == "whois":
        domain = " ".join(invocation.args).strip()
        if not domain:
            return LuteResponse(text="Usage: /lute utility whois <域名>")
        return await call_script_backend(
            [_qqbot_script_python(), str(UAPIPRO_TOOLBOX_SCRIPT), "--json", "whois", domain],
            allowed_executables={str(UAPIPRO_TOOLBOX_SCRIPT)},
        )

    if verb_name == "qr":
        content = " ".join(invocation.args).strip()
        if not content:
            return LuteResponse(text="Usage: /lute utility qr <内容>")
        return await call_script_backend(
            [_qqbot_script_python(), str(UAPIPRO_TOOLBOX_SCRIPT), "--json", "qrcode", content],
            allowed_executables={str(UAPIPRO_TOOLBOX_SCRIPT)},
        )

    if verb_name == "github":
        repo = " ".join(invocation.args).strip()
        if not repo:
            return LuteResponse(text="Usage: /lute utility github <owner/repo>")
        return await call_script_backend(
            [_qqbot_script_python(), str(UAPIPRO_TOOLBOX_SCRIPT), "--json", "github", repo],
            allowed_executables={str(UAPIPRO_TOOLBOX_SCRIPT)},
        )

    if verb_name == "news":
        if invocation.args:
            return LuteResponse(text="Usage: /lute utility news")
        return await call_script_backend(
            [_qqbot_script_python(), str(UAPIPRO_TOOLBOX_SCRIPT), "--json", "news"],
            allowed_executables={str(UAPIPRO_TOOLBOX_SCRIPT)},
        )

    return LuteResponse(text=f"Unknown /{invocation.root} verb: {verb_name}\nTry: /{invocation.root} help utility")


def _load_astrbot_service_config() -> dict[str, object]:
    defaults: dict[str, object] = {
        'dashboard_base_url': 'http://127.0.0.1:4141',
        'default_timeout_sec': 20,
        'sensitive_keywords': [],
    }
    if not ASTRBOT_APIS_CONFIG.exists():
        return defaults
    try:
        payload = json.loads(ASTRBOT_APIS_CONFIG.read_text(encoding='utf-8'))
    except Exception:
        return defaults
    if not isinstance(payload, dict):
        return defaults
    merged = dict(defaults)
    merged.update(payload)
    return merged


def _service_sensitive_keywords(config: dict[str, object]) -> list[str]:
    raw = config.get('sensitive_keywords')
    if not isinstance(raw, list):
        return []
    return [str(item).strip().casefold() for item in raw if str(item).strip()]


def _service_scope(invocation: LuteInvocation, *, default: str = 'safe') -> str:
    scope = str(invocation.options.get('scope') or default).strip().lower()
    return scope if scope in {'safe', 'sensitive', 'all'} else default


def _service_item_haystack(item: dict[str, object]) -> str:
    keywords = item.get('keywords') or []
    keyword_text = ' '.join(str(x) for x in keywords) if isinstance(keywords, list) else ''
    return '\n'.join(
        [
            str(item.get('name') or ''),
            str(item.get('url') or ''),
            str(item.get('site') or ''),
            keyword_text,
        ]
    ).casefold()


def _is_sensitive_service_item(item: dict[str, object], sensitive_keywords: list[str]) -> bool:
    haystack = _service_item_haystack(item)
    return any(keyword in haystack for keyword in sensitive_keywords)


def _filter_service_items(
    items: list[dict[str, object]],
    *,
    api_type: str | None = None,
    scope: str = 'safe',
    sensitive_keywords: list[str],
) -> list[dict[str, object]]:
    filtered: list[dict[str, object]] = []
    wanted_type = (api_type or '').strip().lower()
    for item in items:
        item_type = str(item.get('type') or '').strip().lower()
        if wanted_type and item_type != wanted_type:
            continue
        is_sensitive = _is_sensitive_service_item(item, sensitive_keywords)
        if scope == 'safe' and is_sensitive:
            continue
        if scope == 'sensitive' and not is_sensitive:
            continue
        filtered.append(item)
    return filtered


def _render_service_item_line(item: dict[str, object], *, sensitive_keywords: list[str]) -> str:
    name = str(item.get('name') or '').strip()
    api_type = str(item.get('type') or 'text').strip()
    site = str(item.get('site') or '-').strip() or '-'
    enabled = item.get('enabled', True)
    scope = 'sensitive' if _is_sensitive_service_item(item, sensitive_keywords) else 'safe'
    return f'- {name} [{api_type}] site={site} enabled={enabled} scope={scope}'


def _load_service_catalog() -> tuple[list[dict[str, object]], list[str]]:
    config = _load_astrbot_service_config()
    base_url = str(config.get('dashboard_base_url') or 'http://127.0.0.1:4141').rstrip('/')
    timeout = int(config.get('default_timeout_sec') or 20)
    req = urllib.request.Request(f'{base_url}/api/pool', headers={'User-Agent': 'Hermes lute-service/0.1'})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode('utf-8', errors='replace'))
    if not isinstance(payload, dict):
        return [], _service_sensitive_keywords(config)
    data = payload.get('data') or {}
    items = data.get('apis') if isinstance(data, dict) else []
    if not isinstance(items, list):
        items = []
    return [item for item in items if isinstance(item, dict)], _service_sensitive_keywords(config)


async def _dispatch_service(invocation: LuteInvocation, verb_name: str) -> LuteResponse:
    if verb_name == 'runtime':
        action = str(invocation.args[0]).strip().lower() if invocation.args else 'status'
        subcommand = {
            'show': ['runtime-status', '--json'],
            'status': ['runtime-status', '--json'],
            'start': ['start-runtime'],
            'stop': ['stop-runtime'],
            'restart': ['restart-runtime'],
        }.get(action)
        if subcommand is None:
            return LuteResponse(text='Usage: /lute service runtime show|status|start|stop|restart')
        return await call_script_backend(
            [_qqbot_script_python(), str(ASTRBOT_APIS_SCRIPT), *subcommand],
            allowed_executables={str(ASTRBOT_APIS_SCRIPT)},
        )

    if verb_name in {'list', 'search', 'show', 'run', 'match'}:
        try:
            catalog, sensitive_keywords = _load_service_catalog()
        except (OSError, ValueError, urllib.error.URLError) as exc:
            return LuteResponse(text=f'Service catalog unavailable: {exc}')
    else:
        catalog, sensitive_keywords = [], []

    if verb_name == 'list':
        api_type = str(invocation.args[0]).strip().lower() if invocation.args else str(invocation.options.get('type') or '').strip().lower() or None
        scope = _service_scope(invocation, default='safe')
        items = _filter_service_items(catalog, api_type=api_type, scope=scope, sensitive_keywords=sensitive_keywords)
        lines = [f'count={len(items)}']
        lines.extend(_render_service_item_line(item, sensitive_keywords=sensitive_keywords) for item in items[:10])
        return LuteResponse(text='\n'.join(lines))

    if verb_name == 'search':
        query = ' '.join(invocation.args).strip()
        if not query:
            return LuteResponse(text='Usage: /lute service search <关键词> [--type image] [--scope safe|sensitive|all]')
        api_type = str(invocation.options.get('type') or '').strip().lower() or None
        scope = _service_scope(invocation, default='safe')
        items = _filter_service_items(catalog, api_type=api_type, scope=scope, sensitive_keywords=sensitive_keywords)
        matches = [item for item in items if query.casefold() in _service_item_haystack(item)]
        lines = [f'count={len(matches)}']
        lines.extend(_render_service_item_line(item, sensitive_keywords=sensitive_keywords) for item in matches[:10])
        return LuteResponse(text='\n'.join(lines))

    if verb_name in {'show', 'status'}:
        name = ' '.join(invocation.args).strip()
        if not name:
            return LuteResponse(text='Usage: /lute service show|status <名称> [--scope safe|sensitive|all]')
        scope = _service_scope(invocation, default='safe')
        target = next((item for item in catalog if str(item.get('name') or '').strip() == name), None)
        if target is None:
            return LuteResponse(text=f'NOT_FOUND {name}')
        if _is_sensitive_service_item(target, sensitive_keywords) and scope == 'safe':
            return LuteResponse(text='Refusing to show a sensitive service entry without --scope sensitive')
        return LuteResponse(text=json.dumps(target, ensure_ascii=False, indent=2))

    if verb_name == 'run':
        name = str(invocation.args[0]).strip() if invocation.args else ''
        if not name:
            return LuteResponse(text='Usage: /lute service run <名称> [参数...] [--scope safe|sensitive|all]')
        scope = _service_scope(invocation, default='safe')
        target = next((item for item in catalog if str(item.get('name') or '').strip() == name), None)
        if target is None:
            return LuteResponse(text=f'NOT_FOUND {name}')
        if _is_sensitive_service_item(target, sensitive_keywords) and scope == 'safe':
            return LuteResponse(text='Refusing to run a sensitive service entry without --scope sensitive')
        return await call_script_backend(
            [_qqbot_script_python(), str(ASTRBOT_APIS_SCRIPT), 'run-api', name, *[str(arg) for arg in invocation.args[1:]], '--json'],
            allowed_executables={str(ASTRBOT_APIS_SCRIPT)},
        )

    if verb_name == 'match':
        text = ' '.join(invocation.args).strip()
        if not text:
            return LuteResponse(text='Usage: /lute service match <文本...> [--scope safe|sensitive|all]')
        scope = _service_scope(invocation, default='safe')
        matches = [item for item in catalog if text.casefold() in _service_item_haystack(item)]
        if scope == 'safe' and any(_is_sensitive_service_item(item, sensitive_keywords) for item in matches):
            return LuteResponse(text='Refusing to run a sensitive service entry without --scope sensitive')
        return await call_script_backend(
            [_qqbot_script_python(), str(ASTRBOT_APIS_SCRIPT), 'match-api', *[str(arg) for arg in invocation.args], '--json'],
            allowed_executables={str(ASTRBOT_APIS_SCRIPT)},
        )

    return LuteResponse(text=f'Unknown /{invocation.root} verb: {verb_name}\nTry: /{invocation.root} help service')


async def _dispatch_qq(invocation: LuteInvocation, verb_name: str) -> LuteResponse:
    if verb_name not in {'message', 'file'}:
        return LuteResponse(text=f'Unknown /{invocation.root} verb: {verb_name}\nTry: /{invocation.root} help qq')

    usage_send = render_usage_list(
        [
            LuteUsageEntry(f'/{invocation.root} qq message send private <qq_id> <text>', '给指定 QQ 私聊发送消息'),
            LuteUsageEntry(f'/{invocation.root} qq message send group <group_id> <text>', '给指定 QQ 群发送消息'),
        ]
    )
    usage_forward = render_usage_list(
        [
            LuteUsageEntry(f'/{invocation.root} qq message forward <message_id> --to-user <qq_id>', '转发消息到指定 QQ 用户'),
            LuteUsageEntry(f'/{invocation.root} qq message forward <message_id> --to-group <group_id>', '转发消息到指定 QQ 群'),
        ]
    )
    usage_merge = render_usage_list(
        [
            LuteUsageEntry(f'/{invocation.root} qq message merge <message_id> [more_ids...] --to-user <qq_id>', '合并转发到指定 QQ 用户'),
            LuteUsageEntry(f'/{invocation.root} qq message merge <message_id> [more_ids...] --to-group <group_id>', '合并转发到指定 QQ 群'),
        ]
    )
    usage_file = render_usage_list(
        [
            LuteUsageEntry(f'/{invocation.root} qq file list <group_id>', '查看群根目录文件'),
            LuteUsageEntry(f'/{invocation.root} qq file all <group_id>', '查看群全部文件'),
            LuteUsageEntry(f'/{invocation.root} qq file detail <group_id> <file_id>', '查看群文件详情'),
            LuteUsageEntry(f'/{invocation.root} qq file url <group_id> <file_id>', '获取群文件下载地址'),
            LuteUsageEntry(f'/{invocation.root} qq file mkdir <group_id> <folder_name>', '创建群文件目录'),
            LuteUsageEntry(f'/{invocation.root} qq file delete <group_id> <file_id>', '删除群文件'),
            LuteUsageEntry(f'/{invocation.root} qq file download <url>', '下载远程文件到本地'),
        ]
    )

    def _resolve_target() -> tuple[str, str] | None:
        to_user = str(invocation.options.get('to_user') or '').strip()
        to_group = str(invocation.options.get('to_group') or '').strip()
        if bool(to_user) == bool(to_group):
            return None
        if to_user:
            return ('private', to_user)
        return ('group', to_group)

    action = str(invocation.args[0]).strip().lower() if invocation.args else ''
    if action == 'send':
        if len(invocation.args) < 4:
            return LuteResponse(text=usage_send)
        chat_type = str(invocation.args[1]).strip().lower()
        if chat_type not in {'private', 'group'}:
            return LuteResponse(text=usage_send)
        target_id = str(invocation.args[2]).strip()
        message = ' '.join(invocation.args[3:]).strip()
        if not target_id or not message:
            return LuteResponse(text=usage_send)
        return await call_tool_backend(
            'qq_send_message',
            {'chat_type': chat_type, 'target_id': target_id, 'message': message},
            allowed_tool_names={'qq_send_message'},
        )

    if action == 'forward':
        target = _resolve_target()
        if len(invocation.args) != 2 or target is None:
            return LuteResponse(text=usage_forward)
        chat_type, target_id = target
        message_id = str(invocation.args[1]).strip()
        if not message_id:
            return LuteResponse(text=usage_forward)
        return await call_tool_backend(
            'qq_forward_message',
            {'chat_type': chat_type, 'target_id': target_id, 'message_id': message_id},
            allowed_tool_names={'qq_forward_message'},
        )

    if action == 'merge':
        target = _resolve_target()
        if len(invocation.args) < 2 or target is None:
            return LuteResponse(text=usage_merge)
        chat_type, target_id = target
        message_ids = [str(item).strip() for item in invocation.args[1:] if str(item).strip()]
        if not message_ids:
            return LuteResponse(text=usage_merge)
        return await call_tool_backend(
            'qq_forward_messages',
            {'chat_type': chat_type, 'target_id': target_id, 'messages': message_ids},
            allowed_tool_names={'qq_forward_messages'},
        )

    if verb_name == 'file':
        action = str(invocation.args[0]).strip().lower() if invocation.args else ''
        if action == 'list' and len(invocation.args) == 2:
            return await call_tool_backend(
                'qq_get_group_root_files',
                {'group_id': str(invocation.args[1]).strip()},
                allowed_tool_names={'qq_get_group_root_files'},
            )
        if action == 'all' and len(invocation.args) == 2:
            return await call_tool_backend(
                'qq_get_all_group_files',
                {'group_id': str(invocation.args[1]).strip()},
                allowed_tool_names={'qq_get_all_group_files'},
            )
        if action == 'detail' and len(invocation.args) == 3:
            return await call_tool_backend(
                'qq_get_group_file_detail',
                {'group_id': str(invocation.args[1]).strip(), 'file_id': str(invocation.args[2]).strip()},
                allowed_tool_names={'qq_get_group_file_detail'},
            )
        if action == 'url' and len(invocation.args) == 3:
            return await call_tool_backend(
                'qq_get_group_file_url',
                {'group_id': str(invocation.args[1]).strip(), 'file_id': str(invocation.args[2]).strip()},
                allowed_tool_names={'qq_get_group_file_url'},
            )
        if action == 'mkdir' and len(invocation.args) >= 3:
            return await call_tool_backend(
                'qq_create_group_file_folder',
                {'group_id': str(invocation.args[1]).strip(), 'folder_name': ' '.join(invocation.args[2:]).strip()},
                allowed_tool_names={'qq_create_group_file_folder'},
            )
        if action == 'delete' and len(invocation.args) == 3:
            return await call_tool_backend(
                'qq_delete_group_file',
                {'group_id': str(invocation.args[1]).strip(), 'file_id': str(invocation.args[2]).strip()},
                allowed_tool_names={'qq_delete_group_file'},
            )
        if action == 'download' and len(invocation.args) == 2:
            url = str(invocation.args[1]).strip()
            if not url:
                return LuteResponse(text=usage_file)
            return await call_tool_backend(
                'qq_download_file',
                {'url': url},
                allowed_tool_names={'qq_download_file'},
            )
        return LuteResponse(text=usage_file)

    return LuteResponse(text=usage_send)


async def _dispatch_group(invocation: LuteInvocation, verb_name: str) -> LuteResponse:
    if verb_name == 'admin':
        return await _dispatch_group_admin(invocation)

    if verb_name == 'info':
        if invocation.args:
            return LuteResponse(text=render_single_usage(f'/{invocation.root} group info'))
        default_group_id = _default_group_id(invocation)
        if not default_group_id:
            return LuteResponse(text=render_single_usage(f'/{invocation.root} group info'))
        return await call_tool_backend('qq_get_group_info', {'group_id': default_group_id}, allowed_tool_names={'qq_get_group_info'})

    if verb_name == 'member':
        action = str(invocation.args[0]).strip().lower() if invocation.args else ''
        default_group_id = _default_group_id(invocation)
        usage = _group_member_usage(invocation.root)
        if not default_group_id:
            return LuteResponse(text=usage)
        if action == 'list' and len(invocation.args) == 1:
            return await call_tool_backend(
                'qq_get_group_member_list',
                {'group_id': default_group_id},
                allowed_tool_names={'qq_get_group_member_list'},
            )
        if action == 'detail' and len(invocation.args) >= 2:
            return await call_tool_backend(
                'qq_get_group_member_info',
                {'group_id': default_group_id, 'user_id': str(invocation.args[1]).strip()},
                allowed_tool_names={'qq_get_group_member_info'},
            )
        if action == 'admin' and len(invocation.args) == 3:
            raw_enable = str(invocation.args[2]).strip().lower()
            if raw_enable not in {'on', 'off'}:
                return LuteResponse(text=usage)
            return await call_tool_backend(
                'qq_set_group_admin',
                {'group_id': default_group_id, 'user_id': str(invocation.args[1]).strip(), 'enable': raw_enable == 'on'},
                allowed_tool_names={'qq_set_group_admin'},
            )
        if action == 'card' and len(invocation.args) >= 3:
            return await call_tool_backend(
                'qq_set_group_card',
                {'group_id': default_group_id, 'user_id': str(invocation.args[1]).strip(), 'card': ' '.join(invocation.args[2:]).strip()},
                allowed_tool_names={'qq_set_group_card'},
            )
        if action == 'title' and len(invocation.args) >= 3:
            return await call_tool_backend(
                'qq_set_group_special_title',
                {'group_id': default_group_id, 'user_id': str(invocation.args[1]).strip(), 'special_title': ' '.join(invocation.args[2:]).strip()},
                allowed_tool_names={'qq_set_group_special_title'},
            )
        return LuteResponse(text=usage)

    if verb_name == 'notice':
        action = str(invocation.args[0]).strip().lower() if invocation.args else ''
        default_group_id = _default_group_id(invocation)
        usage = _group_notice_usage(invocation.root)
        if not default_group_id:
            return LuteResponse(text=usage)
        if action == 'send' and len(invocation.args) >= 2:
            payload = {'group_id': default_group_id, 'content': ' '.join(invocation.args[1:]).strip()}
            if 'image' in invocation.options:
                payload['image_url'] = str(invocation.options['image']).strip()
            return await call_tool_backend(
                'qq_send_group_notice',
                payload,
                allowed_tool_names={'qq_send_group_notice'},
            )
        if action == 'clone' and len(invocation.args) >= 3:
            source_group_id = str(invocation.args[1]).strip()
            notice_id = str(invocation.args[2]).strip()
            if not source_group_id or not notice_id:
                return LuteResponse(text=usage)
            detail = await call_tool_backend(
                'qq_get_group_notice_detail',
                {'group_id': source_group_id, 'notice_id': notice_id},
                allowed_tool_names={'qq_get_group_notice_detail'},
            )
            payload = detail.payload if isinstance(detail.payload, dict) else {}
            if not payload.get('success', True):
                return detail
            content = str(payload.get('content') or '').strip()
            if not content:
                return LuteResponse(text='Source notice has no text content to clone.')
            images = payload.get('images') if isinstance(payload.get('images'), list) else []
            image_count = payload.get('image_count')
            if image_count is None:
                image_count = len(images)
            try:
                image_count = int(image_count or 0)
            except (TypeError, ValueError):
                image_count = len(images)
            manual_image = str(invocation.options.get('image') or '').strip()
            referenced_image = _first_referenced_media_path(invocation, media_type='image') or ''
            selected_image = manual_image or referenced_image
            if image_count > 0 and not selected_image:
                return LuteResponse(
                    text=(
                        f'Source notice includes {image_count} image(s). Re-run with --image <url_or_path> or reply to an image message and run the clone command again to publish a replacement image in the current group.\n\n'
                        f'Source content:\n{content}'
                    ),
                    payload=payload,
                )
            send_payload = {'group_id': default_group_id, 'content': content}
            if selected_image:
                send_payload['image_url'] = selected_image
            return await call_tool_backend(
                'qq_send_group_notice',
                send_payload,
                allowed_tool_names={'qq_send_group_notice'},
            )
        if action == 'list' and len(invocation.args) == 1:
            return await call_tool_backend(
                'qq_get_group_notice',
                {'group_id': default_group_id},
                allowed_tool_names={'qq_get_group_notice'},
            )
        if action == 'detail' and len(invocation.args) >= 2:
            return await call_tool_backend(
                'qq_get_group_notice_detail',
                {'group_id': default_group_id, 'notice_id': str(invocation.args[1]).strip()},
                allowed_tool_names={'qq_get_group_notice_detail'},
            )
        if action == 'delete' and len(invocation.args) >= 2:
            return await call_tool_backend(
                'qq_delete_group_notice',
                {'group_id': default_group_id, 'notice_id': str(invocation.args[1]).strip()},
                allowed_tool_names={'qq_delete_group_notice'},
            )
        return LuteResponse(text=usage)


    if verb_name == 'history':
        count = 20
        if 'count' in invocation.options:
            try:
                count = int(str(invocation.options['count']).strip())
            except (TypeError, ValueError):
                return LuteResponse(text=render_single_usage(f'/{invocation.root} group history [--count 20]'))
        default_group_id = _default_group_id(invocation)
        if not default_group_id or invocation.args:
            return LuteResponse(text=render_single_usage(f'/{invocation.root} group history [--count 20]'))
        return await call_tool_backend(
            'qq_get_group_msg_history',
            {'group_id': default_group_id, 'count': count},
            allowed_tool_names={'qq_get_group_msg_history'},
        )

    if verb_name == 'honor':
        default_group_id = _default_group_id(invocation)
        if not default_group_id or invocation.args:
            return LuteResponse(text=render_single_usage(f'/{invocation.root} group honor [--type all]'))
        honor_type = str(invocation.options.get('type') or 'all').strip().lower() or 'all'
        return await call_tool_backend(
            'qq_get_group_honor_info',
            {'group_id': default_group_id, 'type': honor_type},
            allowed_tool_names={'qq_get_group_honor_info'},
        )

    if verb_name == 'name':
        default_group_id = _default_group_id(invocation)
        if not default_group_id or not invocation.args:
            return LuteResponse(text=render_single_usage(f'/{invocation.root} group name <new_name>'))
        return await call_tool_backend(
            'qq_set_group_name',
            {'group_id': default_group_id, 'group_name': ' '.join(invocation.args).strip()},
            allowed_tool_names={'qq_set_group_name'},
        )

    if verb_name == 'whole-ban':
        default_group_id = _default_group_id(invocation)
        if not default_group_id or len(invocation.args) != 1:
            return LuteResponse(text=render_single_usage(f'/{invocation.root} group whole-ban on|off'))
        raw_enable = str(invocation.args[0]).strip().lower()
        if raw_enable not in {'on', 'off'}:
            return LuteResponse(text=render_single_usage(f'/{invocation.root} group whole-ban on|off'))
        return await call_tool_backend(
            'qq_set_group_whole_ban',
            {'group_id': default_group_id, 'enable': raw_enable == 'on'},
            allowed_tool_names={'qq_set_group_whole_ban'},
        )

    if verb_name == 'sign':
        default_group_id = _default_group_id(invocation)
        if not default_group_id or invocation.args:
            return LuteResponse(text=render_single_usage(f'/{invocation.root} group sign'))
        return await call_tool_backend(
            'qq_set_group_sign',
            {'group_id': default_group_id},
            allowed_tool_names={'qq_set_group_sign'},
        )

    if verb_name == 'at-all':
        default_group_id = _default_group_id(invocation)
        if not default_group_id or invocation.args:
            return LuteResponse(text=render_single_usage(f'/{invocation.root} group at-all'))
        return await call_tool_backend(
            'qq_get_group_at_all_remain',
            {'group_id': default_group_id},
            allowed_tool_names={'qq_get_group_at_all_remain'},
        )

    if verb_name == 'essence':
        usage = _group_essence_usage(invocation.root)
        action = str(invocation.args[0]).strip().lower() if invocation.args else ''
        default_group_id = _default_group_id(invocation)
        if not default_group_id:
            return LuteResponse(text=usage)
        if action == 'list' and len(invocation.args) == 1:
            return await call_tool_backend(
                'qq_get_essence_msg_list',
                {'group_id': default_group_id},
                allowed_tool_names={'qq_get_essence_msg_list'},
            )
        if action in {'add', 'remove'}:
            message_id = str(invocation.args[1]).strip() if len(invocation.args) >= 2 else (_default_reply_message_id(invocation) or '')
            if not message_id:
                return LuteResponse(text=usage)
            tool_name = 'qq_set_essence_msg' if action == 'add' else 'qq_delete_essence_msg'
            return await call_tool_backend(
                tool_name,
                {'message_id': message_id},
                allowed_tool_names={'qq_set_essence_msg', 'qq_delete_essence_msg'},
            )
        return LuteResponse(text=usage)

    if verb_name == 'react':
        usage = _group_react_usage(invocation.root)
        action = str(invocation.args[0]).strip().lower() if invocation.args else ''
        default_group_id = _default_group_id(invocation)
        if not default_group_id:
            return LuteResponse(text=usage)
        if action not in {'add', 'remove'} or len(invocation.args) < 2:
            return LuteResponse(text=usage)
        emoji_id = str(invocation.args[1]).strip()
        message_id = str(invocation.args[2]).strip() if len(invocation.args) >= 3 else (_default_reply_message_id(invocation) or '')
        if not emoji_id or not message_id:
            return LuteResponse(text=usage)
        return await call_tool_backend(
            'qq_set_msg_emoji_like',
            {'message_id': message_id, 'emoji_id': emoji_id, 'set': action == 'add'},
            allowed_tool_names={'qq_set_msg_emoji_like'},
        )

    if verb_name == 'read':
        default_group_id = _default_group_id(invocation)
        if not default_group_id or invocation.args:
            return LuteResponse(text=render_single_usage(f'/{invocation.root} group read'))
        return await call_tool_backend(
            'qq_mark_msg_as_read',
            {'chat_type': 'group', 'target_id': default_group_id},
            allowed_tool_names={'qq_mark_msg_as_read'},
        )

    if verb_name == 'poke':
        default_group_id = _default_group_id(invocation)
        if not default_group_id or len(invocation.args) != 1:
            return LuteResponse(text=render_single_usage(f'/{invocation.root} group poke <qq_id>'))
        return await call_tool_backend(
            'qq_poke',
            {'group_id': default_group_id, 'user_id': str(invocation.args[0]).strip()},
            allowed_tool_names={'qq_poke'},
        )

    if verb_name == 'recall':
        message_id = str(invocation.args[0]).strip() if invocation.args else _default_reply_message_id(invocation)
        if not message_id:
            return LuteResponse(text=render_single_usage(f'/{invocation.root} group recall <message_id>'))
        return await call_tool_backend(
            'qq_recall_message',
            {'message_id': message_id},
            allowed_tool_names={'qq_recall_message'},
        )

    if verb_name == 'mute':
        default_group_id = _default_group_id(invocation)
        if not default_group_id or len(invocation.args) < 2:
            return LuteResponse(text=render_single_usage(f'/{invocation.root} group mute <qq_id> <seconds>'))
        try:
            duration = int(str(invocation.args[1]).strip())
        except (TypeError, ValueError):
            return LuteResponse(text=render_single_usage(f'/{invocation.root} group mute <qq_id> <seconds>'))
        return await call_tool_backend(
            'qq_mute_group_member',
            {'group_id': default_group_id, 'user_id': str(invocation.args[0]).strip(), 'duration_seconds': duration},
            allowed_tool_names={'qq_mute_group_member'},
        )

    if verb_name == 'kick':
        default_group_id = _default_group_id(invocation)
        if not default_group_id or len(invocation.args) < 1:
            return LuteResponse(text=render_single_usage(f'/{invocation.root} group kick <qq_id>'))
        return await call_tool_backend(
            'qq_kick_group_member',
            {'group_id': default_group_id, 'user_id': str(invocation.args[0]).strip()},
            allowed_tool_names={'qq_kick_group_member'},
        )

    return LuteResponse(text=f'Unknown /{invocation.root} verb: {verb_name}\nTry: /{invocation.root} help group')


async def _dispatch_group_admin(invocation: LuteInvocation) -> LuteResponse:
    action = str(invocation.args[0]).strip().lower() if invocation.args else ''
    default_group_id = _default_group_id(invocation)

    if action in {'show', 'status'}:
        if len(invocation.args) != 1 or not default_group_id:
            return _group_admin_show_usage_response(invocation.root)
        response = await call_tool_backend(
            'qq_group_admin_get_config',
            {'group_id': default_group_id},
            allowed_tool_names={'qq_group_admin_get_config'},
        )
        return LuteResponse(text=_format_group_admin_summary(response, fallback_group_id=default_group_id))

    if action == 'reset':
        if len(invocation.args) != 1 or not default_group_id:
            return _group_admin_reset_usage_response(invocation.root)
        return await call_tool_backend(
            'qq_group_admin_reset_config',
            {'group_id': default_group_id},
            allowed_tool_names={'qq_group_admin_reset_config'},
        )

    if action == 'moderation':
        return await _dispatch_group_admin_moderation(invocation, default_group_id)

    if action == 'join':
        return await _dispatch_group_admin_join(invocation, default_group_id)

    if action == 'leave':
        return await _dispatch_group_admin_leave(invocation, default_group_id)

    if action == 'curfew':
        return await _dispatch_group_admin_curfew(invocation, default_group_id)

    if action == 'cleanup':
        return await _dispatch_group_admin_cleanup(invocation, default_group_id)

    if action in {'gname', 'title'}:
        return await _dispatch_group_admin_manual_profile(invocation, default_group_id)

    if action == 'ai':
        return await _dispatch_group_admin_ai(invocation, default_group_id)

    return _group_admin_root_usage_response(invocation.root)


async def _dispatch_group_admin_moderation(invocation: LuteInvocation, default_group_id: str | None) -> LuteResponse:
    action = str(invocation.args[1]).strip().lower() if len(invocation.args) >= 2 else ''

    if action in {'show', 'status'}:
        if len(invocation.args) != 2 or not default_group_id:
            return LuteResponse(text=_group_admin_moderation_usage(invocation.root))
        response = await call_tool_backend(
            'qq_group_admin_get_config',
            {'group_id': default_group_id},
            allowed_tool_names={'qq_group_admin_get_config'},
        )
        return LuteResponse(text=_format_group_admin_moderation_summary(response, fallback_group_id=default_group_id))

    if action == 'builtin':
        if len(invocation.args) != 3 or not default_group_id:
            return LuteResponse(text=_group_admin_moderation_usage(invocation.root))
        raw_enable = str(invocation.args[2]).strip().lower()
        if raw_enable not in {'on', 'off'}:
            return LuteResponse(text=_group_admin_moderation_usage(invocation.root))
        return await call_tool_backend(
            'qq_group_admin_update_config',
            {'group_id': default_group_id, 'updates': {'builtin_ban': raw_enable == 'on'}},
            allowed_tool_names={'qq_group_admin_update_config'},
        )

    if action == 'words':
        return await _dispatch_group_admin_moderation_words(invocation, default_group_id)

    if action == 'word-ban':
        if len(invocation.args) != 3 or not default_group_id:
            return LuteResponse(text=_group_admin_moderation_usage(invocation.root))
        seconds = _parse_group_admin_int(invocation.args[2])
        if seconds is None:
            return LuteResponse(text=_group_admin_moderation_usage(invocation.root))
        return await call_tool_backend(
            'qq_group_admin_update_config',
            {'group_id': default_group_id, 'updates': {'word_ban_time': seconds}},
            allowed_tool_names={'qq_group_admin_update_config'},
        )

    if action == 'spam-ban':
        if len(invocation.args) != 3 or not default_group_id:
            return LuteResponse(text=_group_admin_moderation_usage(invocation.root))
        seconds = _parse_group_admin_int(invocation.args[2])
        if seconds is None:
            return LuteResponse(text=_group_admin_moderation_usage(invocation.root))
        return await call_tool_backend(
            'qq_group_admin_update_config',
            {'group_id': default_group_id, 'updates': {'spamming_ban_time': seconds}},
            allowed_tool_names={'qq_group_admin_update_config'},
        )

    return LuteResponse(text=_group_admin_moderation_usage(invocation.root))


async def _dispatch_group_admin_moderation_words(invocation: LuteInvocation, default_group_id: str | None) -> LuteResponse:
    usage = _group_admin_moderation_usage(invocation.root)
    if not default_group_id or len(invocation.args) < 4:
        return LuteResponse(text=usage)
    action = str(invocation.args[2]).strip().lower()
    words = _normalize_group_admin_terms(invocation.args[3:])
    if action not in {'set', 'add', 'remove'} or not words:
        return LuteResponse(text=usage)

    if action == 'set':
        return await call_tool_backend(
            'qq_group_admin_update_config',
            {'group_id': default_group_id, 'updates': {'custom_ban_words': words}},
            allowed_tool_names={'qq_group_admin_update_config'},
        )

    current_response = await call_tool_backend(
        'qq_group_admin_get_config',
        {'group_id': default_group_id},
        allowed_tool_names={'qq_group_admin_get_config'},
    )
    extracted = _extract_group_admin_config_payload(current_response)
    if extracted is None:
        return current_response
    _, config = extracted
    current_words = _group_admin_list(config.get('custom_ban_words'))

    if action == 'add':
        next_words = _normalize_group_admin_terms([*current_words, *words])
    else:
        remove_set = set(words)
        next_words = [word for word in current_words if word not in remove_set]

    return await call_tool_backend(
        'qq_group_admin_update_config',
        {'group_id': default_group_id, 'updates': {'custom_ban_words': next_words}},
        allowed_tool_names={'qq_group_admin_update_config'},
    )


async def _dispatch_group_admin_join(invocation: LuteInvocation, default_group_id: str | None) -> LuteResponse:
    action = str(invocation.args[1]).strip().lower() if len(invocation.args) >= 2 else ''
    usage = _group_admin_join_usage(invocation.root)

    if action in {'show', 'status'}:
        if len(invocation.args) != 2 or not default_group_id:
            return LuteResponse(text=usage)
        response = await call_tool_backend(
            'qq_group_admin_get_config',
            {'group_id': default_group_id},
            allowed_tool_names={'qq_group_admin_get_config'},
        )
        return LuteResponse(text=_format_group_admin_join_summary(response, fallback_group_id=default_group_id))

    if action == 'switch':
        return await _dispatch_group_admin_toggle_field(
            invocation,
            default_group_id,
            arg_index=2,
            field_name='join_switch',
            usage=usage,
        )

    if action in {'accept', 'reject'}:
        field_name = 'join_accept_words' if action == 'accept' else 'join_reject_words'
        return await _dispatch_group_admin_list_field(
            invocation,
            default_group_id,
            field_name=field_name,
            usage=usage,
        )

    if action == 'no-match-reject':
        return await _dispatch_group_admin_toggle_field(
            invocation,
            default_group_id,
            arg_index=2,
            field_name='join_no_match_reject',
            usage=usage,
        )

    if action == 'reject-word-block':
        return await _dispatch_group_admin_toggle_field(
            invocation,
            default_group_id,
            arg_index=2,
            field_name='reject_word_block',
            usage=usage,
        )

    if action == 'blacklist':
        return await _dispatch_group_admin_blacklist(invocation, default_group_id)

    if action == 'min-level':
        return await _dispatch_group_admin_int_field(
            invocation,
            default_group_id,
            arg_index=2,
            field_name='join_min_level',
            usage=usage,
        )

    if action == 'max-attempts':
        return await _dispatch_group_admin_int_field(
            invocation,
            default_group_id,
            arg_index=2,
            field_name='join_max_time',
            usage=usage,
        )

    if action == 'welcome':
        return await _dispatch_group_admin_join_welcome(invocation, default_group_id, usage)

    if action == 'ban':
        return await _dispatch_group_admin_int_field(
            invocation,
            default_group_id,
            arg_index=2,
            field_name='join_ban_time',
            usage=usage,
        )

    return LuteResponse(text=usage)


async def _dispatch_group_admin_leave(invocation: LuteInvocation, default_group_id: str | None) -> LuteResponse:
    action = str(invocation.args[1]).strip().lower() if len(invocation.args) >= 2 else ''
    usage = _group_admin_leave_usage(invocation.root)

    if action == 'notify':
        return await _dispatch_group_admin_toggle_field(
            invocation,
            default_group_id,
            arg_index=2,
            field_name='leave_notify',
            usage=usage,
        )

    if action == 'block':
        return await _dispatch_group_admin_toggle_field(
            invocation,
            default_group_id,
            arg_index=2,
            field_name='leave_block',
            usage=usage,
        )

    if action == 'kick-block':
        return await _dispatch_group_admin_toggle_field(
            invocation,
            default_group_id,
            arg_index=2,
            field_name='kick_block',
            usage=usage,
        )

    return LuteResponse(text=usage)


async def _dispatch_group_admin_curfew(invocation: LuteInvocation, default_group_id: str | None) -> LuteResponse:
    action = str(invocation.args[1]).strip().lower() if len(invocation.args) >= 2 else ''
    usage = _group_admin_curfew_usage(invocation.root)
    usage_response = _group_admin_curfew_usage_response(invocation.root)

    if action in {'show', 'status'}:
        if len(invocation.args) != 2 or not default_group_id:
            return usage_response
        response = await call_tool_backend(
            'qq_group_admin_get_config',
            {'group_id': default_group_id},
            allowed_tool_names={'qq_group_admin_get_config'},
        )
        return LuteResponse(text=_format_group_admin_curfew_summary(response, fallback_group_id=default_group_id))

    if action == 'set':
        if len(invocation.args) != 4 or not default_group_id:
            return usage_response
        start = str(invocation.args[2]).strip()
        end = str(invocation.args[3]).strip()
        if not _is_valid_hhmm(start) or not _is_valid_hhmm(end):
            return usage_response
        return await call_tool_backend(
            'qq_group_admin_update_config',
            {'group_id': default_group_id, 'updates': {'curfew_enabled': True, 'curfew_start': start, 'curfew_end': end}},
            allowed_tool_names={'qq_group_admin_update_config'},
        )

    if action == 'clear':
        if len(invocation.args) != 2 or not default_group_id:
            return usage_response
        return await call_tool_backend(
            'qq_group_admin_update_config',
            {'group_id': default_group_id, 'updates': {'curfew_enabled': False, 'curfew_start': '', 'curfew_end': ''}},
            allowed_tool_names={'qq_group_admin_update_config'},
        )

    return usage_response


async def _dispatch_group_admin_cleanup(invocation: LuteInvocation, default_group_id: str | None) -> LuteResponse:
    usage = _group_admin_cleanup_usage(invocation.root)
    action = str(invocation.args[1]).strip().lower() if len(invocation.args) >= 2 else ''
    if not default_group_id:
        return LuteResponse(text=usage)

    inactive_days = 30
    max_level = 1
    if 'inactive_days' in invocation.options:
        parsed = _parse_group_admin_int(invocation.options.get('inactive_days'))
        if parsed is None:
            return LuteResponse(text=usage)
        inactive_days = parsed
    if 'max_level' in invocation.options:
        parsed = _parse_group_admin_int(invocation.options.get('max_level'))
        if parsed is None:
            return LuteResponse(text=usage)
        max_level = parsed

    if action == 'preview' and len(invocation.args) == 2:
        return await call_tool_backend(
            'qq_group_member_cleanup_preview',
            {'group_id': default_group_id, 'inactive_days': inactive_days, 'max_level': max_level},
            allowed_tool_names={'qq_group_member_cleanup_preview'},
        )

    if action == 'apply' and len(invocation.args) >= 2:
        user_ids = _normalize_group_admin_terms(invocation.args[2:])
        return await call_tool_backend(
            'qq_group_member_cleanup_apply',
            {
                'group_id': default_group_id,
                'user_ids': user_ids,
                'inactive_days': inactive_days,
                'max_level': max_level,
                'reject_add_request': bool(invocation.options.get('reject_add_request')),
            },
            allowed_tool_names={'qq_group_member_cleanup_apply'},
        )

    return LuteResponse(text=usage)


async def _dispatch_group_admin_ai(invocation: LuteInvocation, default_group_id: str | None) -> LuteResponse:
    usage = _group_admin_ai_usage(invocation.root)
    scope = str(invocation.args[1]).strip().lower() if len(invocation.args) >= 2 else ''
    if scope == 'card':
        scope = 'gname'
    if not default_group_id or scope not in {'gname', 'title'}:
        return LuteResponse(text=usage)

    apply = False
    user_index = 2
    if len(invocation.args) >= 3 and str(invocation.args[2]).strip().lower() == 'apply':
        apply = True
        user_index = 3
    if len(invocation.args) <= user_index:
        return LuteResponse(text=usage)

    user_id = str(invocation.args[user_index]).strip()
    if not user_id:
        return LuteResponse(text=usage)

    history_count = 50
    if 'history_count' in invocation.options:
        parsed = _parse_group_admin_int(invocation.options.get('history_count'))
        if parsed is None:
            return LuteResponse(text=usage)
        history_count = parsed

    tool_name = 'qq_group_ai_set_card' if scope == 'gname' else 'qq_group_ai_set_title'
    return await call_tool_backend(
        tool_name,
        {'group_id': default_group_id, 'user_id': user_id, 'history_count': history_count, 'apply': apply},
        allowed_tool_names={'qq_group_ai_set_card', 'qq_group_ai_set_title'},
    )


async def _dispatch_group_admin_manual_profile(invocation: LuteInvocation, default_group_id: str | None) -> LuteResponse:
    usage = _group_admin_manual_profile_usage(invocation.root)
    action = str(invocation.args[0]).strip().lower() if invocation.args else ''
    if not default_group_id or action not in {'gname', 'title'} or len(invocation.args) < 3:
        return LuteResponse(text=usage)
    user_id = str(invocation.args[1]).strip()
    value = ' '.join(invocation.args[2:]).strip()
    if not user_id or not value:
        return LuteResponse(text=usage)
    tool_name = 'qq_group_admin_set_gname' if action == 'gname' else 'qq_group_admin_set_title'
    payload_key = 'gname' if action == 'gname' else 'title'
    return await call_tool_backend(
        tool_name,
        {'group_id': default_group_id, 'user_id': user_id, payload_key: value},
        allowed_tool_names={'qq_group_admin_set_gname', 'qq_group_admin_set_title'},
    )


async def _dispatch_group_admin_toggle_field(
    invocation: LuteInvocation,
    default_group_id: str | None,
    *,
    arg_index: int,
    field_name: str,
    usage: str,
) -> LuteResponse:
    if not default_group_id or len(invocation.args) <= arg_index:
        return LuteResponse(text=usage)
    raw_value = str(invocation.args[arg_index]).strip().lower()
    if raw_value not in {'on', 'off'}:
        return LuteResponse(text=usage)
    return await call_tool_backend(
        'qq_group_admin_update_config',
        {'group_id': default_group_id, 'updates': {field_name: raw_value == 'on'}},
        allowed_tool_names={'qq_group_admin_update_config'},
    )


async def _dispatch_group_admin_int_field(
    invocation: LuteInvocation,
    default_group_id: str | None,
    *,
    arg_index: int,
    field_name: str,
    usage: str,
) -> LuteResponse:
    if not default_group_id or len(invocation.args) <= arg_index:
        return LuteResponse(text=usage)
    value = _parse_group_admin_int(invocation.args[arg_index])
    if value is None:
        return LuteResponse(text=usage)
    return await call_tool_backend(
        'qq_group_admin_update_config',
        {'group_id': default_group_id, 'updates': {field_name: value}},
        allowed_tool_names={'qq_group_admin_update_config'},
    )


async def _dispatch_group_admin_list_field(
    invocation: LuteInvocation,
    default_group_id: str | None,
    *,
    field_name: str,
    usage: str,
) -> LuteResponse:
    if not default_group_id or len(invocation.args) < 4:
        return LuteResponse(text=usage)
    action = str(invocation.args[2]).strip().lower()
    values = _normalize_group_admin_terms(invocation.args[3:])
    if action not in {'set', 'add', 'remove'} or not values:
        return LuteResponse(text=usage)

    if action == 'set':
        next_values = values
    else:
        current_response = await call_tool_backend(
            'qq_group_admin_get_config',
            {'group_id': default_group_id},
            allowed_tool_names={'qq_group_admin_get_config'},
        )
        extracted = _extract_group_admin_config_payload(current_response)
        if extracted is None:
            return current_response
        _, config = extracted
        current_values = _group_admin_list(config.get(field_name))
        if action == 'add':
            next_values = _normalize_group_admin_terms([*current_values, *values])
        else:
            remove_set = set(values)
            next_values = [value for value in current_values if value not in remove_set]

    return await call_tool_backend(
        'qq_group_admin_update_config',
        {'group_id': default_group_id, 'updates': {field_name: next_values}},
        allowed_tool_names={'qq_group_admin_update_config'},
    )


async def _dispatch_group_admin_blacklist(invocation: LuteInvocation, default_group_id: str | None) -> LuteResponse:
    usage = _group_admin_join_usage(invocation.root)
    if not default_group_id or len(invocation.args) < 3:
        return LuteResponse(text=usage)
    action = str(invocation.args[2]).strip().lower()

    if action == 'list':
        if len(invocation.args) != 3:
            return LuteResponse(text=usage)
        return await call_tool_backend(
            'qq_group_admin_blacklist',
            {'group_id': default_group_id, 'action': 'list'},
            allowed_tool_names={'qq_group_admin_blacklist'},
        )

    if action in {'add', 'remove'} and len(invocation.args) >= 4:
        values = _normalize_group_admin_terms(invocation.args[3:])
        if not values:
            return LuteResponse(text=usage)
        return await call_tool_backend(
            'qq_group_admin_blacklist',
            {'group_id': default_group_id, 'action': action, 'user_ids': values},
            allowed_tool_names={'qq_group_admin_blacklist'},
        )

    return LuteResponse(text=usage)


async def _dispatch_group_admin_join_welcome(invocation: LuteInvocation, default_group_id: str | None, usage: str) -> LuteResponse:
    if not default_group_id or len(invocation.args) < 3:
        return LuteResponse(text=usage)
    action = str(invocation.args[2]).strip().lower()
    if action == 'clear' and len(invocation.args) == 3:
        welcome = ''
    elif action == 'set' and len(invocation.args) >= 4:
        welcome = ' '.join(str(item).strip() for item in invocation.args[3:] if str(item).strip()).strip()
        if not welcome:
            return LuteResponse(text=usage)
    else:
        return LuteResponse(text=usage)
    return await call_tool_backend(
        'qq_group_admin_update_config',
        {'group_id': default_group_id, 'updates': {'join_welcome': welcome}},
        allowed_tool_names={'qq_group_admin_update_config'},
    )


def _group_member_usage(root: str) -> str:
    return render_usage_list(
        [
            LuteUsageEntry(f'/{root} group member list', '查看当前群成员列表'),
            LuteUsageEntry(f'/{root} group member detail <qq_id>', '查看指定成员资料'),
            LuteUsageEntry(f'/{root} group member admin <qq_id> on|off', '设置或取消群管理员'),
            LuteUsageEntry(f'/{root} group member card <qq_id> <card>', '修改成员群名片'),
            LuteUsageEntry(f'/{root} group member title <qq_id> <title>', '修改成员专属头衔'),
        ]
    )


def _group_notice_usage(root: str) -> str:
    return render_usage_list(
        [
            LuteUsageEntry(f'/{root} group notice send <内容> [--image <url_or_path>]', '发送当前群公告'),
            LuteUsageEntry(f'/{root} group notice clone <source_group_id> <notice_id> [--image <url_or_path>]', '克隆公告，可改配图'),
            LuteUsageEntry(f'/{root} group notice list', '查看当前群公告列表'),
            LuteUsageEntry(f'/{root} group notice detail <notice_id>', '查看公告详情'),
            LuteUsageEntry(f'/{root} group notice delete <notice_id>', '删除当前群公告'),
        ]
    )


def _group_essence_usage(root: str) -> str:
    return render_usage_list(
        [
            LuteUsageEntry(f'/{root} group essence list', '查看当前群精华消息'),
            LuteUsageEntry(f'/{root} group essence add [message_id]', '添加精华消息'),
            LuteUsageEntry(f'/{root} group essence remove [message_id]', '移除精华消息'),
        ]
    )


def _group_react_usage(root: str) -> str:
    return render_usage_list(
        [
            LuteUsageEntry(f'/{root} group react add <emoji_id> [message_id]', '为目标消息添加表情回应'),
            LuteUsageEntry(f'/{root} group react remove <emoji_id> [message_id]', '移除目标消息的表情回应'),
        ]
    )


def _group_admin_root_usage(root: str) -> str:
    return render_usage_list(
        [
            LuteUsageEntry(f'/{root} group admin show', '查看本群 qqadmin 总览'),
            LuteUsageEntry(f'/{root} group admin status', '查看本群 qqadmin 总览（show 别名）'),
            LuteUsageEntry(f'/{root} group admin reset', '重置本群 qqadmin 配置'),
            LuteUsageEntry(f'/{root} group admin moderation ...', '管理违禁词、内置词库和封禁时长'),
            LuteUsageEntry(f'/{root} group admin join ...', '管理进群审核、黑名单、欢迎语和入群禁言'),
            LuteUsageEntry(f'/{root} group admin leave ...', '管理退群/踢出后的通知与拉黑策略'),
            LuteUsageEntry(f'/{root} group admin curfew ...', '管理宵禁时间窗'),
            LuteUsageEntry(f'/{root} group admin cleanup ...', '预览或执行成员清理'),
            LuteUsageEntry(f'/{root} group admin gname <qq_id> <文本>', '手动设置群昵称'),
            LuteUsageEntry(f'/{root} group admin title <qq_id> <文本>', '手动设置群头衔'),
            LuteUsageEntry(f'/{root} group admin ai gname ...', '生成群昵称建议并可选应用'),
            LuteUsageEntry(f'/{root} group admin ai title ...', '生成头衔建议并可选应用'),
        ]
    )


def _usage_entries_payload(entries: list[LuteUsageEntry]) -> list[dict[str, str]]:
    return [
        {
            'syntax': str(entry.syntax).strip(),
            'description': str(entry.description).strip(),
        }
        for entry in entries
        if str(entry.syntax).strip()
    ]


def _usage_list_response(entries: list[LuteUsageEntry], *, domain: str, verb: str) -> LuteResponse:
    fallback = render_usage_list(entries)
    return LuteResponse(
        text='',
        view=ViewSpec(
            kind='image',
            template='help.usage-card',
            data={
                'variant': 'usage-list',
                'title': 'Lute Usage / Help',
                'subtitle': '命令速查卡',
                'domain': domain,
                'verb': verb,
                'entries': _usage_entries_payload(entries),
            },
            fallback_text=fallback,
            cache_policy=ViewCachePolicy(namespace='help-jpeg-q80'),
            telemetry_tags={'domain': domain, 'verb': verb},
        ),
    )


def _single_usage_response(syntax: str, *, domain: str, verb: str) -> LuteResponse:
    fallback = render_single_usage(syntax)
    return LuteResponse(
        text='',
        view=ViewSpec(
            kind='image',
            template='help.usage-card',
            data={
                'variant': 'usage-single',
                'title': 'Lute Usage / Help',
                'subtitle': '命令速查卡',
                'domain': domain,
                'verb': verb,
                'syntax': str(syntax).strip(),
            },
            fallback_text=fallback,
            cache_policy=ViewCachePolicy(namespace='help-jpeg-q80'),
            telemetry_tags={'domain': domain, 'verb': verb},
        ),
    )


def _group_admin_root_usage_response(root: str) -> LuteResponse:
    entries = [
        LuteUsageEntry(f'/{root} group admin show', '查看本群 qqadmin 总览'),
        LuteUsageEntry(f'/{root} group admin status', '查看本群 qqadmin 总览（show 别名）'),
        LuteUsageEntry(f'/{root} group admin reset', '重置本群 qqadmin 配置'),
        LuteUsageEntry(f'/{root} group admin moderation ...', '管理违禁词、内置词库和封禁时长'),
        LuteUsageEntry(f'/{root} group admin join ...', '管理进群审核、黑名单、欢迎语和入群禁言'),
        LuteUsageEntry(f'/{root} group admin leave ...', '管理退群/踢出后的通知与拉黑策略'),
        LuteUsageEntry(f'/{root} group admin curfew ...', '管理宵禁时间窗'),
        LuteUsageEntry(f'/{root} group admin cleanup ...', '预览或执行成员清理'),
        LuteUsageEntry(f'/{root} group admin gname <qq_id> <文本>', '手动设置群昵称'),
        LuteUsageEntry(f'/{root} group admin title <qq_id> <文本>', '手动设置群头衔'),
        LuteUsageEntry(f'/{root} group admin ai gname ...', '生成群昵称建议并可选应用'),
        LuteUsageEntry(f'/{root} group admin ai title ...', '生成头衔建议并可选应用'),
    ]
    return _usage_list_response(entries, domain='group', verb='admin')


def _group_admin_show_usage(root: str) -> str:
    return render_usage_list(
        [
            LuteUsageEntry(f'/{root} group admin show', '查看本群 qqadmin 总览'),
            LuteUsageEntry(f'/{root} group admin status', '查看本群 qqadmin 总览（show 别名）'),
        ]
    )


def _group_admin_show_usage_response(root: str) -> LuteResponse:
    return _usage_list_response(
        [
            LuteUsageEntry(f'/{root} group admin show', '查看本群 qqadmin 总览'),
            LuteUsageEntry(f'/{root} group admin status', '查看本群 qqadmin 总览（show 别名）'),
        ],
        domain='group',
        verb='admin',
    )


def _group_admin_reset_usage(root: str) -> str:
    return render_single_usage(f'/{root} group admin reset')


def _group_admin_reset_usage_response(root: str) -> LuteResponse:
    return _single_usage_response(f'/{root} group admin reset', domain='group', verb='admin')


def _group_admin_moderation_usage(root: str) -> str:
    return render_usage_list(
        [
            LuteUsageEntry(f'/{root} group admin moderation show', '查看违禁词与封禁配置'),
            LuteUsageEntry(f'/{root} group admin moderation status', '查看违禁词与封禁配置（show 别名）'),
            LuteUsageEntry(f'/{root} group admin moderation builtin on|off', '开关内置违禁词库'),
            LuteUsageEntry(f'/{root} group admin moderation words set <词...>', '覆盖自定义违禁词列表'),
            LuteUsageEntry(f'/{root} group admin moderation words add <词...>', '追加自定义违禁词'),
            LuteUsageEntry(f'/{root} group admin moderation words remove <词...>', '删除自定义违禁词'),
            LuteUsageEntry(f'/{root} group admin moderation word-ban <seconds>', '设置违禁词触发封禁时长'),
            LuteUsageEntry(f'/{root} group admin moderation spam-ban <seconds>', '设置刷屏触发封禁时长'),
        ]
    )


def _group_admin_join_usage(root: str) -> str:
    return render_usage_list(
        [
            LuteUsageEntry(f'/{root} group admin join show', '查看进群审核配置'),
            LuteUsageEntry(f'/{root} group admin join status', '查看进群审核配置（show 别名）'),
            LuteUsageEntry(f'/{root} group admin join switch on|off', '开关进群审核'),
            LuteUsageEntry(f'/{root} group admin join accept set|add|remove <词...>', '管理进群通过关键词'),
            LuteUsageEntry(f'/{root} group admin join reject set|add|remove <词...>', '管理进群拒绝关键词'),
            LuteUsageEntry(f'/{root} group admin join no-match-reject on|off', '开关未命中时拒绝'),
            LuteUsageEntry(f'/{root} group admin join reject-word-block on|off', '开关拒绝词自动拉黑'),
            LuteUsageEntry(f'/{root} group admin join blacklist list|add|remove <qq_id...>', '管理进群黑名单'),
            LuteUsageEntry(f'/{root} group admin join min-level <n>', '设置最低等级要求'),
            LuteUsageEntry(f'/{root} group admin join max-attempts <n>', '设置最大尝试次数'),
            LuteUsageEntry(f'/{root} group admin join welcome set <text>', '设置欢迎语'),
            LuteUsageEntry(f'/{root} group admin join welcome clear', '清空欢迎语'),
            LuteUsageEntry(f'/{root} group admin join ban <seconds>', '设置入群后禁言时长'),
        ]
    )


def _group_admin_leave_usage(root: str) -> str:
    return render_usage_list(
        [
            LuteUsageEntry(f'/{root} group admin leave notify on|off', '开关退群通知'),
            LuteUsageEntry(f'/{root} group admin leave block on|off', '开关退群后拉黑'),
            LuteUsageEntry(f'/{root} group admin leave kick-block on|off', '开关被踢后拉黑'),
        ]
    )


def _group_admin_curfew_usage(root: str) -> str:
    return render_usage_list(
        [
            LuteUsageEntry(f'/{root} group admin curfew show', '查看宵禁配置'),
            LuteUsageEntry(f'/{root} group admin curfew status', '查看宵禁配置（show 别名）'),
            LuteUsageEntry(f'/{root} group admin curfew set <HH:MM> <HH:MM>', '设置宵禁时间窗'),
            LuteUsageEntry(f'/{root} group admin curfew clear', '清除宵禁时间窗'),
        ]
    )


def _group_admin_curfew_usage_response(root: str) -> LuteResponse:
    return _usage_list_response(
        [
            LuteUsageEntry(f'/{root} group admin curfew show', '查看宵禁配置'),
            LuteUsageEntry(f'/{root} group admin curfew status', '查看宵禁配置（show 别名）'),
            LuteUsageEntry(f'/{root} group admin curfew set <HH:MM> <HH:MM>', '设置宵禁时间窗'),
            LuteUsageEntry(f'/{root} group admin curfew clear', '清除宵禁时间窗'),
        ],
        domain='group',
        verb='admin',
    )


def _group_admin_cleanup_usage(root: str) -> str:
    return render_usage_list(
        [
            LuteUsageEntry(f'/{root} group admin cleanup preview [--inactive-days <n>] [--max-level <n>]', '预览待清理成员'),
            LuteUsageEntry(f'/{root} group admin cleanup apply [<qq_id...>] [--inactive-days <n>] [--max-level <n>] [--reject-add-request]', '执行成员清理'),
        ]
    )


def _group_admin_ai_usage(root: str) -> str:
    return render_usage_list(
        [
            LuteUsageEntry(f'/{root} group admin ai gname <qq_id> [--history-count <n>]', '生成群昵称建议'),
            LuteUsageEntry(f'/{root} group admin ai gname apply <qq_id> [--history-count <n>]', '生成并应用群昵称建议'),
            LuteUsageEntry(f'/{root} group admin ai title <qq_id> [--history-count <n>]', '生成头衔建议'),
            LuteUsageEntry(f'/{root} group admin ai title apply <qq_id> [--history-count <n>]', '生成并应用头衔建议'),
        ]
    )


def _group_admin_manual_profile_usage(root: str) -> str:
    return render_usage_list(
        [
            LuteUsageEntry(f'/{root} group admin gname <qq_id> <文本>', '手动设置群昵称'),
            LuteUsageEntry(f'/{root} group admin title <qq_id> <文本>', '手动设置群头衔'),
        ]
    )


def _extract_group_admin_config_payload(response: LuteResponse) -> tuple[str, dict[str, object]] | None:
    payload = response.payload
    if not isinstance(payload, dict):
        return None
    config = payload.get('config')
    if not isinstance(config, dict):
        return None
    group_id = str(payload.get('group_id') or '').strip()
    return group_id, config


def _group_admin_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _normalize_group_admin_terms(values: list[object]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        normalized.append(text)
    return normalized


def _parse_group_admin_int(value: object) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _is_valid_hhmm(value: str) -> bool:
    match = re.fullmatch(r'(\d{2}):(\d{2})', str(value).strip())
    if match is None:
        return False
    hour = int(match.group(1))
    minute = int(match.group(2))
    return 0 <= hour <= 23 and 0 <= minute <= 59


def _format_group_admin_summary(response: LuteResponse, *, fallback_group_id: str) -> str:
    extracted = _extract_group_admin_config_payload(response)
    if extracted is None:
        return response.text.strip()
    group_id, config = extracted
    current_group_id = group_id or fallback_group_id
    words = _group_admin_list(config.get('custom_ban_words'))
    accept_words = _group_admin_list(config.get('join_accept_words'))
    reject_words = _group_admin_list(config.get('join_reject_words'))
    block_ids = _group_admin_list(config.get('block_ids'))
    welcome_state = 'set' if str(config.get('join_welcome') or '').strip() else 'clear'
    curfew_enabled = bool(config.get('curfew_enabled'))
    curfew_start = str(config.get('curfew_start') or '').strip()
    curfew_end = str(config.get('curfew_end') or '').strip()
    if curfew_enabled and curfew_start and curfew_end:
        curfew_state = f'enabled {curfew_start}-{curfew_end}'
    else:
        curfew_state = 'disabled'
    return '\n'.join(
        [
            f'QQAdmin config for current group {current_group_id}',
            f"Moderation: builtin={'on' if bool(config.get('builtin_ban')) else 'off'}, words={len(words)}, word-ban={config.get('word_ban_time', 0)}, spam-ban={config.get('spamming_ban_time', 0)}",
            f"Join: switch={'on' if bool(config.get('join_switch')) else 'off'}, accept={len(accept_words)}, reject={len(reject_words)}, blacklist={len(block_ids)}, welcome={welcome_state}",
            f"Leave: notify={'on' if bool(config.get('leave_notify')) else 'off'}, block={'on' if bool(config.get('leave_block')) else 'off'}, kick-block={'on' if bool(config.get('kick_block')) else 'off'}",
            f'Curfew: {curfew_state}',
        ]
    )


def _format_group_admin_moderation_summary(response: LuteResponse, *, fallback_group_id: str) -> str:
    extracted = _extract_group_admin_config_payload(response)
    if extracted is None:
        return response.text.strip()
    group_id, config = extracted
    current_group_id = group_id or fallback_group_id
    words = _group_admin_list(config.get('custom_ban_words'))
    words_text = ', '.join(words) if words else '(none)'
    return '\n'.join(
        [
            f'Moderation config for current group {current_group_id}',
            f"builtin: {'on' if bool(config.get('builtin_ban')) else 'off'}",
            f'words ({len(words)}): {words_text}',
            f"word-ban: {config.get('word_ban_time', 0)}",
            f"spam-ban: {config.get('spamming_ban_time', 0)}",
        ]
    )


def _format_group_admin_join_summary(response: LuteResponse, *, fallback_group_id: str) -> str:
    extracted = _extract_group_admin_config_payload(response)
    if extracted is None:
        return response.text.strip()
    group_id, config = extracted
    current_group_id = group_id or fallback_group_id
    accept_words = _group_admin_list(config.get('join_accept_words'))
    reject_words = _group_admin_list(config.get('join_reject_words'))
    block_ids = _group_admin_list(config.get('block_ids'))
    accept_text = ', '.join(accept_words) if accept_words else '(none)'
    reject_text = ', '.join(reject_words) if reject_words else '(none)'
    block_text = ', '.join(block_ids) if block_ids else '(none)'
    welcome = str(config.get('join_welcome') or '').strip() or '(clear)'
    return '\n'.join(
        [
            f'Join config for current group {current_group_id}',
            f"switch: {'on' if bool(config.get('join_switch')) else 'off'}",
            f'accept ({len(accept_words)}): {accept_text}',
            f'reject ({len(reject_words)}): {reject_text}',
            f"no-match-reject: {'on' if bool(config.get('join_no_match_reject')) else 'off'}",
            f"reject-word-block: {'on' if bool(config.get('reject_word_block')) else 'off'}",
            f'blacklist ({len(block_ids)}): {block_text}',
            f"min-level: {config.get('join_min_level', 0)}",
            f"max-attempts: {config.get('join_max_time', 0)}",
            f'welcome: {welcome}',
            f"join-ban: {config.get('join_ban_time', 0)}",
        ]
    )


def _format_group_admin_curfew_summary(response: LuteResponse, *, fallback_group_id: str) -> str:
    extracted = _extract_group_admin_config_payload(response)
    if extracted is None:
        return response.text.strip()
    group_id, config = extracted
    current_group_id = group_id or fallback_group_id
    return '\n'.join(
        [
            f'Curfew config for current group {current_group_id}',
            f"enabled: {'on' if bool(config.get('curfew_enabled')) else 'off'}",
            f"start: {str(config.get('curfew_start') or '').strip()}",
            f"end: {str(config.get('curfew_end') or '').strip()}",
        ]
    )


async def _dispatch_comic(invocation: LuteInvocation, verb_name: str) -> LuteResponse:
    platform = verb_name.strip().lower()
    if platform != 'jm':
        return LuteResponse(text=f'Usage: /{invocation.root} comic <platform> <verb> ...\nSupported platforms: jm')

    subverb = str(invocation.args[0]).strip().lower() if invocation.args else ''
    subargs = invocation.args[1:]
    if not subverb:
        return LuteResponse(text=f'Usage: /{invocation.root} comic jm <search|album|rank|download> ...')

    if subverb == 'search':
        query = ' '.join(subargs).strip()
        if not query:
            return LuteResponse(text=f'Usage: /{invocation.root} comic jm search <关键词>')
        return await call_script_backend(
            [_qqbot_script_python(), str(JM_SCRIPT), 'search', query, '--page', '1', '--json'],
            allowed_executables={str(JM_SCRIPT)},
        )

    if subverb == 'album':
        album_id = str(subargs[0]).strip() if subargs else ''
        if not album_id:
            return LuteResponse(text=f'Usage: /{invocation.root} comic jm album <album_id>')
        return await call_script_backend(
            [_qqbot_script_python(), str(JM_SCRIPT), 'album', album_id, '--json'],
            allowed_executables={str(JM_SCRIPT)},
        )

    if subverb == 'rank':
        period = str(subargs[0]).strip().lower() if subargs else 'day'
        if period not in {'day', 'month'}:
            return LuteResponse(text=f'Usage: /{invocation.root} comic jm rank [day|month]')
        return await call_script_backend(
            [_qqbot_script_python(), str(JM_SCRIPT), 'rank', period, '--page', '1', '--json'],
            allowed_executables={str(JM_SCRIPT)},
        )

    if subverb == 'download':
        album_id = str(subargs[0]).strip() if subargs else ''
        if not album_id:
            return LuteResponse(text=f'Usage: /{invocation.root} comic jm download <album_id> [--pack zip|pdf]')
        command = [_qqbot_script_python(), str(JM_SCRIPT), 'download', album_id]
        if 'pack' in invocation.options:
            command.extend(['--pack-format', str(invocation.options['pack'])])
        command.append('--json')
        return await call_script_backend(command, allowed_executables={str(JM_SCRIPT)})

    return LuteResponse(text=f'Usage: /{invocation.root} comic jm <search|album|rank|download> ...')


def _bangumi_search_usage(root: str) -> str:
    return render_usage_list(
        [
            LuteUsageEntry(
                f'/{root} bangumi search [<关键词>] [--tag <标签>] [--meta-tag <公共标签>] [--year <年份>] [--image]',
                '搜索番剧；至少提供一个关键词或筛选条件',
            )
        ]
    )


def _bangumi_subject_usage(root: str) -> str:
    return render_usage_list(
        [
            LuteUsageEntry(f'/{root} bangumi subject <subject_id> [more_ids...] [--image]', '查看一个或多个条目详情，可选输出长图'),
        ]
    )


def _feed_fetch_usage(root: str) -> str:
    return render_usage_list(
        [
            LuteUsageEntry(f'/{root} feed fetch <subscription_id>', '抓取指定订阅，也可传已存在订阅标题/标签进行匹配'),
        ]
    )


def _feed_add_usage(root: str) -> str:
    return render_usage_list(
        [
            LuteUsageEntry(f'/{root} feed add <url> [--title 标题]', '添加订阅，可选附加标题/间隔/标签'),
        ]
    )


def _feed_remove_usage(root: str) -> str:
    return render_usage_list(
        [
            LuteUsageEntry(f'/{root} feed remove <subscription_id>', '删除指定订阅'),
        ]
    )


def _pixiv_search_usage(root: str) -> str:
    return render_usage_list(
        [
            LuteUsageEntry(f'/{root} pixiv search <关键词>', '搜索 Pixiv 作品'),
        ]
    )


def _pixiv_illust_usage(root: str) -> str:
    return render_usage_list(
        [
            LuteUsageEntry(f'/{root} pixiv illust <illust_id>', '查看作品详情'),
        ]
    )


def _pixiv_related_usage(root: str) -> str:
    return render_usage_list(
        [
            LuteUsageEntry(f'/{root} pixiv related <illust_id>', '查看相关作品'),
        ]
    )


def _pixiv_download_usage(root: str) -> str:
    return render_usage_list(
        [
            LuteUsageEntry(f'/{root} pixiv download <illust_id>', '下载指定作品'),
        ]
    )


def _torrent_search_usage(root: str) -> str:
    return render_usage_list(
        [
            LuteUsageEntry(
                f'/{root} torrent search <关键词> [--season 1] [--episode 5] [--quality 1080p] [--language en]',
                '使用 TorrentClaw 搜索资源',
            ),
        ]
    )


def _torrent_stream_usage(root: str) -> str:
    return render_usage_list(
        [
            LuteUsageEntry(f'/{root} torrent stream <content_id> [--country US]', '查看指定条目的可观看平台'),
        ]
    )


def _torrent_fallback_usage(root: str) -> str:
    return render_usage_list(
        [
            LuteUsageEntry(f'/{root} torrent fallback <关键词>', '显式调用备用搜索后端'),
        ]
    )


def _torrent_analyze_usage(root: str) -> str:
    return render_usage_list(
        [
            LuteUsageEntry(f'/{root} torrent analyze <magnet_or_hash>', '分析磁链或 BTIH 哈希'),
        ]
    )


def _book_search_usage(root: str) -> str:
    return render_usage_list(
        [
            LuteUsageEntry(f'/{root} book search <关键词>', '搜索电子书资源'),
        ]
    )


def _book_metadata_usage(root: str) -> str:
    return render_usage_list(
        [
            LuteUsageEntry(f'/{root} book metadata <book_id> --hash <book_hash>', '查看电子书元数据'),
        ]
    )


def _response_with_existing_image_path(response: LuteResponse) -> LuteResponse:
    if response.media_paths:
        return response
    text = str(response.text or '').strip()
    if not text:
        return response
    path = Path(text)
    if path.exists() and path.suffix.lower() in {'.png', '.jpg', '.jpeg', '.webp', '.gif'}:
        return LuteResponse(
            text='Image ready.',
            media_paths=[str(path)],
            file_paths=response.file_paths,
            payload=response.payload,
            telemetry_events=list(response.telemetry_events),
        )
    return response



def _response_with_existing_image_view(response: LuteResponse, *, domain: str, verb: str, template: str) -> LuteResponse:
    image_ready = _response_with_existing_image_path(response)
    if image_ready.view is not None:
        return image_ready
    if not image_ready.media_paths:
        return image_ready
    image_paths = [str(path).strip() for path in image_ready.media_paths if str(path).strip()]
    if not image_paths:
        return image_ready
    return LuteResponse(
        text=str(image_ready.text or '').strip(),
        file_paths=list(image_ready.file_paths),
        payload=image_ready.payload,
        telemetry_events=list(image_ready.telemetry_events),
        view=ViewSpec(
            kind='image',
            template=template,
            data={'image_path': image_paths[0], 'image_paths': image_paths},
            fallback_text=str(image_ready.text or '').strip() or 'Image ready.',
            cache_policy=ViewCachePolicy(namespace=f'{domain}-rendered'),
            telemetry_tags={'domain': domain, 'verb': verb},
        ),
    )


def _format_pixiv_user_payload(payload: dict[str, Any] | list[Any] | None) -> str | None:
    if not isinstance(payload, dict):
        return None
    previews = payload.get('user_previews')
    if not isinstance(previews, list):
        return None
    lines: list[str] = []
    for idx, item in enumerate(previews[:10], start=1):
        if not isinstance(item, dict):
            continue
        user = item.get('user') or {}
        if not isinstance(user, dict):
            continue
        name = str(user.get('name') or '').strip()
        account = str(user.get('account') or '').strip()
        user_id = user.get('id')
        if name or account or user_id:
            lines.append(f"{idx}. {name or '-'} (@{account or '-'}) ID={user_id}")
    if not lines:
        return None
    return '\n'.join(lines)


def _unwrap_nested_json_text(payload: dict[str, Any] | list[Any] | None) -> str | None:
    if not isinstance(payload, dict):
        return None
    raw = payload.get('result')
    if not isinstance(raw, str):
        return None
    try:
        nested = json.loads(raw)
    except Exception:
        return None
    return json.dumps(nested, ensure_ascii=False, indent=2)


def _default_group_id(invocation: LuteInvocation) -> str | None:
    if str(invocation.current_chat_type).strip().lower() == 'group' and str(invocation.current_chat_id).strip():
        return str(invocation.current_chat_id).strip()
    return None


def _ensure_group_scope(invocation: LuteInvocation, group_id: str | None) -> str | None:
    current_group_id = _default_group_id(invocation)
    if not current_group_id:
        return None
    target = str(group_id or '').strip()
    if target and target != current_group_id:
        return 'Cross-group operations are not allowed from this group chat.'
    return None


def _default_reply_message_id(invocation: LuteInvocation) -> str | None:
    reply_id = str(invocation.reply_to_message_id).strip()
    return reply_id or None


def _first_referenced_media_path(invocation: LuteInvocation, *, media_type: str) -> str | None:
    urls = list(getattr(invocation, 'referenced_media_urls', None) or [])
    types = list(getattr(invocation, 'referenced_media_types', None) or [])
    normalized_target = str(media_type).strip().lower()
    for idx, url in enumerate(urls):
        candidate = str(url).strip()
        if not candidate:
            continue
        current_type = str(types[idx]).strip().lower() if idx < len(types) else ''
        if current_type == normalized_target:
            return candidate
    if normalized_target == 'image' and urls and not types:
        fallback = str(urls[0]).strip()
        return fallback or None
    return None


def _render_backend_response(response: LuteResponse, *, view_config: QQBotViewConfig | None = None) -> str:
    """Compatibility bridge for legacy string-return QQBot paths.

    Canonical live QQBot adapter delivery should return structured `LuteResponse`
    objects into `GatewayRunner._send_gateway_response()`. This helper exists so
    older non-structured paths can still render marker strings like IMAGE_PATH=
    and FILE_PATH= when the caller expects a plain string contract.
    """
    view_config = view_config or QQBotViewConfig()
    text_value = str(response.text or '').strip()
    media_paths = list(response.media_paths)
    file_paths = list(response.file_paths)

    if response.view is not None:
        if getattr(view_config, 'enabled', True):
            try:
                from gateway.view.cache import ViewRenderCache
                from gateway.view.renderers import get_default_renderer_registry

                cache_config = getattr(view_config, 'cache', None)
                cache = None
                if getattr(cache_config, 'enabled', True):
                    cache_root = getattr(cache_config, 'root', None)
                    cache = ViewRenderCache(root=Path(cache_root) if cache_root else None)
                rendered = get_default_renderer_registry().render(response.view, cache=cache)
                if rendered.text_chunks:
                    text_value = '\n\n'.join(chunk for chunk in rendered.text_chunks if str(chunk).strip()).strip()
                if rendered.media_paths:
                    media_paths.extend(rendered.media_paths)
                if rendered.file_paths:
                    file_paths.extend(rendered.file_paths)
            except ModuleNotFoundError:
                view_data = getattr(response.view, 'data', {}) or {}
                if isinstance(view_data, dict):
                    direct_image = str(view_data.get('image_path') or '').strip()
                    if direct_image:
                        media_paths.append(direct_image)
                    more_images = view_data.get('image_paths') or []
                    if isinstance(more_images, list):
                        for item in more_images:
                            item_text = str(item).strip()
                            if item_text and item_text not in media_paths:
                                media_paths.append(item_text)
                if not text_value:
                    text_value = str(getattr(response.view, 'fallback_text', '') or '').strip()
        elif not text_value:
            text_value = str(getattr(response.view, 'fallback_text', '') or '').strip()

    lines: list[str] = []
    if text_value:
        lines.append(text_value)
    for path in media_paths:
        lines.append(f"IMAGE_PATH={path}")
    for path in file_paths:
        lines.append(f"FILE_PATH={path}")
    return "\n".join(lines).strip()


def _parse_required_int_arg(invocation: LuteInvocation, *, usage: str) -> int | None:
    if not invocation.args:
        return None
    try:
        return int(str(invocation.args[0]))
    except (TypeError, ValueError):
        return None


def _render_core_status(policy: QQBotPolicy, user_id: str | None, cli: QQBotCLIConfig) -> str:
    role = "admin" if policy.is_admin(user_id) else "user"
    lines = [
        "Lute status",
        f"role: {role}",
        f"root_command: /{cli.root_command}",
        f"legacy_bare_commands: {'enabled' if cli.enable_legacy_bare_commands else 'disabled'}",
        f"predefined_commands: {'yes' if policy.can_use_predefined_commands(user_id) else 'no'}",
        f"llm: {'yes' if policy.can_use_llm(user_id) else 'no'}",
    ]
    return "\n".join(lines)


def _dispatch_system_admin(
    invocation: LuteInvocation,
    verb_name: str,
    *,
    stats_provider: Callable[[LuteInvocation], str] | None = None,
    logs_provider: Callable[[LuteInvocation], str] | None = None,
) -> LuteDispatchResult:
    if verb_name in {"status", "show"}:
        if invocation.args:
            return LuteDispatchResult(text=_system_status_usage(invocation.root))
        return LuteDispatchResult(admin_command_text="/admin status")

    if verb_name == "runtime":
        action = str(invocation.args[0]).strip().lower() if invocation.args else ""
        if len(invocation.args) != 1 or action not in {"show", "status"}:
            return LuteDispatchResult(text=_system_runtime_usage(invocation.root))
        return LuteDispatchResult(admin_command_text=f"/admin runtime {action}")

    if verb_name == "stats":
        if not _is_valid_system_stats_args(invocation.args):
            return LuteDispatchResult(text=_system_stats_usage(invocation.root))
        if stats_provider is None:
            return LuteDispatchResult(text='QQBot view stats are unavailable in this runtime.')
        return LuteDispatchResult(text=stats_provider(invocation))

    if verb_name == 'logs':
        if not _is_valid_system_logs_args(invocation.args):
            return LuteDispatchResult(text=render_single_usage(f'/{invocation.root} system logs [limit]'))
        if logs_provider is None:
            return LuteDispatchResult(text='QQBot view logs are unavailable in this runtime.')
        return LuteDispatchResult(text=logs_provider(invocation))

    if verb_name == "reload":
        if invocation.args:
            return LuteDispatchResult(text=render_single_usage(f'/{invocation.root} system reload'))
        return LuteDispatchResult(admin_command_text="/admin reload")

    return LuteDispatchResult(text=f"Unknown /{invocation.root} verb: {verb_name}\nTry: /{invocation.root} help system")



def _dispatch_config_admin(invocation: LuteInvocation, verb_name: str) -> LuteDispatchResult:
    if verb_name == "llm":
        action = str(invocation.args[0]).strip().lower() if invocation.args else ""
        if len(invocation.args) != 2 or action not in {"grant", "revoke"}:
            return LuteDispatchResult(text=_config_llm_usage(invocation.root))
        return LuteDispatchResult(admin_command_text=f"/admin llm {action} {str(invocation.args[1]).strip()}")

    if verb_name == "allow":
        args = [str(arg).strip() for arg in invocation.args if str(arg).strip()]
        if _is_valid_config_allow_args(args):
            return LuteDispatchResult(admin_command_text=f"/admin allow {' '.join(args)}".strip())
        return LuteDispatchResult(text=_config_allow_usage(invocation.root))

    if verb_name == "feature":
        args = [str(arg).strip() for arg in invocation.args if str(arg).strip()]
        if _is_valid_config_feature_args(args):
            return LuteDispatchResult(admin_command_text=f"/admin feature {' '.join(args)}".strip())
        return LuteDispatchResult(text=_config_feature_usage(invocation.root))

    return LuteDispatchResult(text=f"Unknown /{invocation.root} verb: {verb_name}\nTry: /{invocation.root} help config")



def _is_valid_config_allow_args(args: list[str]) -> bool:
    normalized = [arg.lower() for arg in args]
    if normalized == ["group"]:
        return True
    if normalized == ["group", "remove"]:
        return True
    if len(normalized) == 3:
        target_kind, action, _ = normalized
        if target_kind in {"user", "group"} and action in {"add", "remove"}:
            return True
        if target_kind == "group-user" and action in {"add", "remove"}:
            return True
        return False
    if len(normalized) == 4:
        target_kind, action, _, _ = normalized
        return target_kind == "group-user" and action in {"add", "remove"}
    return False


def _is_valid_config_feature_args(args: list[str]) -> bool:
    normalized = [arg.lower() for arg in args]
    if normalized in ([], ["list"]):
        return True
    return len(normalized) == 2 and normalized[0] in {"enable", "disable"} and bool(normalized[1])



def _system_status_usage(root: str) -> str:
    return render_usage_list(
        [
            LuteUsageEntry(f'/{root} system show', '查看 QQBot 系统状态'),
            LuteUsageEntry(f'/{root} system status', '查看 QQBot 系统状态（show 别名）'),
        ]
    )



def _system_runtime_usage(root: str) -> str:
    return render_usage_list(
        [
            LuteUsageEntry(f'/{root} system runtime show', '查看 QQBot runtime 配置'),
            LuteUsageEntry(f'/{root} system runtime status', '查看 QQBot runtime 配置（show 别名）'),
        ]
    )


def _is_valid_system_stats_args(args: list[str]) -> bool:
    normalized = [str(arg).strip().lower() for arg in args if str(arg).strip()]
    if normalized in ([], ['show'], ['api']):
        return True
    return len(normalized) == 2 and normalized[0] == 'domain' and bool(normalized[1])


def _system_stats_usage(root: str) -> str:
    return render_usage_list(
        [
            LuteUsageEntry(f'/{root} system stats show', '查看 QQBot view/delivery 统计总览'),
            LuteUsageEntry(f'/{root} system stats domain <domain>', '查看指定 Lute domain 的调用统计'),
            LuteUsageEntry(f'/{root} system stats api', '查看外部 API 调用统计'),
        ]
    )


def _is_valid_system_logs_args(args: list[str]) -> bool:
    if not args:
        return True
    if len(args) != 1:
        return False
    candidate = str(args[0]).strip()
    return candidate.isdigit() and int(candidate) > 0



def _config_llm_usage(root: str) -> str:
    return render_usage_list(
        [
            LuteUsageEntry(f'/{root} config llm grant <qq_id>', '授予指定 QQ 号 LLM 权限'),
            LuteUsageEntry(f'/{root} config llm revoke <qq_id>', '撤销指定 QQ 号 LLM 权限'),
        ]
    )



def _config_allow_usage(root: str) -> str:
    return render_usage_list(
        [
            LuteUsageEntry(f'/{root} config allow user add <qq_id>', '将指定 QQ 用户加入 allowlist'),
            LuteUsageEntry(f'/{root} config allow user remove <qq_id>', '将指定 QQ 用户移出 allowlist'),
            LuteUsageEntry(f'/{root} config allow group add <group_id>', '将指定群加入 allowlist'),
            LuteUsageEntry(f'/{root} config allow group remove <group_id>', '将指定群移出 allowlist'),
            LuteUsageEntry(f'/{root} config allow group', '将当前群加入 allowlist'),
            LuteUsageEntry(f'/{root} config allow group remove', '将当前群移出 allowlist'),
            LuteUsageEntry(f'/{root} config allow group-user add <qq_id>', '将当前群内指定用户加入 group_user_allowlist'),
            LuteUsageEntry(f'/{root} config allow group-user remove <qq_id>', '将当前群内指定用户移出 group_user_allowlist'),
            LuteUsageEntry(f'/{root} config allow group-user add <group_id> <qq_id>', '将指定群内指定用户加入 group_user_allowlist'),
            LuteUsageEntry(f'/{root} config allow group-user remove <group_id> <qq_id>', '将指定群内指定用户移出 group_user_allowlist'),
        ]
    )


def _config_feature_usage(root: str) -> str:
    return render_usage_list(
        [
            LuteUsageEntry(f'/{root} config feature list', '查看普通用户功能开关'),
            LuteUsageEntry(f'/{root} config feature enable <domain|module|all>', '对普通用户开启指定功能或模块'),
            LuteUsageEntry(f'/{root} config feature disable <domain|module|all>', '对普通用户关闭指定功能或模块'),
        ]
    )



def rewrite_admin_message_for_lute(invocation: LuteInvocation, message: str | None, cli: QQBotCLIConfig | None = None) -> str | None:
    if message is None:
        return None
    cli = cli or QQBotCLIConfig()

    replacements = {
        "/admin status": f"/{cli.root_command} system status",
        "/admin runtime show": f"/{cli.root_command} system runtime show",
        "/admin reload": f"/{cli.root_command} system reload",
        "/admin allow": f"/{cli.root_command} config allow",
        "/admin llm": f"/{cli.root_command} config llm",
        "/admin feature": f"/{cli.root_command} config feature",
    }
    rewritten = message
    for old, new in replacements.items():
        rewritten = rewritten.replace(old, new)
    return rewritten
