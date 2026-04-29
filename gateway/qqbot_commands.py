"""QQ bot command registry, parsers, and dispatch helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable

import yaml

from gateway.platforms.base import MessageEvent
from gateway.qqbot_config import QQBotCLIDomainConfig, QQBotConfig
from gateway.qqbot_policy import QQBotPolicy
from gateway.status import read_runtime_status
from gateway.config import load_gateway_config
from hermes_constants import get_hermes_home
from utils import atomic_yaml_write

QQCommandHandler = Callable[[MessageEvent, QQBotPolicy, list[str]], Awaitable[str]]


def _build_command_lookup(commands: dict[str, "QQCommandDef"]) -> dict[str, "QQCommandDef"]:
    lookup: dict[str, QQCommandDef] = {}
    for command in commands.values():
        lookup[command.name] = command
        for alias in command.aliases:
            lookup[alias] = command
    return lookup


@dataclass(frozen=True)
class QQCommandDef:
    """Definition of a QQ product command."""

    name: str
    description: str
    handler: QQCommandHandler
    aliases: tuple[str, ...] = ()
    args_hint: str = ""


@dataclass(frozen=True)
class QQCommandDispatchResult:
    """Structured result from QQ command routing."""

    status: str
    message: str | None = None
    command_name: str | None = None


async def _handle_help_command(event: MessageEvent, policy: QQBotPolicy, args: list[str]) -> str:
    del event, policy, args
    lines = ["QQ bot commands:"]
    for command in get_public_qq_commands().values():
        lines.append(_format_public_command_line(command))
    return "\n".join(lines)


def _render_public_help(commands: dict[str, QQCommandDef]) -> str:
    lines = ["QQ bot commands:"]
    for command in commands.values():
        lines.append(_format_public_command_line(command))
    return "\n".join(lines)


async def _handle_ping_command(event: MessageEvent, policy: QQBotPolicy, args: list[str]) -> str:
    del event, policy, args
    return "pong"


async def _handle_admin_status_command(event: MessageEvent, policy: QQBotPolicy, args: list[str]) -> str:
    del args
    user_id = _user_id_from_event(event)
    lines = [
        "QQ admin status",
        f"user_id: {user_id or '-'}",
        f"is_admin: {'yes' if policy.is_admin(user_id) else 'no'}",
        f"predefined_commands: {'yes' if policy.can_use_predefined_commands(user_id) else 'no'}",
        f"llm: {'yes' if policy.can_use_llm(user_id) else 'no'}",
        f"allow_users: {_format_identifier_list(policy.config.access.allow_users)}",
        f"allow_groups: {_format_identifier_list(policy.config.access.allow_groups)}",
        f"group_user_allowlist: {_format_group_user_allowlist(policy.config.access.group_user_allowlist)}",
        f"llm_users: {_format_identifier_list(policy.config.capabilities.llm_users)}",
    ]
    return "\n".join(lines)


async def _handle_admin_allow_command(event: MessageEvent, policy: QQBotPolicy, args: list[str]) -> str:
    usage = (
        "Usage: /admin allow user add|remove <qq_id> OR "
        "/admin allow group add|remove <group_id> OR /admin allow group OR /admin allow group remove OR "
        "/admin allow group-user add|remove <qq_id> OR /admin allow group-user add|remove <group_id> <qq_id>"
    )
    current_group_id = _current_group_id_from_event(event)

    if len(args) == 1 and args[0].lower() == "group":
        if current_group_id is None:
            return usage
        existing_values = list(policy.config.access.allow_groups)
        updated_config = _update_persisted_qqbot_identifier_list(
            current_config=policy.config,
            section_name="access",
            list_name="allow_groups",
            target_id=current_group_id,
            action="add",
        )
        _refresh_policy(policy, updated_config)
        if current_group_id in existing_values:
            return f"QQ group {current_group_id} is already in allow_groups. No change."
        return f"QQ group {current_group_id} added to allow_groups."

    if len(args) == 2 and args[0].lower() == "group" and args[1].lower() == "remove":
        if current_group_id is None:
            return usage
        existing_values = list(policy.config.access.allow_groups)
        updated_config = _update_persisted_qqbot_identifier_list(
            current_config=policy.config,
            section_name="access",
            list_name="allow_groups",
            target_id=current_group_id,
            action="remove",
        )
        _refresh_policy(policy, updated_config)
        if current_group_id in existing_values:
            return f"QQ group {current_group_id} removed from allow_groups."
        return f"QQ group {current_group_id} was not in allow_groups. No change."

    if len(args) == 3:
        target_kind = args[0].lower()
        action = args[1].lower()
        target_id = _normalize_identifier(args[2])

        if target_kind == "group-user" and action in {"add", "remove"}:
            if current_group_id is None or target_id is None:
                return usage
            if current_group_id not in set(policy.config.access.allow_groups):
                return f"QQ group {current_group_id} is not allowlisted. Run /admin allow group first."
            existing_values = list(policy.config.access.group_user_allowlist.get(current_group_id, []))
            updated_config = _update_persisted_qqbot_group_user_allowlist(
                current_config=policy.config,
                group_id=current_group_id,
                target_id=target_id,
                action=action,
            )
            _refresh_policy(policy, updated_config)
            if action == "add":
                if target_id in existing_values:
                    return f"QQ user {target_id} is already in group_user_allowlist[{current_group_id}]. No change."
                return f"QQ user {target_id} added to group_user_allowlist[{current_group_id}]."
            if target_id in existing_values:
                return f"QQ user {target_id} removed from group_user_allowlist[{current_group_id}]."
            return f"QQ user {target_id} was not in group_user_allowlist[{current_group_id}]. No change."

        if target_kind not in {"user", "group"} or action not in {"add", "remove"} or target_id is None:
            return usage

        list_name = "allow_users" if target_kind == "user" else "allow_groups"
        existing_values = list(getattr(policy.config.access, list_name))
        updated_config = _update_persisted_qqbot_identifier_list(
            current_config=policy.config,
            section_name="access",
            list_name=list_name,
            target_id=target_id,
            action=action,
        )
        _refresh_policy(policy, updated_config)

        if action == "add":
            if target_id in existing_values:
                return f"QQ {target_kind} {target_id} is already in {list_name}. No change."
            return f"QQ {target_kind} {target_id} added to {list_name}."
        if target_id in existing_values:
            return f"QQ {target_kind} {target_id} removed from {list_name}."
        return f"QQ {target_kind} {target_id} was not in {list_name}. No change."

    if len(args) == 4:
        target_kind = args[0].lower()
        action = args[1].lower()
        group_id = _normalize_identifier(args[2])
        target_id = _normalize_identifier(args[3])
        if target_kind != "group-user" or action not in {"add", "remove"} or group_id is None or target_id is None:
            return usage
        if group_id not in set(policy.config.access.allow_groups):
            return f"QQ group {group_id} is not allowlisted. Run /admin allow group first."

        existing_values = list(policy.config.access.group_user_allowlist.get(group_id, []))
        updated_config = _update_persisted_qqbot_group_user_allowlist(
            current_config=policy.config,
            group_id=group_id,
            target_id=target_id,
            action=action,
        )
        _refresh_policy(policy, updated_config)

        if action == "add":
            if target_id in existing_values:
                return f"QQ user {target_id} is already in group_user_allowlist[{group_id}]. No change."
            return f"QQ user {target_id} added to group_user_allowlist[{group_id}]."
        if target_id in existing_values:
            return f"QQ user {target_id} removed from group_user_allowlist[{group_id}]."
        return f"QQ user {target_id} was not in group_user_allowlist[{group_id}]. No change."

    return usage


async def _handle_admin_llm_command(event: MessageEvent, policy: QQBotPolicy, args: list[str]) -> str:
    del event
    if len(args) != 2:
        return "Usage: /admin llm grant|revoke <qq_id>"

    action = args[0].lower()
    target_id = _normalize_identifier(args[1])
    if action not in {"grant", "revoke"} or target_id is None:
        return "Usage: /admin llm grant|revoke <qq_id>"

    existing_values = list(policy.config.capabilities.llm_users)
    updated_config = _update_persisted_qqbot_identifier_list(
        current_config=policy.config,
        section_name="capabilities",
        list_name="llm_users",
        target_id=target_id,
        action="add" if action == "grant" else "remove",
    )
    _refresh_policy(policy, updated_config)

    if action == "grant":
        if target_id in existing_values:
            return f"QQ user {target_id} already has LLM access. No change."
        return f"LLM access granted to QQ user {target_id}."
    if target_id in existing_values:
        return f"LLM access revoked from QQ user {target_id}."
    return f"QQ user {target_id} did not have LLM access. No change."


_FEATURE_MODULES: dict[str, tuple[str, ...]] = {
    "信息查询": ("bili", "utility"),
    "info": ("bili", "utility"),
    "information": ("bili", "utility"),
    "information-lookup": ("bili", "utility"),
    "资源搜索": ("pixiv", "bangumi", "torrent", "book"),
    "resources": ("pixiv", "bangumi", "torrent", "book"),
    "订阅通知": ("feed",),
    "subscriptions": ("feed",),
    "多媒体": ("image",),
    "media": ("image",),
    "娱乐休闲": ("epic",),
    "entertainment": ("epic",),
    "实用工具": ("utility",),
    "utility": ("utility",),
}


async def _handle_admin_feature_command(event: MessageEvent, policy: QQBotPolicy, args: list[str]) -> str:
    del event
    usage = "Usage: /admin feature list OR /admin feature enable|disable <domain|module|all>"
    normalized_args = [str(arg).strip() for arg in args if str(arg).strip()]
    if not normalized_args:
        normalized_args = ["list"]

    action = normalized_args[0].lower()
    if action == "list" and len(normalized_args) == 1:
        return _render_feature_toggle_status(policy.config)

    if action not in {"enable", "disable"} or len(normalized_args) != 2:
        return usage

    target = normalized_args[1]
    domain_names, target_kind, display_name = _resolve_feature_target(target)
    if not domain_names:
        known_modules = ", ".join(_FEATURE_MODULES.keys())
        known_domains = ", ".join(_ordinary_feature_domain_names())
        return f"Unknown QQBot feature target: {target}. Domains: {known_domains}. Modules: {known_modules}."

    enable = action == "enable"
    updated_config = _update_persisted_qqbot_feature_domains(
        current_config=policy.config,
        domain_names=domain_names,
        enable=enable,
    )
    _refresh_policy(policy, updated_config)

    if target_kind == "all":
        state = "enabled" if enable else "disabled"
        return f"All ordinary-user feature domains {state}."
    if target_kind == "module":
        state = "enabled" if enable else "disabled"
        return f"Feature module {display_name} {state} for ordinary users: {', '.join(domain_names)}."
    state = "enabled" if enable else "disabled"
    return f"Feature {domain_names[0]} {state} for ordinary users."


async def _handle_admin_runtime_command(event: MessageEvent, policy: QQBotPolicy, args: list[str]) -> str:
    del event
    normalized_args = [arg.lower() for arg in args]
    if normalized_args not in (["show"], ["status"]):
        return "Usage: /admin runtime show|status"

    runtime_cfg = policy.config.runtime
    status_payload = read_runtime_status() or {}
    runtime_platforms = status_payload.get("platforms") or {}
    napcat_status = runtime_platforms.get("qqbot") or runtime_platforms.get(policy.config.platform, {})
    enabled_toolsets = ", ".join(runtime_cfg.enabled_toolsets) if runtime_cfg.enabled_toolsets else "-"
    lines = [
        "QQ admin runtime",
        f"mode: {runtime_cfg.mode or '-'}",
        f"model: {runtime_cfg.model or '-'}",
        f"provider: {runtime_cfg.provider or '-'}",
        f"enabled_toolsets: {enabled_toolsets}",
        f"smart_routing: {'yes' if runtime_cfg.enable_smart_routing else 'no'}",
        f"max_iterations: {runtime_cfg.max_iterations}",
        f"streaming: {'disabled' if runtime_cfg.disable_streaming else 'enabled'}",
        f"reasoning: {'disabled' if runtime_cfg.disable_reasoning else 'enabled'}",
        f"tool_progress: {'disabled' if runtime_cfg.disable_tool_progress else 'enabled'}",
        f"gateway_state: {status_payload.get('gateway_state', '-')}",
        f"restart_requested: {'yes' if status_payload.get('restart_requested') else 'no'}",
        f"active_agents: {status_payload.get('active_agents', 0)}",
        f"napcat_state: {napcat_status.get('state', '-')}",
    ]
    return "\n".join(lines)


async def _handle_admin_reload_command(event: MessageEvent, policy: QQBotPolicy, args: list[str]) -> str:
    if args:
        return "Usage: /admin reload"

    reloaded_config = load_gateway_config().qqbot
    current_admin = _user_id_from_event(event)
    reload_error = _validate_reload_candidate(reloaded_config, current_admin)
    if reload_error is not None:
        return f"QQ bot config reload failed: {reload_error}"

    _refresh_policy(policy, reloaded_config)

    return (
        "QQ bot config reloaded from disk. "
        f"model={policy.config.runtime.model or '-'} "
        f"provider={policy.config.runtime.provider or '-'}"
    )


def _validate_reload_candidate(config: QQBotConfig, current_admin: str | None) -> str | None:
    if not config.enabled:
        return "qqbot config is missing or disabled"
    if config.platform != "napcat":
        return f"unsupported qqbot platform: {config.platform or '-'}"
    if current_admin is None or not QQBotPolicy(config).is_admin(current_admin):
        return "reloaded config would remove your admin access"
    return None


_PUBLIC_COMMANDS: dict[str, QQCommandDef] = {
    "help": QQCommandDef(
        name="help",
        description="Show public QQ bot commands",
        handler=_handle_help_command,
        aliases=("menu",),
    ),
    "ping": QQCommandDef(
        name="ping",
        description="Check whether the QQ bot is responsive",
        handler=_handle_ping_command,
    ),
}

_ADMIN_COMMANDS: dict[str, QQCommandDef] = {
    "allow": QQCommandDef(
        name="allow",
        description="Manage QQ allowlists for users, groups, and group users",
        handler=_handle_admin_allow_command,
        args_hint="user add|remove <qq_id> | group add|remove <group_id> | group | group remove | group-user add|remove <qq_id> | group-user add|remove <group_id> <qq_id>",
    ),
    "llm": QQCommandDef(
        name="llm",
        description="Grant or revoke QQ LLM access",
        handler=_handle_admin_llm_command,
        args_hint="grant|revoke <qq_id>",
    ),
    "feature": QQCommandDef(
        name="feature",
        description="Manage ordinary-user Lute feature toggles",
        handler=_handle_admin_feature_command,
        args_hint="list | enable|disable <domain|module|all>",
    ),
    "reload": QQCommandDef(
        name="reload",
        description="Reload QQ bot config from disk",
        handler=_handle_admin_reload_command,
    ),
    "runtime": QQCommandDef(
        name="runtime",
        description="Show QQ bot runtime settings and gateway status",
        handler=_handle_admin_runtime_command,
        args_hint="show",
    ),
    "status": QQCommandDef(
        name="status",
        description="Show QQ admin runtime status",
        handler=_handle_admin_status_command,
        aliases=("stat",),
    ),
}

def parse_qq_command(text: str) -> tuple[str | None, str]:
    """Parse a QQ slash command into command name and raw argument text."""
    candidate = (text or "").strip()
    if not candidate.startswith("/"):
        return None, candidate

    parts = candidate.split(maxsplit=1)
    command = parts[0][1:].lower() if parts else ""
    if "@" in command:
        command = command.split("@", 1)[0]
    if not command or "/" in command:
        return None, candidate

    return command, parts[1].strip() if len(parts) > 1 else ""


def parse_qq_admin_command(text: str) -> tuple[str | None, list[str]]:
    """Parse a QQ admin command like ``/admin status``."""
    command, raw_args = parse_qq_command(text)
    if command != "admin":
        return None, []

    args = raw_args.split()
    if not args:
        return None, []
    return args[0].lower(), args[1:]


def get_public_qq_commands() -> dict[str, QQCommandDef]:
    """Return canonical public QQ commands."""
    return dict(_PUBLIC_COMMANDS)


def get_admin_qq_commands() -> dict[str, QQCommandDef]:
    """Return canonical admin QQ commands."""
    return dict(_ADMIN_COMMANDS)


async def dispatch_public_qq_command(
    event: MessageEvent,
    *,
    policy: QQBotPolicy,
    commands: dict[str, QQCommandDef] | None = None,
) -> QQCommandDispatchResult:
    """Dispatch a public QQ command for an approved user."""
    command_name, raw_args = parse_qq_command(event.text)
    if command_name is None:
        return QQCommandDispatchResult(status="not_command")
    if command_name == "admin":
        return QQCommandDispatchResult(status="not_applicable", command_name=command_name)

    if not policy.can_use_predefined_commands(_user_id_from_event(event)):
        return QQCommandDispatchResult(
            status="denied",
            command_name=command_name,
            message="This QQ bot account only allows predefined commands for your role.",
        )

    registry = _PUBLIC_COMMANDS if commands is None else commands
    command_def = _build_command_lookup(registry).get(command_name)
    if command_def is None:
        return QQCommandDispatchResult(status="not_applicable", command_name=command_name)

    if command_def.name == "help":
        message = _render_public_help(registry)
    else:
        message = await command_def.handler(event, policy, raw_args.split())
    return QQCommandDispatchResult(
        status="handled",
        command_name=command_def.name,
        message=message,
    )


async def dispatch_admin_qq_command(
    event: MessageEvent,
    *,
    policy: QQBotPolicy,
    commands: dict[str, QQCommandDef] | None = None,
) -> QQCommandDispatchResult:
    """Dispatch a QQ admin command."""
    command_name, args = parse_qq_admin_command(event.text)
    if command_name is None:
        return QQCommandDispatchResult(status="not_applicable")

    if not policy.can_use_admin_slash(_user_id_from_event(event)):
        return QQCommandDispatchResult(
            status="denied",
            command_name=command_name,
            message="Permission denied: this command is admin-only.",
        )

    registry = _ADMIN_COMMANDS if commands is None else commands
    command_def = _build_command_lookup(registry).get(command_name)
    if command_def is None:
        return QQCommandDispatchResult(
            status="unknown",
            command_name=command_name,
            message=(
                f"Unknown QQ admin command: /admin {command_name}.\n"
                f"Available admin commands:\n{_format_admin_command_help(registry)}"
            ),
        )

    message = await command_def.handler(event, policy, args)
    return QQCommandDispatchResult(
        status="handled",
        command_name=command_def.name,
        message=message,
    )


def _format_public_command_line(command: QQCommandDef) -> str:
    alias_text = ""
    if command.aliases:
        alias_text = f" (aliases: {', '.join(f'/{alias}' for alias in command.aliases)})"
    args_hint = f" {command.args_hint}" if command.args_hint else ""
    return f"/{command.name}{args_hint} — {command.description}{alias_text}"


def _format_admin_command_help(commands: dict[str, QQCommandDef] | None = None) -> str:
    registry = get_admin_qq_commands() if commands is None else commands
    lines: list[str] = []
    for command in registry.values():
        alias_text = ""
        if command.aliases:
            alias_text = f" (aliases: {', '.join(f'/admin {alias}' for alias in command.aliases)})"
        args_hint = f" {command.args_hint}" if command.args_hint else ""
        lines.append(f"/admin {command.name}{args_hint} — {command.description}{alias_text}")
    return "\n".join(lines)


def _user_id_from_event(event: MessageEvent) -> str | None:
    source = getattr(event, "source", None)
    if source is None:
        return None
    return getattr(source, "user_id", None)


def _current_group_id_from_event(event: MessageEvent) -> str | None:
    source = getattr(event, "source", None)
    if source is None:
        return None
    if str(getattr(source, "chat_type", "") or "").strip().lower() != "group":
        return None
    return _normalize_identifier(getattr(source, "chat_id", None))


def _format_identifier_list(values: list[str]) -> str:
    if not values:
        return "-"
    return ", ".join(values)


def _format_group_user_allowlist(mapping: dict[str, list[str]] | None) -> str:
    if not mapping:
        return "-"
    parts: list[str] = []
    for group_id in sorted(mapping):
        values = mapping.get(group_id) or []
        if values:
            parts.append(f"{group_id}=[{', '.join(values)}]")
    return "; ".join(parts) if parts else "-"


def _normalize_identifier(value: str | None) -> str | None:
    if value is None:
        return None
    candidate = str(value).strip()
    if not candidate:
        return None
    return candidate


def _update_persisted_qqbot_identifier_list(
    *,
    current_config: QQBotConfig,
    section_name: str,
    list_name: str,
    target_id: str,
    action: str,
) -> QQBotConfig:
    config_path = get_hermes_home() / "config.yaml"
    user_config = _load_user_config_yaml(config_path)
    effective_config = current_config

    section = getattr(effective_config, section_name)
    existing_values = list(getattr(section, list_name))
    updated_values = _apply_identifier_list_action(existing_values, target_id, action)
    setattr(section, list_name, updated_values)

    qqbot_yaml = user_config.get("qqbot")
    if not isinstance(qqbot_yaml, dict):
        qqbot_yaml = {}
        user_config["qqbot"] = qqbot_yaml

    section_yaml = qqbot_yaml.get(section_name)
    if not isinstance(section_yaml, dict):
        section_yaml = {}
        qqbot_yaml[section_name] = section_yaml

    section_yaml[list_name] = list(updated_values)

    if section_name == "access" and list_name == "allow_groups" and action == "remove":
        current_mapping = {key: list(values) for key, values in effective_config.access.group_user_allowlist.items()}
        current_mapping.pop(target_id, None)
        effective_config.access.group_user_allowlist = current_mapping

        mapping_yaml = section_yaml.get("group_user_allowlist")
        if isinstance(mapping_yaml, dict):
            mapping_yaml.pop(target_id, None)

    atomic_yaml_write(config_path, user_config)
    return effective_config


def _update_persisted_qqbot_group_user_allowlist(
    *,
    current_config: QQBotConfig,
    group_id: str,
    target_id: str,
    action: str,
) -> QQBotConfig:
    config_path = get_hermes_home() / "config.yaml"
    user_config = _load_user_config_yaml(config_path)
    effective_config = current_config

    access_cfg = effective_config.access
    current_mapping = {key: list(values) for key, values in access_cfg.group_user_allowlist.items()}
    existing_values = list(current_mapping.get(group_id, []))
    updated_values = _apply_identifier_list_action(existing_values, target_id, action)
    if updated_values:
        current_mapping[group_id] = updated_values
    else:
        current_mapping.pop(group_id, None)
    access_cfg.group_user_allowlist = current_mapping

    qqbot_yaml = user_config.get("qqbot")
    if not isinstance(qqbot_yaml, dict):
        qqbot_yaml = {}
        user_config["qqbot"] = qqbot_yaml

    access_yaml = qqbot_yaml.get("access")
    if not isinstance(access_yaml, dict):
        access_yaml = {}
        qqbot_yaml["access"] = access_yaml

    mapping_yaml = access_yaml.get("group_user_allowlist")
    if not isinstance(mapping_yaml, dict):
        mapping_yaml = {}
        access_yaml["group_user_allowlist"] = mapping_yaml

    if updated_values:
        mapping_yaml[group_id] = list(updated_values)
    else:
        mapping_yaml.pop(group_id, None)

    atomic_yaml_write(config_path, user_config)
    return effective_config


def _update_persisted_qqbot_feature_domains(
    *,
    current_config: QQBotConfig,
    domain_names: tuple[str, ...],
    enable: bool,
) -> QQBotConfig:
    config_path = get_hermes_home() / "config.yaml"
    user_config = _load_user_config_yaml(config_path)
    effective_config = current_config

    qqbot_yaml = user_config.get("qqbot")
    if not isinstance(qqbot_yaml, dict):
        qqbot_yaml = {}
        user_config["qqbot"] = qqbot_yaml

    cli_yaml = qqbot_yaml.get("cli")
    if not isinstance(cli_yaml, dict):
        cli_yaml = {}
        qqbot_yaml["cli"] = cli_yaml

    domains_yaml = cli_yaml.get("domains")
    if not isinstance(domains_yaml, dict):
        domains_yaml = {}
        cli_yaml["domains"] = domains_yaml

    for domain_name in domain_names:
        if domain_name not in _ordinary_feature_domain_names():
            continue
        existing_domain_config = effective_config.cli.domains.get(domain_name)
        if existing_domain_config is None:
            domain_config = QQBotCLIDomainConfig(enabled=True)
        else:
            domain_config = QQBotCLIDomainConfig(
                enabled=existing_domain_config.enabled,
                access=existing_domain_config.access,
                visible_in_help=existing_domain_config.visible_in_help,
            )
        domain_config.access = "user" if enable else "admin"
        domain_config.visible_in_help = enable
        effective_config.cli.domains[domain_name] = domain_config
        domains_yaml[domain_name] = domain_config.to_dict()

    atomic_yaml_write(config_path, user_config)
    return effective_config


def _ordinary_feature_domain_names() -> tuple[str, ...]:
    from gateway.qqbot_lute_registry import _DEFAULT_DOMAINS

    return tuple(name for name, spec in _DEFAULT_DOMAINS.items() if spec.access != "admin")


def _resolve_feature_target(target: str) -> tuple[tuple[str, ...], str, str]:
    normalized = str(target).strip()
    if not normalized:
        return (), "", normalized
    lowered = normalized.lower()
    ordinary_domains = _ordinary_feature_domain_names()
    if lowered == "all" or normalized == "全部":
        return ordinary_domains, "all", normalized
    if normalized in _FEATURE_MODULES:
        return tuple(domain for domain in _FEATURE_MODULES[normalized] if domain in ordinary_domains), "module", normalized
    if lowered in _FEATURE_MODULES:
        return tuple(domain for domain in _FEATURE_MODULES[lowered] if domain in ordinary_domains), "module", normalized
    if lowered in ordinary_domains:
        return (lowered,), "domain", lowered
    return (), "", normalized


def _feature_domain_enabled_for_users(config: QQBotConfig, domain_name: str) -> bool:
    override = config.cli.domains.get(domain_name)
    if override is None:
        return True
    if not override.enabled:
        return False
    return override.access != "admin"


def _render_feature_toggle_status(config: QQBotConfig) -> str:
    lines = ["QQBot feature toggles for ordinary users"]
    for domain_name in _ordinary_feature_domain_names():
        status = "enabled" if _feature_domain_enabled_for_users(config, domain_name) else "disabled"
        lines.append(f"{domain_name}: {status}")
    lines.append("modules: " + "; ".join(f"{name}={', '.join(domains)}" for name, domains in _FEATURE_MODULES.items()))
    return "\n".join(lines)


def _load_user_config_yaml(config_path) -> dict:
    if not config_path.exists():
        return {}

    with config_path.open("r", encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle) or {}
    if isinstance(loaded, dict):
        return loaded
    return {}


def _apply_identifier_list_action(values: list[str], target_id: str, action: str) -> list[str]:
    normalized_values = [_normalize_identifier(value) for value in values]
    clean_values = [value for value in normalized_values if value is not None]
    if action == "add":
        if target_id not in clean_values:
            clean_values.append(target_id)
        return clean_values
    return [value for value in clean_values if value != target_id]


def _refresh_policy(policy: QQBotPolicy, config: QQBotConfig) -> None:
    QQBotPolicy.__init__(policy, config)
