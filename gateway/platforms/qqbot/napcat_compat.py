"""NapCat-backed compatibility implementation for the qqbot platform slot.

This branch preserves the public `qqbot` platform identity while routing the
active transport through NapCat / OneBot 11.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import aiohttp
    from aiohttp import web
    AIOHTTP_AVAILABLE = True
except ImportError:  # pragma: no cover - dependency gate
    aiohttp = None  # type: ignore[assignment]
    web = None  # type: ignore[assignment]
    AIOHTTP_AVAILABLE = False

try:
    import httpx  # kept for compatibility with existing patch/test points
    HTTPX_AVAILABLE = True
except ImportError:  # pragma: no cover - dependency gate
    httpx = None  # type: ignore[assignment]
    HTTPX_AVAILABLE = False

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import (
    BasePlatformAdapter,
    MessageEvent,
    MessageType,
    SendResult,
    get_qq_referenced_media_cache_dir,
)

logger = logging.getLogger(__name__)


class QQCloseError(Exception):
    """Compatibility placeholder for historical qqbot imports."""


async def _ssrf_redirect_guard(response):  # pragma: no cover - compatibility shim
    return None


def check_napcat_requirements() -> bool:
    return AIOHTTP_AVAILABLE


class NapCatAdapter(BasePlatformAdapter):
    MAX_MESSAGE_LENGTH = 4500
    REFERENCED_IMAGE_MAX_BYTES = 30 * 1024 * 1024
    REFERENCED_AUDIO_MAX_BYTES = 30 * 1024 * 1024
    REFERENCED_VIDEO_MAX_BYTES = 50 * 1024 * 1024

    def __init__(self, config: PlatformConfig):
        super().__init__(config, Platform.QQBOT)
        extra = config.extra or {}
        self.http_api = str(extra.get("http_api") or os.getenv("NAPCAT_HTTP_API", "")).strip().rstrip("/")
        self.access_token = str(config.token or extra.get("access_token") or os.getenv("NAPCAT_TOKEN", "")).strip()
        self.self_id = str(extra.get("self_id") or os.getenv("NAPCAT_SELF_ID", "")).strip()
        self.ws_host = str(extra.get("ws_host") or os.getenv("NAPCAT_WS_HOST", "127.0.0.1")).strip() or "127.0.0.1"
        self.ws_port = int(extra.get("ws_port") or os.getenv("NAPCAT_WS_PORT", "18800"))
        self.response_prefix = str(extra.get("response_prefix") or os.getenv("NAPCAT_RESPONSE_PREFIX", "")).strip()
        self.media_max_mb = int(extra.get("media_max_mb") or os.getenv("NAPCAT_MEDIA_MAX_MB", "5") or 5)
        self.dm_policy = str(extra.get("dm_policy") or os.getenv("NAPCAT_DM_POLICY", "open")).strip().lower()
        self.group_policy = str(extra.get("group_policy") or os.getenv("NAPCAT_GROUP_POLICY", "open")).strip().lower()
        self.allow_from = self._coerce_list(extra.get("allow_from") or os.getenv("NAPCAT_ALLOWED_USERS", ""))
        self.group_allow_from = self._coerce_list(extra.get("group_allow_from") or os.getenv("NAPCAT_GROUP_ALLOWED_USERS", ""))
        self.require_mention_in_groups = str(extra.get("require_mention_in_groups", "true")).strip().lower() not in {"false", "0", "no", "off"}
        self.command_prefixes = self._coerce_command_prefixes(extra.get("command_prefixes"))
        self.group_commands_require_mention = self._coerce_bool(extra.get("group_commands_require_mention"), False)
        self.group_free_text_require_mention = self._coerce_bool(
            extra.get("group_free_text_require_mention"),
            self.require_mention_in_groups,
        )
        self.allow_reply_without_mention = self._coerce_bool(extra.get("allow_reply_without_mention"), False)
        self._http_session: Optional[aiohttp.ClientSession] = None
        self._runner: Optional[web.AppRunner] = None
        self._chat_types: Dict[str, str] = {}
        self._known_bot_message_ids: set[str] = set()
        self._raw_event_handler = None

    @property
    def name(self) -> str:
        return 'QQBot'

    @staticmethod
    def _coerce_list(value: Any) -> List[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        if isinstance(value, (list, tuple, set)):
            return [str(item).strip() for item in value if str(item).strip()]
        return [str(value).strip()] if str(value).strip() else []

    @staticmethod
    def _coerce_bool(value: Any, default: bool) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "1", "yes", "on"}:
                return True
            if normalized in {"false", "0", "no", "off"}:
                return False
        return default

    @classmethod
    def _coerce_command_prefixes(cls, value: Any) -> List[str]:
        prefixes = cls._coerce_list(value)
        return prefixes or ["/"]

    def refresh_config(self, config: PlatformConfig) -> None:
        self.config = config
        extra = config.extra or {}
        self.http_api = str(extra.get("http_api") or os.getenv("NAPCAT_HTTP_API", "")).strip().rstrip("/")
        self.access_token = str(config.token or extra.get("access_token") or os.getenv("NAPCAT_TOKEN", "")).strip()
        self.self_id = str(extra.get("self_id") or os.getenv("NAPCAT_SELF_ID", "")).strip()
        self.ws_host = str(extra.get("ws_host") or os.getenv("NAPCAT_WS_HOST", "127.0.0.1")).strip() or "127.0.0.1"
        self.ws_port = int(extra.get("ws_port") or os.getenv("NAPCAT_WS_PORT", "18800"))
        self.response_prefix = str(extra.get("response_prefix") or os.getenv("NAPCAT_RESPONSE_PREFIX", "")).strip()
        self.media_max_mb = int(extra.get("media_max_mb") or os.getenv("NAPCAT_MEDIA_MAX_MB", "5") or 5)
        self.dm_policy = str(extra.get("dm_policy") or os.getenv("NAPCAT_DM_POLICY", "open")).strip().lower()
        self.group_policy = str(extra.get("group_policy") or os.getenv("NAPCAT_GROUP_POLICY", "open")).strip().lower()
        self.allow_from = self._coerce_list(extra.get("allow_from") or os.getenv("NAPCAT_ALLOWED_USERS", ""))
        self.group_allow_from = self._coerce_list(extra.get("group_allow_from") or os.getenv("NAPCAT_GROUP_ALLOWED_USERS", ""))
        self.require_mention_in_groups = str(extra.get("require_mention_in_groups", "true")).strip().lower() not in {"false", "0", "no", "off"}
        self.command_prefixes = self._coerce_command_prefixes(extra.get("command_prefixes"))
        self.group_commands_require_mention = self._coerce_bool(extra.get("group_commands_require_mention"), False)
        self.group_free_text_require_mention = self._coerce_bool(
            extra.get("group_free_text_require_mention"),
            self.require_mention_in_groups,
        )
        self.allow_reply_without_mention = self._coerce_bool(extra.get("allow_reply_without_mention"), False)

    @staticmethod
    def _coerce_api_id(value: Any) -> Any:
        text = str(value or '').strip()
        if text.lstrip('-').isdigit():
            try:
                return int(text)
            except Exception:
                return text
        return text

    @staticmethod
    def _parse_size_bytes(value: Any) -> Optional[int]:
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        try:
            return int(text)
        except Exception:
            return None

    @staticmethod
    def _sanitize_referenced_filename(preferred_name: str, *, fallback_stem: str, default_ext: str = '.bin') -> str:
        safe_name = Path(str(preferred_name or '')).name.replace('\x00', '').strip()
        if not safe_name or safe_name in {'.', '..'}:
            safe_name = fallback_stem
        suffix = Path(safe_name).suffix
        if not suffix and default_ext:
            safe_name = f"{safe_name}{default_ext}"
        return safe_name

    def _get_referenced_media_limit(self, media_kind: str) -> int:
        if media_kind == 'image':
            return self.REFERENCED_IMAGE_MAX_BYTES
        if media_kind == 'audio':
            return self.REFERENCED_AUDIO_MAX_BYTES
        if media_kind == 'video':
            return self.REFERENCED_VIDEO_MAX_BYTES
        raise ValueError(f'Unsupported referenced media kind: {media_kind}')

    def _stage_referenced_existing_file(
        self,
        source_path: str,
        *,
        media_kind: str,
        preferred_name: str,
        max_bytes: int,
    ) -> Optional[str]:
        candidate = Path(str(source_path or '').strip())
        if not candidate.exists() or not candidate.is_file():
            return None
        size = candidate.stat().st_size
        if size > max_bytes:
            logger.info('[napcat] referenced %s skipped — %s bytes exceeds limit %s', media_kind, size, max_bytes)
            return None
        cache_dir = get_qq_referenced_media_cache_dir(media_kind)
        safe_name = self._sanitize_referenced_filename(
            preferred_name or candidate.name,
            fallback_stem=f'{media_kind}_reply',
            default_ext=candidate.suffix or '.bin',
        )
        target = cache_dir / f"ref_{uuid.uuid4().hex[:12]}_{safe_name}"
        shutil.copy2(candidate, target)
        return str(target)

    def _stage_referenced_bytes(
        self,
        data: bytes,
        *,
        media_kind: str,
        preferred_name: str,
        max_bytes: int,
        default_ext: str = '.bin',
    ) -> Optional[str]:
        if len(data) > max_bytes:
            logger.info('[napcat] referenced %s skipped — %s bytes exceeds limit %s', media_kind, len(data), max_bytes)
            return None
        cache_dir = get_qq_referenced_media_cache_dir(media_kind)
        safe_name = self._sanitize_referenced_filename(
            preferred_name,
            fallback_stem=f'{media_kind}_reply',
            default_ext=default_ext,
        )
        target = cache_dir / f"ref_{uuid.uuid4().hex[:12]}_{safe_name}"
        target.write_bytes(data)
        return str(target)

    async def _stage_referenced_payload(
        self,
        payload: Dict[str, Any],
        *,
        media_kind: str,
        preferred_name: str,
        max_bytes: int,
        default_ext: str = '.bin',
    ) -> Optional[str]:
        file_size = self._parse_size_bytes(payload.get('file_size'))
        if file_size is not None and file_size > max_bytes:
            logger.info('[napcat] referenced %s skipped — %s bytes exceeds limit %s', media_kind, file_size, max_bytes)
            return None
        local_path = self._stage_referenced_existing_file(
            str(payload.get('file') or ''),
            media_kind=media_kind,
            preferred_name=str(payload.get('file_name') or preferred_name or ''),
            max_bytes=max_bytes,
        )
        if local_path:
            return local_path
        base64_data = str(payload.get('base64') or '').strip()
        if base64_data:
            try:
                decoded = base64.b64decode(base64_data, validate=False)
            except Exception:
                logger.warning('[napcat] failed to decode referenced %s base64 payload', media_kind)
            else:
                return self._stage_referenced_bytes(
                    decoded,
                    media_kind=media_kind,
                    preferred_name=str(payload.get('file_name') or preferred_name or ''),
                    max_bytes=max_bytes,
                    default_ext=default_ext,
                )
        return None

    async def _cache_referenced_media_for_segment(self, seg_type: str, data: Dict[str, Any]) -> Optional[Tuple[str, str]]:
        if seg_type not in {'image', 'record', 'video'}:
            return None
        media_kind = {'image': 'image', 'record': 'audio', 'video': 'video'}[seg_type]
        max_bytes = self._get_referenced_media_limit(media_kind)
        declared_size = self._parse_size_bytes(data.get('file_size'))
        if declared_size is not None and declared_size > max_bytes:
            logger.info('[napcat] referenced %s skipped from segment metadata — %s bytes exceeds limit %s', media_kind, declared_size, max_bytes)
            return None
        file_ref = str(data.get('file') or '').strip()
        url = str(data.get('url') or '').strip()

        if seg_type == 'image' and file_ref:
            try:
                result = await self._call_api('get_image', {'file': file_ref})
                local_path = await self._stage_referenced_payload(
                    result.get('data') or {},
                    media_kind=media_kind,
                    preferred_name=file_ref,
                    max_bytes=max_bytes,
                    default_ext=Path(file_ref).suffix or '.png',
                )
                if local_path:
                    return local_path, media_kind
            except Exception as exc:
                logger.debug('[napcat] get_image failed for referenced media %s: %s', file_ref, exc)

        if seg_type == 'record' and file_ref:
            out_format = (Path(file_ref).suffix.lstrip('.') or 'mp3').lower()
            try:
                result = await self._call_api('get_record', {'file': file_ref, 'out_format': out_format})
                local_path = await self._stage_referenced_payload(
                    result.get('data') or {},
                    media_kind=media_kind,
                    preferred_name=file_ref,
                    max_bytes=max_bytes,
                    default_ext=f'.{out_format}' if out_format else '.mp3',
                )
                if local_path:
                    return local_path, media_kind
            except Exception as exc:
                logger.debug('[napcat] get_record failed for referenced media %s: %s', file_ref, exc)

        if seg_type == 'video' and url:
            try:
                result = await self._call_api('download_file', {'url': url, 'thread_count': 1})
                local_path = await self._stage_referenced_payload(
                    result.get('data') or {},
                    media_kind=media_kind,
                    preferred_name=file_ref or Path(url).name,
                    max_bytes=max_bytes,
                    default_ext=Path(file_ref or url).suffix or '.mp4',
                )
                if local_path:
                    return local_path, media_kind
            except Exception as exc:
                logger.debug('[napcat] download_file failed for referenced video %s: %s', url, exc)

        if url and seg_type in {'image', 'record'}:
            try:
                result = await self._call_api('download_file', {'url': url, 'thread_count': 1})
                local_path = await self._stage_referenced_payload(
                    result.get('data') or {},
                    media_kind=media_kind,
                    preferred_name=file_ref or Path(url).name,
                    max_bytes=max_bytes,
                    default_ext=Path(file_ref or url).suffix or ('.png' if media_kind == 'image' else '.mp3'),
                )
                if local_path:
                    return local_path, media_kind
            except Exception as exc:
                logger.debug('[napcat] download_file fallback failed for referenced %s %s: %s', media_kind, url, exc)

        return None

    async def _hydrate_referenced_reply_media(self, event: MessageEvent) -> None:
        reply_id = str(getattr(event, 'reply_to_message_id', '') or '').strip()
        if not reply_id:
            return
        try:
            result = await self._call_api('get_msg', {'message_id': self._coerce_api_id(reply_id)})
        except Exception as exc:
            logger.debug('[napcat] get_msg failed for referenced reply %s: %s', reply_id, exc)
            return
        payload = result.get('data') or {}
        segments = payload.get('message')
        if not isinstance(segments, list):
            return
        referenced_urls: List[str] = []
        referenced_types: List[str] = []
        for segment in segments:
            if not isinstance(segment, dict):
                continue
            seg_type = str(segment.get('type') or '').strip().lower()
            data = segment.get('data') or {}
            cached = await self._cache_referenced_media_for_segment(seg_type, data if isinstance(data, dict) else {})
            if cached is None:
                continue
            local_path, media_kind = cached
            referenced_urls.append(local_path)
            referenced_types.append(media_kind)
        event.referenced_media_urls = referenced_urls
        event.referenced_media_types = referenced_types

    async def connect(self) -> bool:
        if not AIOHTTP_AVAILABLE:
            self._set_fatal_error("napcat_missing_dependency", "NapCat startup failed: aiohttp not installed", retryable=False)
            return False
        if not self.http_api:
            self._set_fatal_error("napcat_missing_http_api", "NapCat startup failed: NAPCAT_HTTP_API is required", retryable=False)
            return False
        if not self.access_token:
            self._set_fatal_error("napcat_missing_token", "NapCat startup failed: NAPCAT_TOKEN is required", retryable=False)
            return False
        self._http_session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30), trust_env=True)
        app = web.Application()
        app.router.add_get("/", self._handle_ws)
        app.router.add_get("/ws", self._handle_ws)
        app.router.add_get("/health", self._handle_health)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self.ws_host, self.ws_port)
        try:
            await site.start()
        except Exception as exc:
            await self.disconnect()
            self._set_fatal_error("napcat_bind_failed", f"NapCat startup failed: {exc}", retryable=True)
            return False
        self._mark_connected()
        logger.info("[napcat] listening for reverse WS on ws://%s:%s", self.ws_host, self.ws_port)
        return True

    async def disconnect(self) -> None:
        self._running = False
        if self._runner:
            await self._runner.cleanup()
            self._runner = None
        if self._http_session and not self._http_session.closed:
            await self._http_session.close()
        self._http_session = None
        self._mark_disconnected()

    async def _handle_health(self, request: web.Request) -> web.Response:
        return web.Response(text="ok")

    async def _handle_ws(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=30)
        await ws.prepare(request)
        logger.info("[napcat] reverse WS connected from %s", request.remote)
        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                try:
                    payload = json.loads(msg.data)
                except Exception:
                    logger.debug("[napcat] invalid JSON payload: %s", msg.data[:200])
                    continue
                asyncio.create_task(self._process_payload(payload))
            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                break
        logger.info("[napcat] reverse WS disconnected")
        return ws

    def set_raw_event_handler(self, handler) -> None:
        self._raw_event_handler = handler

    async def _process_payload(self, payload: Dict[str, Any]) -> None:
        event = self._build_event_from_payload(payload)
        if event is None:
            if self._raw_event_handler:
                try:
                    await self._raw_event_handler(payload)
                except Exception as exc:
                    logger.debug("[napcat] raw event handler failed: %s", exc)
            return
        try:
            await self._hydrate_referenced_reply_media(event)
        except Exception as exc:
            logger.debug('[napcat] referenced reply media hydration failed: %s', exc)
        await self.handle_message(event)

    def _is_dm_allowed(self, sender_id: str) -> bool:
        if self.dm_policy == "disabled":
            return False
        if self.dm_policy == "allowlist":
            return sender_id in self.allow_from
        return True

    def _is_group_allowed(self, chat_id: str) -> bool:
        if self.group_policy == "disabled":
            return False
        if self.group_policy == "allowlist" and chat_id not in self.group_allow_from:
            return False
        return True

    def _extract_segments(self, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        message = payload.get("message")
        if isinstance(message, list):
            return [seg for seg in message if isinstance(seg, dict)]
        raw = str(payload.get("raw_message") or payload.get("message") or "").strip()
        if raw:
            return [{"type": "text", "data": {"text": raw}}]
        return []

    def _has_bot_mention(self, segments: List[Dict[str, Any]]) -> bool:
        if not self.self_id:
            return False
        for seg in segments:
            if seg.get("type") == "at" and str((seg.get("data") or {}).get("qq") or "").strip() == self.self_id:
                return True
        return False

    def _strip_bot_mention(self, segments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not self.self_id:
            return segments
        return [
            seg for seg in segments
            if not (seg.get("type") == "at" and str((seg.get("data") or {}).get("qq") or "").strip() == self.self_id)
        ]

    def _is_prefixed_command_text(self, text: str) -> bool:
        stripped = text.strip()
        return any(stripped.startswith(prefix) for prefix in self.command_prefixes)

    def _normalize_command_prefix(self, text: str) -> str:
        stripped = text.strip()
        for prefix in self.command_prefixes:
            if not stripped.startswith(prefix):
                continue
            if prefix == "/":
                return stripped
            remainder = stripped[len(prefix):].lstrip()
            return "/" if not remainder else f"/{remainder}"
        return stripped

    def _extract_text_and_media(self, segments: List[Dict[str, Any]]) -> Tuple[str, List[str], List[str], Optional[str]]:
        parts: List[str] = []
        media_urls: List[str] = []
        media_types: List[str] = []
        reply_to: Optional[str] = None
        for seg in segments:
            seg_type = str(seg.get("type") or "")
            data = seg.get("data") or {}
            if seg_type == "text":
                parts.append(str(data.get("text") or ""))
            elif seg_type == "at":
                parts.append(f"@{data.get('qq')}")
            elif seg_type == "reply":
                seg_id = data.get("id")
                if seg_id is not None:
                    reply_to = str(seg_id)
            elif seg_type == "image":
                url = str(data.get("url") or data.get("file") or "").strip()
                if url:
                    media_urls.append(url)
                    media_types.append("image")
            elif seg_type == "record":
                url = str(data.get("url") or data.get("file") or "").strip()
                if url:
                    media_urls.append(url)
                    media_types.append("audio")
            elif seg_type == "video":
                url = str(data.get("url") or data.get("file") or "").strip()
                if url:
                    media_urls.append(url)
                    media_types.append("video")
            elif seg_type == "file":
                url = str(data.get("url") or data.get("file") or "").strip()
                if url:
                    media_urls.append(url)
                    media_types.append("document")
        return "".join(parts).strip(), media_urls, media_types, reply_to

    def _infer_message_type(self, text: str, media_types: List[str]) -> MessageType:
        if not media_types:
            return MessageType.TEXT
        if any(mt == "audio" for mt in media_types):
            return MessageType.VOICE if not text else MessageType.TEXT
        if any(mt == "video" for mt in media_types):
            return MessageType.VIDEO if not text else MessageType.TEXT
        if any(mt == "document" for mt in media_types):
            return MessageType.DOCUMENT if not text else MessageType.TEXT
        if any(mt == "image" for mt in media_types):
            return MessageType.PHOTO if not text else MessageType.TEXT
        return MessageType.TEXT

    def _build_event_from_payload(self, payload: Dict[str, Any]) -> Optional[MessageEvent]:
        if str(payload.get("post_type") or "") != "message":
            return None
        msg_kind = str(payload.get("message_type") or "").strip().lower()
        sender_id = str(payload.get("user_id") or "").strip()
        if not sender_id:
            return None
        if self.self_id and sender_id == self.self_id:
            return None
        segments = self._extract_segments(payload)
        if msg_kind == "group":
            group_id = str(payload.get("group_id") or "").strip()
            if not group_id or not self._is_group_allowed(group_id):
                return None
            text_preview, _, _, reply_to_preview = self._extract_text_and_media(segments)
            normalized_preview = self._normalize_command_prefix(text_preview)
            has_mention = self._has_bot_mention(segments)
            is_prefixed_command = self._is_prefixed_command_text(text_preview)
            reply_without_mention = bool(
                reply_to_preview
                and self.allow_reply_without_mention
                and str(reply_to_preview).strip() in self._known_bot_message_ids
            )
            if self.self_id:
                if is_prefixed_command:
                    if self.group_commands_require_mention and not has_mention:
                        return None
                elif self.group_free_text_require_mention and not has_mention and not reply_without_mention:
                    return None
            segments = self._strip_bot_mention(segments)
            chat_id = group_id
            chat_type = "group"
        else:
            if not self._is_dm_allowed(sender_id):
                return None
            chat_id = sender_id
            chat_type = "dm"
        text, media_urls, media_types, reply_to = self._extract_text_and_media(segments)
        text = self._normalize_command_prefix(text)
        if not text and not media_urls:
            return None
        sender = payload.get("sender") or {}
        user_name = str(sender.get("card") or sender.get("nickname") or sender_id)
        message_id = str(payload.get("message_id") or "").strip() or None
        self._chat_types[chat_id] = chat_type
        return MessageEvent(
            text=text,
            message_type=self._infer_message_type(text, media_types),
            source=self.build_source(
                chat_id=chat_id,
                chat_name=chat_id if chat_type == "group" else user_name,
                chat_type=chat_type,
                user_id=sender_id,
                user_name=user_name,
            ),
            raw_message=payload,
            message_id=message_id,
            media_urls=media_urls,
            media_types=media_types,
            reply_to_message_id=reply_to,
            timestamp=datetime.now(),
            internal=False,
        )

    async def _call_api(self, action: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not self._http_session:
            raise RuntimeError("NapCat HTTP session not initialized")
        url = f"{self.http_api}/{action}"
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {self.access_token}"}
        async with self._http_session.post(url, json=payload, headers=headers) as resp:
            text = await resp.text()
            if resp.status >= 400:
                raise RuntimeError(f"NapCat HTTP {resp.status}: {text[:300]}")
            data = json.loads(text or "{}")
        if data.get("retcode") not in (0, None):
            raise RuntimeError(f"NapCat API error retcode={data.get('retcode')} status={data.get('status')}")
        return data

    async def send(self, chat_id: str, content: str, reply_to: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None) -> SendResult:
        try:
            metadata = metadata or {}
            target_chat_id = str(chat_id)
            chat_type = str(metadata.get("chat_type") or self._chat_types.get(target_chat_id) or "dm")
            if target_chat_id.startswith("group:"):
                target_chat_id = target_chat_id.split(":", 1)[1]
                chat_type = "group"
            text = self.format_message(content)
            if self.response_prefix:
                text = f"{self.response_prefix}{text}"
            chunks = self.truncate_message(text, self.MAX_MESSAGE_LENGTH)
            last_message_id: Optional[str] = None
            for idx, chunk in enumerate(chunks):
                message: List[Dict[str, Any]] = []
                if idx == 0 and reply_to:
                    message.append({"type": "reply", "data": {"id": str(reply_to)}})
                message.append({"type": "text", "data": {"text": chunk}})
                if chat_type == "group":
                    result = await self._call_api("send_group_msg", {"group_id": int(target_chat_id), "message": message})
                else:
                    result = await self._call_api("send_private_msg", {"user_id": int(target_chat_id), "message": message})
                data = result.get("data") or {}
                message_id = data.get("message_id")
                if message_id is not None:
                    last_message_id = str(message_id)
                    self._known_bot_message_ids.add(last_message_id)
            if last_message_id:
                self._chat_types[target_chat_id] = chat_type
            return SendResult(success=True, message_id=last_message_id)
        except Exception as exc:
            logger.error("[napcat] send failed: %s", exc)
            return SendResult(success=False, error=str(exc))

    async def send_image_file(
        self,
        chat_id: str,
        image_path: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        **kwargs,
    ) -> SendResult:
        try:
            metadata = kwargs.get("metadata") or {}
            target_chat_id = str(chat_id)
            chat_type = str(metadata.get("chat_type") or self._chat_types.get(target_chat_id) or "dm")
            if target_chat_id.startswith("group:"):
                target_chat_id = target_chat_id.split(":", 1)[1]
                chat_type = "group"

            path = Path(str(image_path)).expanduser().resolve()
            if not path.is_file():
                return SendResult(success=False, error=f"Image file not found: {image_path}")
            max_bytes = max(int(self.media_max_mb), 1) * 1024 * 1024
            try:
                size = path.stat().st_size
            except OSError as exc:
                return SendResult(success=False, error=str(exc))
            if size > max_bytes:
                return SendResult(success=False, error=f"Image file too large: {size} bytes > {max_bytes} bytes")

            message: List[Dict[str, Any]] = []
            if reply_to:
                message.append({"type": "reply", "data": {"id": str(reply_to)}})
            if caption:
                text = self.format_message(caption)
                if self.response_prefix:
                    text = f"{self.response_prefix}{text}"
                message.append({"type": "text", "data": {"text": text}})
            message.append({"type": "image", "data": {"file": path.as_uri()}})

            if chat_type == "group":
                result = await self._call_api("send_group_msg", {"group_id": int(target_chat_id), "message": message})
            else:
                result = await self._call_api("send_private_msg", {"user_id": int(target_chat_id), "message": message})
            data = result.get("data") or {}
            message_id = data.get("message_id")
            message_id_text = str(message_id) if message_id is not None else None
            if message_id_text:
                self._known_bot_message_ids.add(message_id_text)
                self._chat_types[target_chat_id] = chat_type
            return SendResult(success=True, message_id=message_id_text, raw_response=result)
        except Exception as exc:
            logger.error("[napcat] send_image_file failed: %s", exc)
            return SendResult(success=False, error=str(exc))

    async def send_document(
        self,
        chat_id: str,
        file_path: str,
        caption: Optional[str] = None,
        file_name: Optional[str] = None,
        reply_to: Optional[str] = None,
        **kwargs,
    ) -> SendResult:
        try:
            metadata = kwargs.get("metadata") or {}
            target_chat_id = str(chat_id)
            chat_type = str(metadata.get("chat_type") or self._chat_types.get(target_chat_id) or "dm")
            if target_chat_id.startswith("group:"):
                target_chat_id = target_chat_id.split(":", 1)[1]
                chat_type = "group"

            path = Path(str(file_path)).expanduser().resolve()
            if not path.is_file():
                return SendResult(success=False, error=f"Document file not found: {file_path}")
            name = str(file_name or path.name).strip() or path.name
            payload = {"file": str(path), "name": name}
            if chat_type == "group":
                payload["group_id"] = int(target_chat_id)
                result = await self._call_api("upload_group_file", payload)
            else:
                payload["user_id"] = int(target_chat_id)
                result = await self._call_api("upload_private_file", payload)
            self._chat_types[target_chat_id] = chat_type
            data = result.get("data") if isinstance(result, dict) else {}
            message_id = None
            if isinstance(data, dict):
                message_id = data.get("message_id") or data.get("file_id")
            return SendResult(success=True, message_id=str(message_id) if message_id is not None else None, raw_response=result)
        except Exception as exc:
            logger.error("[napcat] send_document failed: %s", exc)
            return SendResult(success=False, error=str(exc))

    @property
    def supports_delete_message(self) -> bool:
        return True

    async def delete_message(self, chat_id: str, message_id: str) -> SendResult:
        try:
            result = await self._call_api("delete_msg", {"message_id": int(str(message_id))})
            return SendResult(success=True, message_id=str(message_id), raw_response=result)
        except Exception as exc:
            logger.error("[napcat] delete_message failed: %s", exc)
            return SendResult(success=False, error=str(exc))

    async def get_chat_info(self, chat_id: str) -> Dict[str, Any]:
        chat_type = self._chat_types.get(str(chat_id), "dm")
        return {"name": str(chat_id), "type": chat_type}


check_qq_requirements = check_napcat_requirements
QQAdapter = NapCatAdapter
_coerce_list = NapCatAdapter._coerce_list
