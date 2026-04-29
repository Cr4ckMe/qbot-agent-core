from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from pathlib import Path
from typing import Any, Collection

from model_tools import handle_function_call

from gateway.qqbot_lute_types import LuteResponse

logger = logging.getLogger(__name__)


def _summarize_tool_payload(payload: Any) -> str:
    if isinstance(payload, dict):
        for key in ('title', 'message', 'summary', 'name', 'text', 'error'):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return json.dumps(payload, ensure_ascii=False)
    if isinstance(payload, list):
        return json.dumps(payload, ensure_ascii=False)
    if payload is None:
        return ''
    return str(payload)


def _parse_json_payload(text: str) -> dict[str, Any] | list[Any] | None:
    stripped = text.strip()
    if not stripped:
        return None
    if not stripped.startswith(('{', '[')):
        return None
    try:
        payload = json.loads(stripped)
    except Exception:
        return None
    if isinstance(payload, (dict, list)):
        return payload
    return None


def _is_allowed_script_command(command: list[str], allowed_executables: Collection[str] | None) -> bool:
    if allowed_executables is None:
        return True
    allowed = set(allowed_executables)
    if not command:
        return False
    if command[0] in allowed:
        return True
    if len(command) > 1 and command[1] in allowed:
        return True
    return False


def _external_api_event(api_name: str | None, *, started_at: float, success: bool, details: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    if not api_name:
        return []
    return [
        {
            'event_type': 'external_api_call',
            'api_name': api_name,
            'duration_ms': max(0, int((time.perf_counter() - started_at) * 1000)),
            'success': bool(success),
            'details': dict(details or {}),
        }
    ]


def _script_api_name(command: list[str]) -> str | None:
    if not command:
        return None
    executable = command[1] if len(command) > 1 else command[0]
    name = Path(str(executable)).stem.strip()
    return f'script.{name}' if name else None


async def call_tool_backend(
    tool_name: str,
    args: dict[str, Any],
    *,
    task_id: str | None = None,
    allowed_tool_names: Collection[str] | None = None,
    timeout_sec: float = 45,
    api_name: str | None = None,
) -> LuteResponse:
    api_name = api_name or f'tool.{tool_name}'
    started_at = time.perf_counter()
    if allowed_tool_names is not None and tool_name not in set(allowed_tool_names):
        return LuteResponse(
            text=f'Tool backend not allowed: {tool_name}',
            telemetry_events=_external_api_event(api_name, started_at=started_at, success=False, details={'reason': 'not_allowed'}),
        )

    try:
        raw = await asyncio.wait_for(
            asyncio.to_thread(handle_function_call, tool_name, args, task_id=task_id),
            timeout=timeout_sec,
        )
    except asyncio.TimeoutError:
        return LuteResponse(
            text=f'Tool backend timed out after {timeout_sec} seconds.',
            telemetry_events=_external_api_event(api_name, started_at=started_at, success=False, details={'reason': 'timeout'}),
        )
    except Exception as exc:
        return LuteResponse(
            text=f'Tool backend failed: {exc}',
            telemetry_events=_external_api_event(api_name, started_at=started_at, success=False, details={'error': str(exc)}),
        )

    try:
        payload = json.loads(raw)
    except Exception:
        return LuteResponse(text=str(raw).strip(), payload=None, telemetry_events=_external_api_event(api_name, started_at=started_at, success=True))
    return LuteResponse(text=_summarize_tool_payload(payload), payload=payload, telemetry_events=_external_api_event(api_name, started_at=started_at, success=True))


async def call_script_backend(
    command: list[str],
    *,
    timeout_sec: float = 45,
    allowed_executables: Collection[str] | None = None,
    api_name: str | None = None,
) -> LuteResponse:
    started_at = time.perf_counter()
    api_name = api_name or _script_api_name(command)
    if not command:
        return LuteResponse(
            text='Script backend command is empty.',
            telemetry_events=_external_api_event(api_name, started_at=started_at, success=False, details={'reason': 'empty_command'}),
        )
    if not _is_allowed_script_command(command, allowed_executables):
        blocked = command[1] if len(command) > 1 else command[0]
        return LuteResponse(
            text=f'Script backend executable not allowed: {blocked}',
            telemetry_events=_external_api_event(api_name, started_at=started_at, success=False, details={'reason': 'not_allowed', 'executable': str(blocked)}),
        )

    process = None
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_sec)
    except asyncio.TimeoutError:
        if process is not None:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            with contextlib.suppress(Exception):
                await process.communicate()
        return LuteResponse(text=f'Script backend timed out after {timeout_sec} seconds.', telemetry_events=_external_api_event(api_name, started_at=started_at, success=False, details={'reason': 'timeout'}))
    except OSError as exc:
        return LuteResponse(text=f'Script backend failed to start: {exc}', telemetry_events=_external_api_event(api_name, started_at=started_at, success=False, details={'error': str(exc)}))

    stdout_text = stdout.decode('utf-8', errors='replace')
    stderr_text = stderr.decode('utf-8', errors='replace')
    file_paths: list[str] = []
    media_paths: list[str] = []
    text_lines: list[str] = []

    for line in stdout_text.splitlines():
        stripped = line.strip()
        if stripped.startswith('FILE_PATH='):
            file_paths.append(stripped.split('=', 1)[1])
            continue
        if stripped.startswith('IMAGE_PATH='):
            media_paths.append(stripped.split('=', 1)[1])
            continue
        text_lines.append(line)

    body = '\n'.join(line for line in text_lines if line).strip()
    payload = _parse_json_payload(body)
    if process.returncode != 0:
        detail = body or stderr_text.strip() or 'script failed'
        return LuteResponse(
            text=f'Script backend failed with exit code {process.returncode}: {detail}',
            file_paths=file_paths,
            media_paths=media_paths,
            payload=payload,
            telemetry_events=_external_api_event(api_name, started_at=started_at, success=False, details={'exit_code': process.returncode}),
        )

    if payload is not None:
        body = _summarize_tool_payload(payload)
    if stderr_text.strip():
        logger.info('script backend stderr (%s): %s', api_name or 'unknown', stderr_text.strip())
    return LuteResponse(text=body, file_paths=file_paths, media_paths=media_paths, payload=payload, telemetry_events=_external_api_event(api_name, started_at=started_at, success=True))
