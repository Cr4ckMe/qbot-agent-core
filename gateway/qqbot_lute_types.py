from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from gateway.view.types import ViewSpec


@dataclass
class LuteInvocation:
    root: str
    domain: str
    verb: str
    command: str = ''
    args: list[str] = field(default_factory=list)
    options: dict[str, Any] = field(default_factory=dict)
    raw_text: str = ''
    current_chat_id: str = ''
    current_chat_type: str = ''
    reply_to_message_id: str = ''
    reply_to_text: str = ''
    referenced_media_urls: list[str] = field(default_factory=list)
    referenced_media_types: list[str] = field(default_factory=list)


@dataclass
class LuteResponse:
    text: str
    media_paths: list[str] = field(default_factory=list)
    file_paths: list[str] = field(default_factory=list)
    follow_hint: str = ''
    payload: dict[str, Any] | list[Any] | None = None
    view: 'ViewSpec | None' = None
    telemetry_events: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class LuteVerbSpec:
    name: str
    summary: str
    example: str
    access: str = 'user'
    help_group: str = ''


@dataclass
class LuteDomainSpec:
    name: str
    summary: str
    access: str = 'user'
    visible_in_help: bool = True
    task_label: str = ''
    task_summary: str = ''
    examples: list[str] = field(default_factory=list)
    verbs: dict[str, LuteVerbSpec] = field(default_factory=dict)
