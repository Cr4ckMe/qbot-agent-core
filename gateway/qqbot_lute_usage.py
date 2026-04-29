from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LuteUsageEntry:
    syntax: str
    description: str


def render_single_usage(syntax: str) -> str:
    return f"Usage: {str(syntax).strip()}"


def render_usage_list(entries: list[LuteUsageEntry]) -> str:
    lines = ['Usage:']
    for entry in entries:
        syntax = str(entry.syntax).strip()
        description = str(entry.description).strip()
        if not syntax:
            continue
        if description:
            lines.append(f'- {syntax}  {description}')
        else:
            lines.append(f'- {syntax}')
    return '\n'.join(lines)
