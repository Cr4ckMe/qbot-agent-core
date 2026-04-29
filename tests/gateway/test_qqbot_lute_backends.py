from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from gateway.qqbot_lute_backends import call_script_backend, call_tool_backend

@pytest.mark.asyncio
async def test_call_tool_backend_parses_json_result(monkeypatch):
    def _fake_handle_function_call(function_name, function_args, task_id=None, **kwargs):
        assert function_name == 'mcp_pixiv_get_illust_detail'
        assert function_args == {'illust_id': 123}
        assert task_id == 'qq-task'
        return '{"illust_id": 123, "title": "Miku"}'

    monkeypatch.setattr('gateway.qqbot_lute_backends.handle_function_call', _fake_handle_function_call)

    result = await call_tool_backend('mcp_pixiv_get_illust_detail', {'illust_id': 123}, task_id='qq-task')

    assert result.text == 'Miku'
    assert result.payload == {'illust_id': 123, 'title': 'Miku'}
    assert result.telemetry_events[0]['event_type'] == 'external_api_call'
    assert result.telemetry_events[0]['api_name'] == 'tool.mcp_pixiv_get_illust_detail'
    assert result.telemetry_events[0]['success'] is True


@pytest.mark.asyncio
async def test_call_tool_backend_rejects_disallowed_tool_name(monkeypatch):
    monkeypatch.setattr(
        'gateway.qqbot_lute_backends.handle_function_call',
        lambda *args, **kwargs: pytest.fail('should not call tool'),
    )

    result = await call_tool_backend(
        'mcp_pixiv_get_illust_detail',
        {'illust_id': 123},
        allowed_tool_names={'mcp_bangumi_tv_get_daily_broadcast'},
    )

    assert 'not allowed' in result.text.lower()
    assert result.payload is None


@pytest.mark.asyncio
async def test_call_tool_backend_reports_handler_failure_cleanly(monkeypatch):
    def _boom(*args, **kwargs):
        raise RuntimeError('backend exploded')

    monkeypatch.setattr('gateway.qqbot_lute_backends.handle_function_call', _boom)

    result = await call_tool_backend('mcp_pixiv_get_illust_detail', {'illust_id': 123})

    assert 'failed' in result.text.lower()
    assert 'backend exploded' in result.text


@pytest.mark.asyncio
async def test_call_tool_backend_reports_timeout_cleanly(monkeypatch):
    def _slow(*args, **kwargs):
        time.sleep(0.2)
        return '{"ok": true}'

    monkeypatch.setattr('gateway.qqbot_lute_backends.handle_function_call', _slow)

    result = await call_tool_backend('mcp_pixiv_get_illust_detail', {'illust_id': 123}, timeout_sec=0.01)

    assert 'timed out' in result.text.lower()
    assert result.payload is None


@pytest.mark.asyncio
async def test_call_script_backend_parses_file_and_image_markers(tmp_path):
    script = tmp_path / 'emit_media.py'
    script.write_text(
        "print('hello from script')\nprint('FILE_PATH=/tmp/report.txt')\nprint('IMAGE_PATH=/tmp/card.webp')\n",
        encoding='utf-8',
    )

    result = await call_script_backend([sys.executable, str(script)], timeout_sec=5)

    assert 'hello from script' in result.text
    assert result.file_paths == ['/tmp/report.txt']
    assert result.media_paths == ['/tmp/card.webp']
    assert result.telemetry_events[0]['event_type'] == 'external_api_call'
    assert result.telemetry_events[0]['api_name'] == 'script.emit_media'
    assert result.telemetry_events[0]['success'] is True


@pytest.mark.asyncio
async def test_call_script_backend_parses_json_stdout(tmp_path):
    script = tmp_path / 'emit_json.py'
    script.write_text("import json\nprint(json.dumps({'title': 'Weekly free game', 'items': 2}))\n", encoding='utf-8')

    result = await call_script_backend([sys.executable, str(script)], timeout_sec=5)

    assert result.text == 'Weekly free game'
    assert result.payload == {'title': 'Weekly free game', 'items': 2}


@pytest.mark.asyncio
async def test_call_script_backend_logs_success_stderr_without_leaking_it_into_response(tmp_path):
    script = tmp_path / 'emit_stderr.py'
    script.write_text(
        "import sys\nprint('visible body')\nprint('[bangumi-rendered] cache hit: /tmp/out.jpg', file=sys.stderr)\n",
        encoding='utf-8',
    )

    with patch('gateway.qqbot_lute_backends.logger') as logger_mock:
        result = await call_script_backend([sys.executable, str(script)], timeout_sec=5)

    assert result.text == 'visible body'
    assert '[bangumi-rendered]' not in result.text
    logger_mock.info.assert_called()


@pytest.mark.asyncio
async def test_call_script_backend_reports_timeout_cleanly(tmp_path):
    script = tmp_path / 'slow.py'
    script.write_text('import time\ntime.sleep(2)\n', encoding='utf-8')

    result = await call_script_backend([sys.executable, str(script)], timeout_sec=0.1)

    assert 'timed out' in result.text.lower()
    assert result.file_paths == []
    assert result.media_paths == []


@pytest.mark.asyncio
async def test_call_script_backend_kills_timed_out_process(tmp_path):
    pid_file = tmp_path / 'pid.txt'
    script = tmp_path / 'slow_with_pid.py'
    script.write_text(
        (
            'import os\n'
            'import time\n'
            f'open({str(pid_file)!r}, "w", encoding="utf-8").write(str(os.getpid()))\n'
            'time.sleep(10)\n'
        ),
        encoding='utf-8',
    )

    result = await call_script_backend([sys.executable, str(script)], timeout_sec=0.1)

    assert 'timed out' in result.text.lower()
    pid = int(pid_file.read_text(encoding='utf-8').strip())
    with pytest.raises(OSError):
        os.kill(pid, 0)


@pytest.mark.asyncio
async def test_call_script_backend_returns_nonzero_exit_output(tmp_path):
    script = tmp_path / 'fail.py'
    script.write_text("import sys\nprint('bad things happened')\nsys.exit(3)\n", encoding='utf-8')

    result = await call_script_backend([sys.executable, str(script)], timeout_sec=5)

    assert 'exit code 3' in result.text.lower()
    assert 'bad things happened' in result.text


@pytest.mark.asyncio
async def test_call_script_backend_accepts_allowed_script_path_with_python_interpreter(tmp_path):
    script = tmp_path / 'emit.py'
    script.write_text("print('ok')\n", encoding='utf-8')

    result = await call_script_backend(
        [sys.executable, str(script)],
        timeout_sec=5,
        allowed_executables={str(script)},
    )

    assert result.text == 'ok'


@pytest.mark.asyncio
async def test_call_script_backend_rejects_disallowed_script_path_with_python_interpreter(tmp_path):
    script = tmp_path / 'emit.py'
    script.write_text("print('ok')\n", encoding='utf-8')
    other = tmp_path / 'other.py'
    other.write_text("print('other')\n", encoding='utf-8')

    result = await call_script_backend(
        [sys.executable, str(script)],
        timeout_sec=5,
        allowed_executables={str(other)},
    )

    assert 'not allowed' in result.text.lower()
    assert result.payload is None


@pytest.mark.asyncio
async def test_call_script_backend_handles_spawn_failure_cleanly(tmp_path):
    missing = tmp_path / 'missing-executable'

    result = await call_script_backend([str(missing)], timeout_sec=5)

    assert 'failed to start' in result.text.lower()
