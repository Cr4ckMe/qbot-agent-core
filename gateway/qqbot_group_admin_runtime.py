from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from agent.auxiliary_client import async_call_llm
from gateway.qqbot_group_admin_store import QQBotGroupAdminStore

logger = logging.getLogger(__name__)

_ActionExecutor = Callable[[str, dict[str, Any]], Awaitable[Any]]

_DEFAULT_BUILTIN_LEXICON = (
    Path(__file__).resolve().parents[1]
    / 'temp'
    / 'astrbot_plugin_qqadmin'
    / 'SensitiveLexicon.json'
)


@dataclass
class QQBotGroupAdminRuntimeResult:
    handled: bool = False
    actions: list[str] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)


class QQBotGroupAdminRuntime:
    def __init__(
        self,
        store: QQBotGroupAdminStore | None = None,
        *,
        action_executor: _ActionExecutor | None = None,
        builtin_words: list[str] | None = None,
        operator_admin_ids: set[str] | None = None,
        self_id: str = '',
        spamming_count: int = 5,
        spamming_interval: float = 0.5,
        now_provider: Callable[[], str] | None = None,
        curfew_poll_seconds: float = 1.0,
        autostart_background_tasks: bool | None = None,
        llm_provider: str = '',
        llm_model: str = '',
    ):
        self.store = store or QQBotGroupAdminStore()
        self.closed = False
        self._last_raw_payload: dict[str, Any] | None = None
        self._default_action_executor = action_executor
        self._builtin_words = builtin_words if builtin_words is not None else self._load_builtin_words()
        self.operator_admin_ids = {str(item).strip() for item in (operator_admin_ids or set()) if str(item).strip()}
        self.self_id = str(self_id or '').strip()
        self.spamming_count = max(2, int(spamming_count or 5))
        self.spamming_interval = float(spamming_interval or 0.5)
        self._now_provider = now_provider or self._default_now_provider
        self._curfew_poll_seconds = max(0.2, float(curfew_poll_seconds or 1.0))
        self.llm_provider = str(llm_provider or '').strip()
        self.llm_model = str(llm_model or '').strip()
        self._curfew_active: dict[str, tuple[str, str]] = {}
        self._curfew_task: asyncio.Task | None = None
        self._curfew_lock = asyncio.Lock()
        self._vote_tasks: dict[str, asyncio.Task] = {}
        self._vote_lock = asyncio.Lock()
        self._msg_timestamps: dict[str, dict[str, deque[float]]] = defaultdict(
            lambda: defaultdict(lambda: deque(maxlen=self.spamming_count))
        )
        self._last_banned_time: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        self._join_fail_counts: dict[str, int] = {}
        should_autostart = (now_provider is None) if autostart_background_tasks is None else bool(autostart_background_tasks)
        if should_autostart:
            self._ensure_curfew_task_started()

    @staticmethod
    def _load_builtin_words() -> list[str]:
        try:
            payload = json.loads(_DEFAULT_BUILTIN_LEXICON.read_text(encoding='utf-8'))
            words = payload.get('words') if isinstance(payload, dict) else None
            if isinstance(words, list):
                return [str(item).strip() for item in words if str(item).strip()]
        except Exception:
            logger.debug('qqadmin builtin lexicon unavailable: %s', _DEFAULT_BUILTIN_LEXICON)
        return []

    def get_group_config(self, group_id: str) -> dict[str, Any]:
        return self.store.get_group_config(group_id)

    def update_group_config(self, group_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        return self.store.update_group_config(group_id, updates)

    def reset_group_config(self, group_id: str) -> dict[str, Any]:
        return self.store.reset_group_config(group_id)

    def reset_all_groups(self) -> int:
        return self.store.reset_all_groups()

    def get_active_vote_mute(self, group_id: str) -> dict[str, Any] | None:
        session = self.store.get_vote_session(group_id)
        if not isinstance(session, dict):
            return None
        return self._normalize_vote_session(group_id, session)

    async def start_vote_mute(
        self,
        group_id: str,
        target_user_id: str,
        *,
        ban_time: int | None = None,
        action_executor: _ActionExecutor | None = None,
    ) -> dict[str, Any]:
        gid = str(group_id or '').strip()
        target_id = str(target_user_id or '').strip()
        if not gid or not target_id:
            raise ValueError('group_id and target_user_id are required')
        if self.self_id and target_id == self.self_id:
            return {'success': False, 'group_id': gid, 'target_user_id': target_id, 'text': '不能对机器人自己发起禁言投票'}
        if target_id in self.operator_admin_ids:
            return {'success': False, 'group_id': gid, 'target_user_id': target_id, 'text': '不能对 Hermes 管理员发起禁言投票'}

        existing = self.get_active_vote_mute(gid)
        if existing is not None:
            return {
                'success': False,
                'group_id': gid,
                'target_user_id': str(existing.get('target_user_id') or ''),
                'ban_time': int(existing.get('ban_time') or 0),
                'threshold': int(existing.get('threshold') or 0),
                'ttl': int(max(0, int(existing.get('expire_at') or 0) - int(time.time()))),
                'text': '群内已有正在进行的禁言投票',
            }

        cfg = self.get_group_config(gid)
        chosen_ban_time = self._pick_vote_ban_time(cfg, ban_time)
        ttl = max(1, int(cfg.get('vote_ban_ttl') or 120))
        threshold = max(2, int(cfg.get('vote_ban_threshold') or 3))
        now_ts = int(time.time())
        expire_at = now_ts + ttl
        target_name = await self._get_vote_target_name(gid, target_id, action_executor or self._default_action_executor)
        session = {
            'group_id': gid,
            'target_user_id': target_id,
            'target_name': target_name,
            'ban_time': chosen_ban_time,
            'created_at': now_ts,
            'expire_at': expire_at,
            'threshold': threshold,
            'votes': {},
        }
        self.store.save_vote_session(gid, session)
        self._schedule_vote_settlement(gid, expire_at)
        return {
            'success': True,
            'group_id': gid,
            'target_user_id': target_id,
            'target_name': target_name,
            'ban_time': chosen_ban_time,
            'threshold': threshold,
            'ttl': ttl,
            'text': f'已发起对 {target_name} 的禁言投票(禁言{chosen_ban_time}秒)，输入“赞同禁言/反对禁言”进行表态，{ttl}秒后结算',
        }

    async def cast_vote_mute(
        self,
        group_id: str,
        voter_user_id: str,
        *,
        agree: bool,
        action_executor: _ActionExecutor | None = None,
    ) -> dict[str, Any]:
        gid = str(group_id or '').strip()
        voter_id = str(voter_user_id or '').strip()
        if not gid or not voter_id:
            raise ValueError('group_id and voter_user_id are required')
        session = self.get_active_vote_mute(gid)
        if session is None:
            return {'success': False, 'group_id': gid, 'voter_user_id': voter_id, 'agree': bool(agree), 'text': '当前没有进行中的禁言投票'}
        if int(time.time()) >= int(session.get('expire_at') or 0):
            settled = await self._settle_vote_session(gid, action_executor or self._default_action_executor, force_timeout=True)
            return {
                'success': bool(settled.get('success')),
                'group_id': gid,
                'voter_user_id': voter_id,
                'agree': bool(agree),
                'text': str(settled.get('text') or '当前没有进行中的禁言投票'),
            }

        votes = self._normalize_vote_map(session.get('votes'))
        votes[voter_id] = bool(agree)
        session['votes'] = votes
        self.store.save_vote_session(gid, session)

        agree_count, disagree_count = self._count_vote_result(votes)
        threshold = int(session.get('threshold') or 0)
        target_name = str(session.get('target_name') or session.get('target_user_id') or '')
        if agree_count >= threshold:
            settled = await self._settle_vote_session(gid, action_executor or self._default_action_executor, force_result=True)
            return {
                'success': bool(settled.get('success')),
                'group_id': gid,
                'voter_user_id': voter_id,
                'agree': bool(agree),
                'text': str(settled.get('text') or f'投票通过！已禁言{target_name}'),
            }
        if disagree_count >= threshold:
            self.store.clear_vote_session(gid)
            self._cancel_vote_settlement(gid)
            return {
                'success': True,
                'group_id': gid,
                'voter_user_id': voter_id,
                'agree': bool(agree),
                'text': f'禁言投票被否决，{target_name}安全了',
            }
        return {
            'success': True,
            'group_id': gid,
            'voter_user_id': voter_id,
            'agree': bool(agree),
            'agree_count': agree_count,
            'disagree_count': disagree_count,
            'threshold': threshold,
            'text': f'禁言【{target_name}】：\n赞同({agree_count}/{threshold})\n反对({disagree_count}/{threshold})',
        }

    async def run_vote_settlement_once(self) -> list[str]:
        now_ts = int(time.time())
        settled_groups: list[str] = []
        for group_id, session in self.store.list_vote_sessions().items():
            expire_at = int((session or {}).get('expire_at') or 0)
            if expire_at <= 0 or expire_at > now_ts:
                continue
            result = await self._settle_vote_session(group_id, self._default_action_executor, force_timeout=True)
            if result.get('settled'):
                settled_groups.append(group_id)
        return settled_groups

    @staticmethod
    def _normalize_vote_map(value: Any) -> dict[str, bool]:
        if not isinstance(value, dict):
            return {}
        result: dict[str, bool] = {}
        for key, item in value.items():
            user_id = str(key or '').strip()
            if not user_id:
                continue
            result[user_id] = bool(item)
        return result

    def _normalize_vote_session(self, group_id: str, session: dict[str, Any]) -> dict[str, Any] | None:
        if not isinstance(session, dict):
            return None
        gid = str(group_id or session.get('group_id') or '').strip()
        target_user_id = str(session.get('target_user_id') or session.get('target') or '').strip()
        if not gid or not target_user_id:
            return None
        threshold = max(2, int(session.get('threshold') or 3))
        ban_time = max(1, int(session.get('ban_time') or 60))
        created_at = int(session.get('created_at') or 0)
        expire_at = int(session.get('expire_at') or session.get('expire') or 0)
        if expire_at <= 0:
            return None
        return {
            'group_id': gid,
            'target_user_id': target_user_id,
            'target_name': str(session.get('target_name') or target_user_id),
            'ban_time': ban_time,
            'threshold': threshold,
            'created_at': created_at,
            'expire_at': expire_at,
            'votes': self._normalize_vote_map(session.get('votes')),
        }

    @staticmethod
    def _count_vote_result(votes: dict[str, bool]) -> tuple[int, int]:
        values = list(votes.values())
        agree_count = sum(1 for item in values if item)
        disagree_count = len(values) - agree_count
        return agree_count, disagree_count

    def _pick_vote_ban_time(self, cfg: dict[str, Any], ban_time: int | None) -> int:
        if isinstance(ban_time, int) and ban_time > 0:
            return ban_time
        text = str(cfg.get('vote_ban_random_range') or '30~300').strip()
        if '~' in text:
            left, right = text.split('~', 1)
            try:
                start = max(1, int(left.strip() or 30))
                end = max(start, int(right.strip() or start))
                return random.randint(start, end)
            except Exception:
                pass
        try:
            return max(1, int(text or 60))
        except Exception:
            return 60

    async def _get_vote_target_name(self, group_id: str, target_user_id: str, executor: _ActionExecutor | None) -> str:
        if executor is None:
            return target_user_id
        try:
            member_info = await executor(
                'get_group_member_info',
                {'group_id': int(group_id), 'user_id': int(target_user_id), 'no_cache': False},
            )
        except Exception:
            return target_user_id
        return str((member_info or {}).get('card') or (member_info or {}).get('nickname') or target_user_id)

    def _schedule_vote_settlement(self, group_id: str, expire_at: int) -> None:
        self._cancel_vote_settlement(group_id)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        delay = max(0.0, float(expire_at - int(time.time())))
        self._vote_tasks[group_id] = loop.create_task(self._vote_settlement_task(group_id, delay))

    def _cancel_vote_settlement(self, group_id: str) -> None:
        task = self._vote_tasks.pop(str(group_id).strip(), None)
        if task is not None and task is not asyncio.current_task():
            task.cancel()

    async def _vote_settlement_task(self, group_id: str, delay: float) -> None:
        try:
            await asyncio.sleep(delay)
            result = await self._settle_vote_session(group_id, self._default_action_executor, force_timeout=True)
            if result.get('settled') and result.get('text') and self._default_action_executor is not None:
                await self._default_action_executor(
                    'send_group_msg',
                    {'group_id': int(group_id), 'message': str(result['text'])},
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning('qqadmin vote settlement failed for %s: %s', group_id, exc)
        finally:
            self._vote_tasks.pop(str(group_id).strip(), None)

    async def _settle_vote_session(
        self,
        group_id: str,
        executor: _ActionExecutor | None,
        *,
        force_timeout: bool = False,
        force_result: bool = False,
    ) -> dict[str, Any]:
        gid = str(group_id or '').strip()
        async with self._vote_lock:
            session = self.get_active_vote_mute(gid)
            if session is None:
                self._cancel_vote_settlement(gid)
                return {'success': False, 'settled': False, 'group_id': gid, 'text': '当前没有进行中的禁言投票'}
            if not force_result and not force_timeout and int(time.time()) < int(session.get('expire_at') or 0):
                return {'success': False, 'settled': False, 'group_id': gid, 'text': '当前投票尚未到期'}
            self.store.clear_vote_session(gid)
            self._cancel_vote_settlement(gid)

        votes = self._normalize_vote_map(session.get('votes'))
        agree_count, disagree_count = self._count_vote_result(votes)
        target_name = str(session.get('target_name') or session.get('target_user_id') or '')
        if agree_count > disagree_count or force_result:
            if executor is not None:
                await executor(
                    'set_group_ban',
                    {
                        'group_id': int(gid),
                        'user_id': int(session['target_user_id']),
                        'duration': int(session['ban_time']),
                    },
                )
            self._last_banned_time[gid][str(session['target_user_id'])] = time.time()
            return {
                'success': True,
                'settled': True,
                'group_id': gid,
                'approved': True,
                'text': f'投票通过！已禁言{target_name}' if force_result else f'投票时间到！已禁言{target_name}',
            }
        return {
            'success': True,
            'settled': True,
            'group_id': gid,
            'approved': False,
            'text': f'投票时间到！禁言被否决，{target_name}安全了',
        }

    async def preview_member_cleanup(
        self,
        group_id: str,
        *,
        inactive_days: int = 30,
        max_level: int = 1,
        action_executor: _ActionExecutor | None = None,
    ) -> dict[str, Any]:
        gid = str(group_id or '').strip()
        if not gid:
            raise ValueError('group_id is required')
        executor = action_executor or self._default_action_executor
        if executor is None:
            raise ValueError('qqadmin cleanup preview requires an action executor')
        days = max(0, int(inactive_days or 0))
        level_max = max(0, int(max_level or 0))
        members = await executor('get_group_member_list', {'group_id': int(gid)})
        candidates = self._select_cleanup_candidates(gid, members, inactive_days=days, max_level=level_max)
        text = self._format_cleanup_preview_text(days, level_max, candidates)
        return {
            'success': True,
            'group_id': gid,
            'inactive_days': days,
            'max_level': level_max,
            'count': len(candidates),
            'candidates': candidates,
            'text': text,
        }

    async def apply_member_cleanup(
        self,
        group_id: str,
        *,
        user_ids: list[str] | None = None,
        inactive_days: int = 30,
        max_level: int = 1,
        reject_add_request: bool = False,
        action_executor: _ActionExecutor | None = None,
    ) -> dict[str, Any]:
        gid = str(group_id or '').strip()
        if not gid:
            raise ValueError('group_id is required')
        requested_user_ids = self._normalize_cleanup_user_ids(user_ids)
        executor = action_executor or self._default_action_executor
        if executor is None:
            raise ValueError('qqadmin cleanup apply requires an action executor')

        preview = await self.preview_member_cleanup(
            gid,
            inactive_days=inactive_days,
            max_level=max_level,
            action_executor=executor,
        )
        candidate_map = {item['user_id']: item for item in preview['candidates']}
        if not requested_user_ids:
            requested_user_ids = [item['user_id'] for item in preview['candidates']]
        if not requested_user_ids:
            return {
                'success': True,
                'group_id': gid,
                'inactive_days': int(preview['inactive_days']),
                'max_level': int(preview['max_level']),
                'requested_user_ids': [],
                'kicked_user_ids': [],
                'kicked_count': 0,
                'failed_count': 0,
                'failures': [],
                'text': '当前没有符合条件的清理候选。',
            }
        invalid_user_ids = [user_id for user_id in requested_user_ids if user_id not in candidate_map]
        if invalid_user_ids:
            return {
                'success': False,
                'group_id': gid,
                'inactive_days': int(preview['inactive_days']),
                'max_level': int(preview['max_level']),
                'requested_user_ids': requested_user_ids,
                'invalid_user_ids': invalid_user_ids,
                'kicked_user_ids': [],
                'kicked_count': 0,
                'failed_count': 0,
                'failures': [],
                'text': '以下成员当前不在清理候选内：' + ', '.join(invalid_user_ids),
            }

        kicked_user_ids: list[str] = []
        failures: list[dict[str, str]] = []
        for user_id in requested_user_ids:
            try:
                await executor(
                    'set_group_kick',
                    {
                        'group_id': int(gid),
                        'user_id': int(user_id),
                        'reject_add_request': bool(reject_add_request),
                    },
                )
            except Exception as exc:
                failures.append({'user_id': user_id, 'error': str(exc)})
            else:
                kicked_user_ids.append(user_id)

        text = self._format_cleanup_apply_text(
            requested_user_ids=requested_user_ids,
            kicked_user_ids=kicked_user_ids,
            failures=failures,
        )
        return {
            'success': not failures,
            'group_id': gid,
            'inactive_days': int(preview['inactive_days']),
            'max_level': int(preview['max_level']),
            'requested_user_ids': requested_user_ids,
            'kicked_user_ids': kicked_user_ids,
            'kicked_count': len(kicked_user_ids),
            'failed_count': len(failures),
            'failures': failures,
            'text': text,
        }

    def _select_cleanup_candidates(
        self,
        group_id: str,
        members: Any,
        *,
        inactive_days: int,
        max_level: int,
    ) -> list[dict[str, Any]]:
        if not isinstance(members, list):
            return []
        threshold_ts = int(time.time()) - inactive_days * 86400
        candidates: list[dict[str, Any]] = []
        for member in members:
            if not isinstance(member, dict):
                continue
            user_id = str(member.get('user_id') or '').strip()
            if not user_id or self._is_cleanup_member_exempt(member, user_id):
                continue
            level = self._coerce_cleanup_level(member.get('level'))
            last_sent_time = self._coerce_cleanup_timestamp(member.get('last_sent_time'))
            if level > max_level:
                continue
            if last_sent_time > threshold_ts:
                continue
            join_time = self._coerce_cleanup_timestamp(member.get('join_time'))
            candidates.append(
                {
                    'user_id': user_id,
                    'nickname': str(member.get('nickname') or member.get('card') or user_id),
                    'card': str(member.get('card') or '').strip(),
                    'role': str(member.get('role') or '').strip().lower() or 'member',
                    'level': level,
                    'join_time': join_time,
                    'join_time_text': self._format_cleanup_time(join_time, fallback='未知'),
                    'last_sent_time': last_sent_time,
                    'last_active_text': self._format_cleanup_time(last_sent_time, fallback='从未发言'),
                }
            )
        candidates.sort(key=lambda item: (int(item['last_sent_time']), int(item['level']), item['user_id']))
        return candidates

    def _is_cleanup_member_exempt(self, member: dict[str, Any], user_id: str) -> bool:
        role = str(member.get('role') or '').strip().lower()
        if role in {'owner', 'admin'}:
            return True
        if self.self_id and user_id == self.self_id:
            return True
        if user_id in self.operator_admin_ids:
            return True
        return False

    @staticmethod
    def _coerce_cleanup_level(value: Any) -> int:
        try:
            return int(str(value or '0').strip() or 0)
        except Exception:
            return 0

    @staticmethod
    def _coerce_cleanup_timestamp(value: Any) -> int:
        try:
            return max(0, int(str(value or '0').strip() or 0))
        except Exception:
            return 0

    @staticmethod
    def _format_cleanup_time(timestamp: int, *, fallback: str) -> str:
        if int(timestamp or 0) <= 0:
            return fallback
        return time.strftime('%Y-%m-%d', time.localtime(int(timestamp)))

    @staticmethod
    def _normalize_cleanup_user_ids(user_ids: Any) -> list[str]:
        if isinstance(user_ids, str):
            raw_items = [item for item in user_ids.replace(',', ' ').split() if item]
        elif isinstance(user_ids, list):
            raw_items = [str(item).strip() for item in user_ids if str(item).strip()]
        else:
            raw_items = []
        seen: set[str] = set()
        normalized: list[str] = []
        for item in raw_items:
            if item in seen:
                continue
            seen.add(item)
            normalized.append(item)
        return normalized

    @staticmethod
    def _format_cleanup_preview_text(inactive_days: int, max_level: int, candidates: list[dict[str, Any]]) -> str:
        if not candidates:
            return f'无符合条件的群友（{inactive_days} 天未发言且群等级 ≤ {max_level}）。'
        lines = [f'共 {len(candidates)} 位候选群友符合条件（{inactive_days} 天未发言且群等级 ≤ {max_level}）：']
        for item in candidates:
            lines.append(
                f"- {item['nickname']}({item['user_id']})｜等级 {item['level']}｜最后发言 {item['last_active_text']}"
            )
        return '\n'.join(lines)

    @staticmethod
    def _format_cleanup_apply_text(
        *,
        requested_user_ids: list[str],
        kicked_user_ids: list[str],
        failures: list[dict[str, str]],
    ) -> str:
        lines = [f'已请求清理 {len(requested_user_ids)} 位群友。']
        if kicked_user_ids:
            lines.append('成功移出：' + ', '.join(kicked_user_ids))
        if failures:
            lines.append('失败：' + '; '.join(f"{item['user_id']} ({item['error']})" for item in failures))
        return '\n'.join(lines)

    async def ai_set_card(
        self,
        group_id: str,
        user_id: str,
        *,
        history_count: int = 50,
        apply: bool = False,
        action_executor: _ActionExecutor | None = None,
    ) -> dict[str, Any]:
        return await self._ai_set_name(
            group_id,
            user_id,
            history_count=history_count,
            apply=apply,
            action_executor=action_executor,
            name_kind='card',
        )

    async def ai_set_title(
        self,
        group_id: str,
        user_id: str,
        *,
        history_count: int = 50,
        apply: bool = False,
        action_executor: _ActionExecutor | None = None,
    ) -> dict[str, Any]:
        return await self._ai_set_name(
            group_id,
            user_id,
            history_count=history_count,
            apply=apply,
            action_executor=action_executor,
            name_kind='title',
        )

    async def _ai_set_name(
        self,
        group_id: str,
        user_id: str,
        *,
        history_count: int,
        apply: bool,
        action_executor: _ActionExecutor | None,
        name_kind: str,
    ) -> dict[str, Any]:
        gid = str(group_id or '').strip()
        uid = str(user_id or '').strip()
        if not gid or not uid:
            raise ValueError('group_id and user_id are required')
        executor = action_executor or self._default_action_executor
        if executor is None:
            raise ValueError('qqadmin ai naming requires an action executor')
        history_limit = max(1, min(int(history_count or 50), 200))

        member_info = await executor('get_group_member_info', {'group_id': int(gid), 'user_id': int(uid), 'no_cache': True})
        target_name = str((member_info or {}).get('card') or (member_info or {}).get('nickname') or uid)
        history_payload = await executor('get_group_msg_history', {'group_id': int(gid), 'count': history_limit})
        history_lines = self._extract_member_history_lines(history_payload, uid)
        if not history_lines:
            return {
                'success': False,
                'group_id': gid,
                'user_id': uid,
                'target_name': target_name,
                'history_count': history_limit,
                'history_line_count': 0,
                'applied': False,
                'text': '聊天记录为空',
            }

        suggested_name, reason = await self._generate_ai_name(target_name, history_lines, name_kind=name_kind)
        if not suggested_name:
            return {
                'success': False,
                'group_id': gid,
                'user_id': uid,
                'target_name': target_name,
                'history_count': history_limit,
                'history_line_count': len(history_lines),
                'applied': False,
                'text': reason or 'AI 生成失败',
            }

        applied = False
        if apply:
            if name_kind == 'card':
                await executor('set_group_card', {'group_id': int(gid), 'user_id': int(uid), 'card': suggested_name})
            else:
                await executor('set_group_special_title', {'group_id': int(gid), 'user_id': int(uid), 'special_title': suggested_name})
            applied = True

        label = '昵称' if name_kind == 'card' else '头衔'
        text = f"建议{label}：{suggested_name}\n理由：{reason}" if reason else f"建议{label}：{suggested_name}"
        if applied:
            text += f"\n已应用到 {target_name}({uid})。"
        return {
            'success': True,
            'group_id': gid,
            'user_id': uid,
            'target_name': target_name,
            'history_count': history_limit,
            'history_line_count': len(history_lines),
            'suggested_name': suggested_name,
            'reason': reason,
            'applied': applied,
            'text': text,
        }

    @staticmethod
    def _extract_member_history_lines(history_payload: Any, target_user_id: str) -> list[str]:
        data = history_payload.get('messages') if isinstance(history_payload, dict) and 'messages' in history_payload else history_payload
        messages = data if isinstance(data, list) else []
        lines: list[str] = []
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            sender = msg.get('sender') or {}
            sender_id = str(sender.get('user_id') or '').strip()
            if sender_id != str(target_user_id):
                continue
            raw = str(msg.get('raw_message') or '').strip()
            if not raw:
                segments = msg.get('message') or []
                raw = ''.join(
                    str((seg.get('data') or {}).get('text') or '')
                    for seg in segments
                    if isinstance(seg, dict) and seg.get('type') == 'text'
                ).strip()
            if raw:
                lines.append(raw)
        return lines

    async def _generate_ai_name(self, target_name: str, history_lines: list[str], *, name_kind: str) -> tuple[str | None, str]:
        label = '群昵称' if name_kind == 'card' else '群头衔'
        max_len = 8 if name_kind == 'card' else 8
        messages = [
            {
                'role': 'system',
                'content': (
                    f'你是 QQ 群{name_kind}命名助手。请根据目标群友最近的发言，为他生成一个简短的{label}。'
                    f'输出必须是 JSON，对象结构为 {{"name":"...","reason":"..."}}。'
                    f'name 需要简短、好读、不要带前后缀，不超过 {max_len} 个字符；'
                    'reason 用一句中文解释原因。不要输出 JSON 以外的任何内容。'
                ),
            },
            {
                'role': 'user',
                'content': f'目标群友：{target_name}\n最近发言：\n' + '\n'.join(history_lines[-50:]),
            },
        ]
        try:
            response = await async_call_llm(
                task='title_generation',
                provider=self.llm_provider or None,
                model=self.llm_model or None,
                messages=messages,
                temperature=0.4,
                max_tokens=120,
            )
        except Exception as exc:
            logger.warning('qqadmin ai naming failed: %s', exc)
            return None, f'AI 调用失败：{exc}'
        content = self._extract_llm_content(response)
        return self._parse_ai_name_response(content, name_kind=name_kind)

    @staticmethod
    def _extract_llm_content(response: Any) -> str:
        try:
            return str(response.choices[0].message.content or '').strip()
        except Exception:
            return ''

    @staticmethod
    def _parse_ai_name_response(content: str, *, name_kind: str) -> tuple[str | None, str]:
        text = str(content or '').strip()
        if not text:
            return None, 'AI 响应为空'
        stripped = text.strip()
        if stripped.startswith('```'):
            stripped = re.sub(r'^```(?:json)?\s*', '', stripped)
            stripped = re.sub(r'\s*```$', '', stripped)
        payload = None
        try:
            payload = json.loads(stripped)
        except Exception:
            match = re.search(r'\{.*\}', stripped, re.S)
            if match:
                try:
                    payload = json.loads(match.group(0))
                except Exception:
                    payload = None
        if isinstance(payload, dict):
            raw_name = payload.get('name')
            raw_reason = str(payload.get('reason') or '').strip()
        else:
            raw_name = stripped
            raw_reason = ''
        name = QQBotGroupAdminRuntime._sanitize_ai_name(raw_name, name_kind=name_kind)
        if not name:
            return None, '未能从 AI 响应中提取有效名称'
        return name, raw_reason

    @staticmethod
    def _sanitize_ai_name(value: Any, *, name_kind: str) -> str:
        text = str(value or '').strip()
        text = re.sub(r'[`*_#"\'\s]+', '', text)
        if name_kind == 'card':
            text = re.sub(r'[^0-9A-Za-z\u4e00-\u9fff_-]', '', text)
            return text[:8]
        text = re.sub(r'[^0-9A-Za-z\u4e00-\u9fff_-]', '', text)
        return text[:8]

    async def handle_group_message(
        self,
        event: Any,
        *,
        action_executor: _ActionExecutor | None = None,
    ) -> QQBotGroupAdminRuntimeResult:
        if self.closed or event is None:
            return QQBotGroupAdminRuntimeResult()

        source = getattr(event, 'source', None)
        if source is None or str(getattr(source, 'chat_type', '')).strip().lower() != 'group':
            return QQBotGroupAdminRuntimeResult()

        group_id = str(getattr(source, 'chat_id', '') or '').strip()
        user_id = str(getattr(source, 'user_id', '') or '').strip()
        text = str(getattr(event, 'text', '') or '').strip()
        if not group_id or not user_id or not text:
            return QQBotGroupAdminRuntimeResult()

        active_vote = self.get_active_vote_mute(group_id)
        if active_vote is not None and group_id not in self._vote_tasks:
            self._schedule_vote_settlement(group_id, int(active_vote.get('expire_at') or 0))

        vote_text = text.lstrip('/').strip()
        if vote_text in {'赞同禁言', '反对禁言'}:
            if not self._is_vote_sender_allowed(event, user_id):
                return QQBotGroupAdminRuntimeResult()
            vote_result = await self.cast_vote_mute(
                group_id,
                user_id,
                agree=(vote_text == '赞同禁言'),
                action_executor=action_executor or self._default_action_executor,
            )
            return QQBotGroupAdminRuntimeResult(
                handled=bool(vote_result.get('success') or vote_result.get('text')),
                actions=['set_group_ban'] if '已禁言' in str(vote_result.get('text') or '') else [],
                messages=[str(vote_result.get('text') or '')] if str(vote_result.get('text') or '') else [],
            )

        if getattr(event, 'get_command', None) and event.get_command():
            return QQBotGroupAdminRuntimeResult()
        if self._is_sender_exempt(event, user_id):
            return QQBotGroupAdminRuntimeResult()

        cfg = self.get_group_config(group_id)
        text_lower = text.lower()
        executor = action_executor or self._default_action_executor

        custom_words = [str(item).strip().lower() for item in cfg.get('custom_ban_words', []) if str(item).strip()]
        if custom_words and any(word in text_lower for word in custom_words):
            return await self._handle_word_hit(event, group_id, user_id, cfg, executor)

        if cfg.get('builtin_ban'):
            builtin_words = [word.lower() for word in self._builtin_words if word]
            if builtin_words and any(word in text_lower for word in builtin_words):
                return await self._handle_word_hit(event, group_id, user_id, cfg, executor)

        return await self._handle_spam(event, group_id, user_id, cfg, executor)

    def _is_sender_exempt(self, event: Any, user_id: str) -> bool:
        if self.self_id and user_id == self.self_id:
            return True
        if user_id in self.operator_admin_ids:
            return True
        raw = getattr(event, 'raw_message', None)
        sender = raw.get('sender') if isinstance(raw, dict) else None
        role = str((sender or {}).get('role') or '').strip().lower()
        return role in {'admin', 'owner'}

    def _is_vote_sender_allowed(self, event: Any, user_id: str) -> bool:
        if self.self_id and user_id == self.self_id:
            return False
        if user_id in self.operator_admin_ids:
            return True
        raw = getattr(event, 'raw_message', None)
        sender = raw.get('sender') if isinstance(raw, dict) else None
        role = str((sender or {}).get('role') or '').strip().lower()
        return role in {'admin', 'owner'}

    async def _handle_word_hit(
        self,
        event: Any,
        group_id: str,
        user_id: str,
        cfg: dict[str, Any],
        executor: _ActionExecutor | None,
    ) -> QQBotGroupAdminRuntimeResult:
        actions: list[str] = []
        message_id = self._extract_message_id(event)
        if executor and message_id is not None:
            try:
                await executor('delete_msg', {'message_id': int(message_id)})
            except Exception as exc:
                logger.warning('qqadmin recall action failed for message %s: %s', message_id, exc)
            else:
                actions.append('delete_msg')

        ban_time = int(cfg.get('word_ban_time') or 0)
        if executor and ban_time > 0:
            await executor(
                'set_group_ban',
                {'group_id': int(group_id), 'user_id': int(user_id), 'duration': ban_time},
            )
            actions.append('set_group_ban')
            self._last_banned_time[group_id][user_id] = time.time()

        return QQBotGroupAdminRuntimeResult(handled=bool(actions), actions=actions, messages=[])

    async def _handle_spam(
        self,
        event: Any,
        group_id: str,
        user_id: str,
        cfg: dict[str, Any],
        executor: _ActionExecutor | None,
    ) -> QQBotGroupAdminRuntimeResult:
        ban_time = int(cfg.get('spamming_ban_time') or 0)
        if ban_time <= 0:
            return QQBotGroupAdminRuntimeResult()

        now = time.time()
        last_banned = self._last_banned_time[group_id][user_id]
        if last_banned and (now - last_banned) < ban_time:
            return QQBotGroupAdminRuntimeResult()

        timestamps = self._msg_timestamps[group_id][user_id]
        timestamps.append(now)
        if len(timestamps) < self.spamming_count:
            return QQBotGroupAdminRuntimeResult()

        recent = list(timestamps)[-self.spamming_count :]
        intervals = [recent[idx + 1] - recent[idx] for idx in range(len(recent) - 1)]
        if not intervals or not all(delta < self.spamming_interval for delta in intervals):
            return QQBotGroupAdminRuntimeResult()

        actions: list[str] = []
        if executor:
            await executor(
                'set_group_ban',
                {'group_id': int(group_id), 'user_id': int(user_id), 'duration': ban_time},
            )
            actions.append('set_group_ban')
        self._last_banned_time[group_id][user_id] = now
        timestamps.clear()
        sender_name = str(getattr(getattr(event, 'source', None), 'user_name', '') or 'unknown').strip() or 'unknown'
        return QQBotGroupAdminRuntimeResult(
            handled=bool(actions),
            actions=actions,
            messages=[f'检测到 {sender_name}({user_id}) 刷屏，已禁言 {ban_time} 秒。'] if actions else [],
        )

    @staticmethod
    def _extract_message_id(event: Any) -> int | None:
        candidates = [getattr(event, 'message_id', None)]
        raw = getattr(event, 'raw_message', None)
        if isinstance(raw, dict):
            candidates.append(raw.get('message_id'))
        for candidate in candidates:
            text = str(candidate or '').strip()
            if text.isdigit():
                return int(text)
        return None

    async def handle_napcat_raw_event(
        self,
        payload: dict[str, Any],
        *,
        action_executor: _ActionExecutor | None = None,
    ) -> QQBotGroupAdminRuntimeResult:
        self._last_raw_payload = dict(payload or {})
        if self.closed or not isinstance(payload, dict):
            return QQBotGroupAdminRuntimeResult()

        self._ensure_curfew_task_started()
        await self.run_curfew_check_once()

        executor = action_executor or self._default_action_executor
        post_type = str(payload.get('post_type') or '').strip().lower()
        if post_type == 'request':
            return await self._handle_group_join_request(payload, executor)
        if post_type == 'notice':
            notice_type = str(payload.get('notice_type') or '').strip().lower()
            if notice_type == 'group_increase':
                return await self._handle_group_increase(payload, executor)
            if notice_type == 'group_decrease':
                return await self._handle_group_decrease(payload)
        return QQBotGroupAdminRuntimeResult()

    @staticmethod
    def _default_now_provider() -> str:
        return time.strftime('%H:%M')

    def _ensure_curfew_task_started(self) -> None:
        if self._curfew_task is not None and not self._curfew_task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._curfew_task = loop.create_task(self._curfew_monitor_loop())

    async def _curfew_monitor_loop(self) -> None:
        try:
            while not self.closed:
                await self.run_curfew_check_once()
                await asyncio.sleep(self._curfew_poll_seconds)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning('qqadmin curfew monitor failed: %s', exc)

    @staticmethod
    def _parse_hhmm(value: str) -> tuple[int, int] | None:
        text = str(value or '').strip().replace('：', ':')
        if ':' not in text:
            return None
        try:
            hour, minute = map(int, text.split(':', 1))
        except Exception:
            return None
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            return None
        return hour, minute

    def _is_now_within_curfew(self, now_hhmm: str, start_hhmm: str, end_hhmm: str) -> bool:
        now_pair = self._parse_hhmm(now_hhmm)
        start_pair = self._parse_hhmm(start_hhmm)
        end_pair = self._parse_hhmm(end_hhmm)
        if not now_pair or not start_pair or not end_pair:
            return False
        now_minutes = now_pair[0] * 60 + now_pair[1]
        start_minutes = start_pair[0] * 60 + start_pair[1]
        end_minutes = end_pair[0] * 60 + end_pair[1]
        if start_minutes == end_minutes:
            return False
        if start_minutes < end_minutes:
            return start_minutes <= now_minutes < end_minutes
        return now_minutes >= start_minutes or now_minutes < end_minutes

    async def run_curfew_check_once(self) -> list[str]:
        configs = self.store.list_group_configs()
        active_groups: list[str] = []
        now_hhmm = self._now_provider()
        for group_id, cfg in configs.items():
            enabled = bool(cfg.get('curfew_enabled'))
            start_hhmm = str(cfg.get('curfew_start') or '').strip()
            end_hhmm = str(cfg.get('curfew_end') or '').strip()
            currently_active = group_id in self._curfew_active
            in_window = enabled and self._is_now_within_curfew(now_hhmm, start_hhmm, end_hhmm)
            if enabled:
                active_groups.append(group_id)
            if enabled and in_window and not currently_active:
                await self._set_curfew_state(group_id, start_hhmm, end_hhmm, enable=True)
            elif currently_active and (not enabled or not in_window):
                _, stored_end = self._curfew_active.get(group_id, (start_hhmm, end_hhmm))
                await self._set_curfew_state(group_id, start_hhmm, stored_end or end_hhmm, enable=False)
        for group_id, (start_hhmm, end_hhmm) in list(self._curfew_active.items()):
            if group_id not in configs:
                await self._set_curfew_state(group_id, start_hhmm, end_hhmm, enable=False)
        return active_groups

    async def _set_curfew_state(self, group_id: str, start_hhmm: str, end_hhmm: str, *, enable: bool) -> None:
        executor = self._default_action_executor
        async with self._curfew_lock:
            if enable:
                if group_id in self._curfew_active:
                    return
                self._curfew_active[group_id] = (start_hhmm, end_hhmm)
            else:
                if group_id not in self._curfew_active:
                    return
                self._curfew_active.pop(group_id, None)
        if executor is None:
            return
        if enable:
            await executor('send_group_msg', {'group_id': int(group_id), 'message': f'【{start_hhmm}】本群宵禁开始！'})
            await executor('set_group_whole_ban', {'group_id': int(group_id), 'enable': True})
            return
        await executor('send_group_msg', {'group_id': int(group_id), 'message': f'【{end_hhmm}】本群宵禁结束！'})
        await executor('set_group_whole_ban', {'group_id': int(group_id), 'enable': False})

    async def _handle_group_join_request(
        self,
        payload: dict[str, Any],
        executor: _ActionExecutor | None,
    ) -> QQBotGroupAdminRuntimeResult:
        if str(payload.get('request_type') or '').strip().lower() != 'group':
            return QQBotGroupAdminRuntimeResult()
        if str(payload.get('sub_type') or '').strip().lower() != 'add':
            return QQBotGroupAdminRuntimeResult()

        group_id = str(payload.get('group_id') or '').strip()
        user_id = str(payload.get('user_id') or '').strip()
        flag = str(payload.get('flag') or '').strip()
        if not group_id or not user_id or not flag:
            return QQBotGroupAdminRuntimeResult()

        cfg = self.get_group_config(group_id)
        if not cfg.get('join_switch'):
            return QQBotGroupAdminRuntimeResult()

        stranger_info = {}
        if executor:
            try:
                stranger_info = await executor('get_stranger_info', {'user_id': int(user_id)}) or {}
            except Exception as exc:
                logger.warning('qqadmin get_stranger_info failed for %s: %s', user_id, exc)
        nickname = str(stranger_info.get('nickname') or user_id)
        user_level = None
        if not stranger_info.get('isHideQQLevel'):
            for key in ('qqLevel', 'level'):
                value = stranger_info.get(key)
                if value is None:
                    continue
                try:
                    user_level = int(value)
                    break
                except Exception:
                    continue

        approve, reason = self._evaluate_join_request(
            group_id,
            user_id,
            str(payload.get('comment') or ''),
            user_level,
        )

        messages: list[str] = []
        actions: list[str] = []
        if approve is not None and executor:
            await executor(
                'set_group_add_request',
                {
                    'flag': flag,
                    'sub_type': 'add',
                    'approve': approve,
                    'reason': '' if approve else reason,
                },
            )
            actions.append('set_group_add_request')

        tip = '批准/驳回：' if approve is None else ''
        notice = [f'【进群申请】{tip}', f'昵称：{nickname}', f'QQ：{user_id}', f'flag：{flag}']
        if user_level is not None:
            notice.append(f'等级：{user_level}')
        comment = str(payload.get('comment') or '').strip()
        if comment:
            notice.append(comment)
        if approve is True:
            notice.append('')
            notice.append(f'自动批准：{reason}')
        elif approve is False:
            notice.append('')
            notice.append(f'自动驳回：{reason}')
        messages.append('\n'.join(notice).strip())
        return QQBotGroupAdminRuntimeResult(handled=bool(actions or messages), actions=actions, messages=messages)

    async def _handle_group_increase(
        self,
        payload: dict[str, Any],
        executor: _ActionExecutor | None,
    ) -> QQBotGroupAdminRuntimeResult:
        group_id = str(payload.get('group_id') or '').strip()
        user_id = str(payload.get('user_id') or '').strip()
        if not group_id or not user_id:
            return QQBotGroupAdminRuntimeResult()
        if self.self_id and user_id == self.self_id:
            return QQBotGroupAdminRuntimeResult()

        cfg = self.get_group_config(group_id)
        messages: list[str] = []
        actions: list[str] = []
        join_welcome = str(cfg.get('join_welcome') or '')
        if join_welcome:
            messages.append(join_welcome.format(nickname=user_id))
        join_ban_time = int(cfg.get('join_ban_time') or 0)
        if executor and join_ban_time > 0:
            await executor(
                'set_group_ban',
                {'group_id': int(group_id), 'user_id': int(user_id), 'duration': join_ban_time},
            )
            actions.append('set_group_ban')
        return QQBotGroupAdminRuntimeResult(handled=bool(actions or messages), actions=actions, messages=messages)

    async def _handle_group_decrease(self, payload: dict[str, Any]) -> QQBotGroupAdminRuntimeResult:
        sub_type = str(payload.get('sub_type') or '').strip().lower()
        if sub_type not in {'leave', 'kick', 'kick_me'}:
            return QQBotGroupAdminRuntimeResult()
        group_id = str(payload.get('group_id') or '').strip()
        user_id = str(payload.get('user_id') or '').strip()
        if not group_id or not user_id:
            return QQBotGroupAdminRuntimeResult()
        if sub_type == 'kick_me' or (self.self_id and user_id == self.self_id):
            return QQBotGroupAdminRuntimeResult()

        cfg = self.get_group_config(group_id)
        messages: list[str] = []
        should_block = bool(cfg.get('leave_block')) if sub_type == 'leave' else bool(cfg.get('kick_block'))
        if should_block:
            block_ids = list(cfg.get('block_ids') or [])
            if user_id not in block_ids:
                block_ids.append(user_id)
                self.update_group_config(group_id, {'block_ids': block_ids})
        if cfg.get('leave_notify'):
            suffix = '，已拉黑' if should_block else ''
            if sub_type == 'leave':
                messages.append(f'{user_id}({user_id}) 主动退群了{suffix}')
            else:
                messages.append(f'{user_id}({user_id}) 被踢出群了{suffix}')
        return QQBotGroupAdminRuntimeResult(handled=bool(messages or should_block), actions=[], messages=messages)

    def _evaluate_join_request(
        self,
        group_id: str,
        user_id: str,
        comment: str,
        user_level: int | None,
    ) -> tuple[bool | None, str]:
        cfg = self.get_group_config(group_id)
        block_ids = list(cfg.get('block_ids') or [])
        if user_id in block_ids:
            return False, '黑名单用户'

        min_level = int(cfg.get('join_min_level') or 0)
        if min_level > 0:
            if user_level is None:
                return False, f'无法获取QQ等级(要求至少{min_level}级)'
            if user_level < min_level:
                return False, f'QQ等级过低({user_level}<{min_level})'

        answer = str(comment or '')
        if '\n答案：' in answer:
            answer = answer.split('\n答案：', 1)[1]
        lower_answer = answer.lower()

        reject_words = [str(item).strip().lower() for item in cfg.get('join_reject_words', []) if str(item).strip()]
        if reject_words and any(word in lower_answer for word in reject_words):
            if cfg.get('reject_word_block'):
                if user_id not in block_ids:
                    block_ids.append(user_id)
                    self.update_group_config(group_id, {'block_ids': block_ids})
                return False, '命中进群黑词，已拉黑'
            return False, '命中进群黑词'

        accept_words = [str(item).strip().lower() for item in cfg.get('join_accept_words', []) if str(item).strip()]
        if accept_words and any(word in lower_answer for word in accept_words):
            self._join_fail_counts.pop(f'{group_id}:{user_id}', None)
            return True, '命中进群白词'

        max_time = int(cfg.get('join_max_time') or 0)
        if max_time > 0:
            key = f'{group_id}:{user_id}'
            self._join_fail_counts[key] = self._join_fail_counts.get(key, 0) + 1
            if self._join_fail_counts[key] >= max_time:
                if user_id not in block_ids:
                    block_ids.append(user_id)
                    self.update_group_config(group_id, {'block_ids': block_ids})
                return False, f'进群尝试次数已达上限({max_time}次)，已拉黑'

        if cfg.get('join_no_match_reject'):
            return False, '未命中进群关键词'
        return None, '人工审核'

    async def restore_schedules(self) -> list[str]:
        self._ensure_curfew_task_started()
        active_groups = await self.run_curfew_check_once()
        await self.run_vote_settlement_once()
        for group_id, session in self.store.list_vote_sessions().items():
            expire_at = int((session or {}).get('expire_at') or 0)
            if expire_at > int(time.time()):
                self._schedule_vote_settlement(group_id, expire_at)
        return active_groups

    async def shutdown(self) -> None:
        for task in list(self._vote_tasks.values()):
            task.cancel()
        for task in list(self._vote_tasks.values()):
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._vote_tasks.clear()
        if self._curfew_task is not None:
            self._curfew_task.cancel()
            try:
                await self._curfew_task
            except asyncio.CancelledError:
                pass
            self._curfew_task = None
        if not self.closed:
            self.store.close()
            self.closed = True
