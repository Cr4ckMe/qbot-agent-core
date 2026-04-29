from __future__ import annotations

import json
import sqlite3
from copy import deepcopy
from pathlib import Path
from typing import Any

from hermes_constants import get_hermes_home


DEFAULT_GROUP_ADMIN_CONFIG: dict[str, Any] = {
    'builtin_ban': False,
    'custom_ban_words': [],
    'word_ban_time': 0,
    'spamming_ban_time': 0,
    'join_switch': False,
    'join_accept_words': [],
    'join_reject_words': [],
    'join_no_match_reject': False,
    'reject_word_block': False,
    'block_ids': [],
    'join_min_level': 0,
    'join_max_time': 0,
    'join_ban_time': 0,
    'join_welcome': '',
    'leave_notify': False,
    'leave_block': False,
    'kick_block': False,
    'curfew_enabled': False,
    'curfew_start': '',
    'curfew_end': '',
    'vote_ban_ttl': 120,
    'vote_ban_threshold': 3,
    'vote_ban_random_range': '30~300',
}

_FIELD_TYPES: dict[str, type] = {
    'builtin_ban': bool,
    'custom_ban_words': list,
    'word_ban_time': int,
    'spamming_ban_time': int,
    'join_switch': bool,
    'join_accept_words': list,
    'join_reject_words': list,
    'join_no_match_reject': bool,
    'reject_word_block': bool,
    'block_ids': list,
    'join_min_level': int,
    'join_max_time': int,
    'join_ban_time': int,
    'join_welcome': str,
    'leave_notify': bool,
    'leave_block': bool,
    'kick_block': bool,
    'curfew_enabled': bool,
    'curfew_start': str,
    'curfew_end': str,
    'vote_ban_ttl': int,
    'vote_ban_threshold': int,
    'vote_ban_random_range': str,
}


class QQBotGroupAdminStore:
    def __init__(self, db_path: Path | None = None):
        self.db_path = Path(db_path) if db_path else get_hermes_home() / 'qqbot' / 'qqadmin' / 'config.sqlite3'
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.execute(
            'CREATE TABLE IF NOT EXISTS group_admin_config (group_id TEXT PRIMARY KEY, data TEXT NOT NULL)'
        )
        self._conn.execute(
            'CREATE TABLE IF NOT EXISTS group_admin_vote_session (group_id TEXT PRIMARY KEY, data TEXT NOT NULL)'
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def get_group_config(self, group_id: str) -> dict[str, Any]:
        gid = str(group_id).strip()
        row = self._conn.execute(
            'SELECT data FROM group_admin_config WHERE group_id = ?', (gid,)
        ).fetchone()
        data = deepcopy(DEFAULT_GROUP_ADMIN_CONFIG)
        if row and row[0]:
            payload = json.loads(row[0])
            if isinstance(payload, dict):
                data.update({k: payload[k] for k in payload if k in DEFAULT_GROUP_ADMIN_CONFIG})
        return data

    def update_group_config(self, group_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        gid = str(group_id).strip()
        if not gid:
            raise ValueError('group_id is required')
        config = self.get_group_config(gid)
        for key, value in dict(updates or {}).items():
            if key not in DEFAULT_GROUP_ADMIN_CONFIG:
                raise ValueError(f'Unknown qqadmin config field: {key}')
            config[key] = self._normalize_value(key, value)
        self._save(gid, config)
        return config

    def reset_group_config(self, group_id: str) -> dict[str, Any]:
        gid = str(group_id).strip()
        config = deepcopy(DEFAULT_GROUP_ADMIN_CONFIG)
        self._save(gid, config)
        self.clear_vote_session(gid)
        return config

    def reset_all_groups(self) -> int:
        rows = self._conn.execute('SELECT COUNT(*) FROM group_admin_config').fetchone()
        count = int(rows[0] if rows else 0)
        self._conn.execute('DELETE FROM group_admin_config')
        self._conn.execute('DELETE FROM group_admin_vote_session')
        self._conn.commit()
        return count

    def list_group_configs(self) -> dict[str, dict[str, Any]]:
        rows = self._conn.execute('SELECT group_id FROM group_admin_config').fetchall()
        result: dict[str, dict[str, Any]] = {}
        for row in rows:
            gid = str(row[0]).strip()
            if gid:
                result[gid] = self.get_group_config(gid)
        return result

    def get_vote_session(self, group_id: str) -> dict[str, Any] | None:
        gid = str(group_id).strip()
        if not gid:
            return None
        row = self._conn.execute(
            'SELECT data FROM group_admin_vote_session WHERE group_id = ?', (gid,)
        ).fetchone()
        if not row or not row[0]:
            return None
        payload = json.loads(row[0])
        return payload if isinstance(payload, dict) else None

    def save_vote_session(self, group_id: str, session: dict[str, Any]) -> dict[str, Any]:
        gid = str(group_id).strip()
        if not gid:
            raise ValueError('group_id is required')
        data = dict(session or {})
        self._conn.execute(
            'INSERT INTO group_admin_vote_session(group_id, data) VALUES (?, ?) '
            'ON CONFLICT(group_id) DO UPDATE SET data=excluded.data',
            (gid, json.dumps(data, ensure_ascii=False)),
        )
        self._conn.commit()
        return data

    def clear_vote_session(self, group_id: str) -> None:
        gid = str(group_id).strip()
        if not gid:
            return
        self._conn.execute('DELETE FROM group_admin_vote_session WHERE group_id = ?', (gid,))
        self._conn.commit()

    def list_vote_sessions(self) -> dict[str, dict[str, Any]]:
        rows = self._conn.execute('SELECT group_id, data FROM group_admin_vote_session').fetchall()
        result: dict[str, dict[str, Any]] = {}
        for group_id, data in rows:
            gid = str(group_id).strip()
            if not gid or not data:
                continue
            try:
                payload = json.loads(data)
            except Exception:
                continue
            if isinstance(payload, dict):
                result[gid] = payload
        return result

    def _save(self, group_id: str, config: dict[str, Any]) -> None:
        self._conn.execute(
            'INSERT INTO group_admin_config(group_id, data) VALUES (?, ?) '
            'ON CONFLICT(group_id) DO UPDATE SET data=excluded.data',
            (group_id, json.dumps(config, ensure_ascii=False)),
        )
        self._conn.commit()

    def _normalize_value(self, key: str, value: Any) -> Any:
        expected = _FIELD_TYPES[key]
        if expected is bool:
            return self._coerce_bool(value)
        if expected is int:
            return self._coerce_int(value)
        if expected is list:
            if value is None:
                return []
            if isinstance(value, list):
                return [str(item).strip() for item in value if str(item).strip()]
            if isinstance(value, str):
                text = value.strip()
                return [item for item in text.split() if item]
            return [str(value).strip()] if str(value).strip() else []
        if value is None:
            return ''
        return str(value)

    @staticmethod
    def _coerce_bool(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        text = str(value).strip().lower()
        if text in {'1', 'true', 'yes', 'on', '开', '开启'}:
            return True
        if text in {'0', 'false', 'no', 'off', '关', '关闭', ''}:
            return False
        raise ValueError(f'Invalid boolean value: {value}')

    @staticmethod
    def _coerce_int(value: Any) -> int:
        if isinstance(value, int):
            return value
        text = str(value).strip()
        if not text:
            return 0
        return int(text)
