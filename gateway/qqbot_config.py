"""Typed QQ bot product configuration and normalization helpers."""

from dataclasses import dataclass, field
from typing import Any, Dict

from gateway.feed_card_render_options import normalize_feed_card_render_options


_TRUE_VALUES = {"true", "1", "yes", "on"}
_FALSE_VALUES = {"false", "0", "no", "off"}


def _coerce_bool(value: Any, default: bool) -> bool:
    """Coerce only explicit bool-like values, otherwise preserve the default."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in _TRUE_VALUES:
            return True
        if normalized in _FALSE_VALUES:
            return False
    return default



def _normalize_string_id_list(value: Any) -> list[str]:
    """Normalize QQ numeric/string identifiers into stripped string lists."""
    if not isinstance(value, (list, tuple, set)):
        return []

    normalized: list[str] = []
    for item in value:
        if item is None:
            continue
        if isinstance(item, (list, tuple, set, dict)):
            continue
        candidate = str(item).strip()
        if candidate:
            normalized.append(candidate)
    return normalized



def _normalize_string_list(value: Any) -> list[str]:
    """Normalize a list of plain strings."""
    if not isinstance(value, (list, tuple, set)):
        return []

    normalized: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        candidate = item.strip()
        if candidate:
            normalized.append(candidate)
    return normalized



def _normalize_prefixes(value: Any) -> list[str]:
    """Normalize command prefixes while preserving order and safe defaults."""
    values = value
    if isinstance(value, str):
        values = [value]

    prefixes: list[str] = []
    seen: set[str] = set()
    for prefix in _normalize_string_list(values):
        if prefix in seen:
            continue
        seen.add(prefix)
        prefixes.append(prefix)

    return prefixes or ["/"]



def _normalize_string_list_mapping(value: Any) -> dict[str, list[str]]:
    """Normalize mapping[str, list[id]] while dropping invalid keys/items."""
    if not isinstance(value, dict):
        return {}

    normalized: dict[str, list[str]] = {}
    for raw_key, raw_values in value.items():
        if raw_key is None:
            continue
        key = str(raw_key).strip()
        if not key:
            continue
        values = _normalize_string_id_list(raw_values)
        if values:
            normalized[key] = values
    return normalized



def _normalize_runtime_int(value: Any, default: int, minimum: int = 1) -> int:
    """Normalize runtime integer values with a lower bound."""
    if isinstance(value, bool):
        return default
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return default
    if normalized < minimum:
        return default
    return normalized



def _normalize_platform(value: Any, default: str = "napcat") -> str:
    """Normalize configured QQ platform names."""
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized:
            return normalized
    return default



def _normalize_unauthorized_dm_behavior(value: Any, default: str = "ignore") -> str:
    """Normalize QQ-bot unauthorized-DM behavior to supported values."""
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"ignore", "pair"}:
            return normalized
    return default



def _normalize_runtime_mode(value: Any, default: str = "qqbot") -> str:
    """Normalize QQ runtime mode strings."""
    if isinstance(value, str):
        normalized = value.strip()
        if normalized:
            return normalized
    return default



def _normalize_optional_string(value: Any, default: str = "") -> str:
    """Normalize optional string settings with blank fallback."""
    if isinstance(value, str):
        return value.strip()
    return default


def _normalize_cli_command_token(value: Any, default: str) -> str:
    """Normalize CLI command/domain tokens to lowercase safe strings."""
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized.startswith('/'):
            normalized = normalized[1:].strip()
        if normalized and all(not ch.isspace() for ch in normalized):
            return normalized
    return default


def _normalize_cli_access(value: Any, default: str = "user") -> str:
    """Normalize CLI access levels to supported values."""
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"user", "admin"}:
            return normalized
    return default


def _normalize_cli_aliases(value: Any) -> list[str]:
    """Normalize CLI alias tokens while preserving order and uniqueness."""
    aliases: list[str] = []
    seen: set[str] = set()
    for alias in _normalize_string_list(value):
        normalized = _normalize_cli_command_token(alias, "")
        if not normalized:
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        aliases.append(normalized)
    return aliases


def _normalize_cli_int(value: Any, default: int, minimum: int = 1, maximum: int | None = None) -> int:
    """Normalize CLI integer values with optional upper bound."""
    normalized = _normalize_runtime_int(value, default, minimum=minimum)
    if maximum is not None and normalized > maximum:
        return default
    return normalized


def _normalize_enum_string(value: Any, allowed: set[str], default: str) -> str:
    """Normalize a constrained lowercase string setting."""
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in allowed:
            return normalized
    return default


def _normalize_optional_path_string(value: Any, default: str | None = None) -> str | None:
    """Normalize optional filesystem path-like strings without expanding them."""
    if value is None:
        return default
    if isinstance(value, str):
        normalized = value.strip()
        return normalized or default
    return default


def _normalize_positive_optional_int(value: Any, default: int | None = None) -> int | None:
    """Normalize optional positive integers while rejecting bools and non-positive values."""
    if value is None:
        return default
    if isinstance(value, bool):
        return default
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return default
    if normalized < 1:
        return default
    return normalized


def _normalize_positive_float(value: Any, default: float, minimum: float = 0.1, maximum: float | None = None) -> float:
    """Normalize positive float settings while rejecting bools and out-of-range values."""
    if value is None or isinstance(value, bool):
        return default
    try:
        normalized = float(value)
    except (TypeError, ValueError):
        return default
    if normalized < minimum:
        return default
    if maximum is not None and normalized > maximum:
        return default
    return normalized


def _normalize_domain_token_list(value: Any) -> list[str]:
    """Normalize domain/template tokens while preserving order and uniqueness."""
    domains: list[str] = []
    seen: set[str] = set()
    for item in _normalize_string_list(value):
        normalized = _normalize_cli_command_token(item, "")
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        domains.append(normalized)
    return domains


def _normalize_domain_recall_mapping(value: Any) -> dict[str, int | None]:
    """Normalize per-domain recall seconds; null disables recall for that domain."""
    if not isinstance(value, dict):
        return {}
    normalized: dict[str, int | None] = {}
    for raw_key, raw_value in value.items():
        key = _normalize_cli_command_token(raw_key, "")
        if not key:
            continue
        if raw_value is None:
            normalized[key] = None
            continue
        seconds = _normalize_positive_optional_int(raw_value, None)
        if seconds is not None:
            normalized[key] = seconds
    return normalized


@dataclass
class QQBotCLIDomainConfig:
    enabled: bool = True
    access: str = "user"
    visible_in_help: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "access": self.access,
            "visible_in_help": self.visible_in_help,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any] | None) -> "QQBotCLIDomainConfig":
        if not isinstance(data, dict):
            return cls()
        return cls(
            enabled=_coerce_bool(data.get("enabled"), True),
            access=_normalize_cli_access(data.get("access"), "user"),
            visible_in_help=_coerce_bool(data.get("visible_in_help"), True),
        )


def _normalize_cli_domain_mapping(value: Any) -> dict[str, QQBotCLIDomainConfig]:
    """Normalize mapping[str, QQBotCLIDomainConfig] while dropping blank keys."""
    if not isinstance(value, dict):
        return {}

    normalized: dict[str, QQBotCLIDomainConfig] = {}
    for raw_key, raw_values in value.items():
        key = _normalize_cli_command_token(raw_key, "")
        if not key or not isinstance(raw_values, dict):
            continue
        normalized[key] = QQBotCLIDomainConfig.from_dict(raw_values)
    return normalized


@dataclass
class QQBotCLIConfig:
    root_command: str = "lute"
    aliases: list[str] = field(default_factory=list)
    enable_legacy_bare_commands: bool = False
    enable_raw_hermes_slash: bool = False
    default_page_size: int = 5
    max_page_size: int = 10
    default_timeout_sec: int = 45
    domains: dict[str, QQBotCLIDomainConfig] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "root_command": self.root_command,
            "aliases": list(self.aliases),
            "enable_legacy_bare_commands": self.enable_legacy_bare_commands,
            "enable_raw_hermes_slash": self.enable_raw_hermes_slash,
            "default_page_size": self.default_page_size,
            "max_page_size": self.max_page_size,
            "default_timeout_sec": self.default_timeout_sec,
            "domains": {key: value.to_dict() for key, value in self.domains.items()},
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any] | None) -> "QQBotCLIConfig":
        if not isinstance(data, dict):
            return cls()

        default_page_size = _normalize_cli_int(data.get("default_page_size"), 5, minimum=1, maximum=50)
        max_page_size = _normalize_cli_int(data.get("max_page_size"), 10, minimum=1, maximum=50)
        if max_page_size < default_page_size:
            max_page_size = default_page_size

        return cls(
            root_command=_normalize_cli_command_token(data.get("root_command"), "lute"),
            aliases=_normalize_cli_aliases(data.get("aliases")),
            enable_legacy_bare_commands=_coerce_bool(data.get("enable_legacy_bare_commands"), False),
            enable_raw_hermes_slash=_coerce_bool(data.get("enable_raw_hermes_slash"), False),
            default_page_size=default_page_size,
            max_page_size=max_page_size,
            default_timeout_sec=_normalize_cli_int(data.get("default_timeout_sec"), 45, minimum=1, maximum=600),
            domains=_normalize_cli_domain_mapping(data.get("domains")),
        )


@dataclass
class QQBotAccessConfig:
    admins: list[str] = field(default_factory=list)
    allow_users: list[str] = field(default_factory=list)
    allow_groups: list[str] = field(default_factory=list)
    group_user_allowlist: dict[str, list[str]] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "admins": list(self.admins),
            "allow_users": list(self.allow_users),
            "allow_groups": list(self.allow_groups),
            "group_user_allowlist": {key: list(values) for key, values in self.group_user_allowlist.items()},
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any] | None) -> "QQBotAccessConfig":
        if not isinstance(data, dict):
            return cls()
        return cls(
            admins=_normalize_string_id_list(data.get("admins")),
            allow_users=_normalize_string_id_list(data.get("allow_users")),
            allow_groups=_normalize_string_id_list(data.get("allow_groups")),
            group_user_allowlist=_normalize_string_list_mapping(data.get("group_user_allowlist")),
        )


@dataclass
class QQBotCapabilitiesConfig:
    llm_users: list[str] = field(default_factory=list)
    predefined_commands_for_users: bool = True
    # Reserved for later routing/runtime work; Task 2 still keeps these paths
    # admin-only even when the stored config value is False.
    admin_slash_admin_only: bool = True
    modify_state_admin_only: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "llm_users": list(self.llm_users),
            "predefined_commands_for_users": self.predefined_commands_for_users,
            "admin_slash_admin_only": self.admin_slash_admin_only,
            "modify_state_admin_only": self.modify_state_admin_only,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any] | None) -> "QQBotCapabilitiesConfig":
        if not isinstance(data, dict):
            return cls()
        return cls(
            llm_users=_normalize_string_id_list(data.get("llm_users")),
            predefined_commands_for_users=_coerce_bool(
                data.get("predefined_commands_for_users"),
                True,
            ),
            admin_slash_admin_only=_coerce_bool(
                data.get("admin_slash_admin_only"),
                True,
            ),
            modify_state_admin_only=_coerce_bool(
                data.get("modify_state_admin_only"),
                True,
            ),
        )


@dataclass
class QQBotTriggerConfig:
    command_prefixes: list[str] = field(default_factory=lambda: ["/"])
    group_commands_require_mention: bool = False
    group_free_text_require_mention: bool = True
    allow_reply_without_mention: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "command_prefixes": list(self.command_prefixes),
            "group_commands_require_mention": self.group_commands_require_mention,
            "group_free_text_require_mention": self.group_free_text_require_mention,
            "allow_reply_without_mention": self.allow_reply_without_mention,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any] | None) -> "QQBotTriggerConfig":
        if not isinstance(data, dict):
            return cls()
        return cls(
            command_prefixes=_normalize_prefixes(data.get("command_prefixes")),
            group_commands_require_mention=_coerce_bool(
                data.get("group_commands_require_mention"),
                False,
            ),
            group_free_text_require_mention=_coerce_bool(
                data.get("group_free_text_require_mention"),
                True,
            ),
            allow_reply_without_mention=_coerce_bool(
                data.get("allow_reply_without_mention"),
                False,
            ),
        )


@dataclass
class QQBotRuntimeConfig:
    mode: str = "qqbot"
    model: str = ""
    provider: str = ""
    enabled_toolsets: list[str] = field(default_factory=list)
    enable_smart_routing: bool = False
    max_iterations: int = 12
    disable_streaming: bool = True
    disable_reasoning: bool = True
    disable_tool_progress: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "model": self.model,
            "provider": self.provider,
            "enabled_toolsets": list(self.enabled_toolsets),
            "enable_smart_routing": self.enable_smart_routing,
            "max_iterations": self.max_iterations,
            "disable_streaming": self.disable_streaming,
            "disable_reasoning": self.disable_reasoning,
            "disable_tool_progress": self.disable_tool_progress,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any] | None) -> "QQBotRuntimeConfig":
        if not isinstance(data, dict):
            return cls()
        return cls(
            mode=_normalize_runtime_mode(data.get("mode"), "qqbot"),
            model=_normalize_optional_string(data.get("model"), ""),
            provider=_normalize_optional_string(data.get("provider"), ""),
            enabled_toolsets=_normalize_string_list(data.get("enabled_toolsets")),
            enable_smart_routing=_coerce_bool(data.get("enable_smart_routing"), False),
            max_iterations=_normalize_runtime_int(data.get("max_iterations"), 12, minimum=1),
            disable_streaming=_coerce_bool(data.get("disable_streaming"), True),
            disable_reasoning=_coerce_bool(data.get("disable_reasoning"), True),
            disable_tool_progress=_coerce_bool(data.get("disable_tool_progress"), True),
        )


@dataclass
class QQBotTestingConfig:
    explicit_denials: bool = False
    log_access_decisions: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "explicit_denials": self.explicit_denials,
            "log_access_decisions": self.log_access_decisions,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any] | None) -> "QQBotTestingConfig":
        if not isinstance(data, dict):
            return cls()
        return cls(
            explicit_denials=_coerce_bool(data.get("explicit_denials"), False),
            log_access_decisions=_coerce_bool(data.get("log_access_decisions"), False),
        )


@dataclass
class QQBotViewCacheConfig:
    enabled: bool = True
    mode: str = "content_hash"
    root: str | None = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "mode": self.mode,
            "root": self.root,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any] | None) -> "QQBotViewCacheConfig":
        if not isinstance(data, dict):
            return cls()
        return cls(
            enabled=_coerce_bool(data.get("enabled"), True),
            mode=_normalize_enum_string(data.get("mode"), {"none", "content_hash", "ttl"}, "content_hash"),
            root=_normalize_optional_path_string(data.get("root"), None),
        )


@dataclass
class QQBotViewLifecycleConfig:
    auto_recall_default_sec: int | None = None
    per_domain: dict[str, int | None] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "auto_recall_default_sec": self.auto_recall_default_sec,
            "per_domain": dict(self.per_domain),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any] | None) -> "QQBotViewLifecycleConfig":
        if not isinstance(data, dict):
            return cls()
        return cls(
            auto_recall_default_sec=_normalize_positive_optional_int(data.get("auto_recall_default_sec"), None),
            per_domain=_normalize_domain_recall_mapping(data.get("per_domain")),
        )


@dataclass
class QQBotViewTelemetryConfig:
    enabled: bool = True
    sqlite_path: str | None = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "sqlite_path": self.sqlite_path,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any] | None) -> "QQBotViewTelemetryConfig":
        if not isinstance(data, dict):
            return cls()
        return cls(
            enabled=_coerce_bool(data.get("enabled"), True),
            sqlite_path=_normalize_optional_path_string(data.get("sqlite_path"), None),
        )


@dataclass
class QQBotHelpMenuRenderConfig:
    quality: int = 70
    scale_factor: float = 1.5
    min_width: int = 1024
    min_height: int = 720
    max_width: int = 1480
    max_height: int = 4300

    def to_dict(self) -> Dict[str, Any]:
        return {
            "quality": self.quality,
            "scale_factor": self.scale_factor,
            "min_width": self.min_width,
            "min_height": self.min_height,
            "max_width": self.max_width,
            "max_height": self.max_height,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any] | None) -> "QQBotHelpMenuRenderConfig":
        if not isinstance(data, dict):
            return cls()
        return cls(
            quality=_normalize_runtime_int(data.get("quality"), 70, minimum=1),
            scale_factor=_normalize_positive_float(data.get("scale_factor"), 1.5, minimum=0.5, maximum=4.0),
            min_width=_normalize_runtime_int(data.get("min_width"), 1024, minimum=320),
            min_height=_normalize_runtime_int(data.get("min_height"), 720, minimum=320),
            max_width=_normalize_runtime_int(data.get("max_width"), 1480, minimum=320),
            max_height=_normalize_runtime_int(data.get("max_height"), 4300, minimum=320),
        )


@dataclass
class QQBotFeedCardRenderConfig:
    quality: int = 70
    scale_factor: float = 1.5
    min_width: int = 960
    min_height: int = 520
    max_width: int = 1480
    max_height: int = 3200
    font_family: str = '"Noto Sans CJK SC", "Source Han Sans CN", "Microsoft YaHei", "PingFang SC", system-ui, sans-serif'

    def to_dict(self) -> Dict[str, Any]:
        return {
            "quality": self.quality,
            "scale_factor": self.scale_factor,
            "min_width": self.min_width,
            "min_height": self.min_height,
            "max_width": self.max_width,
            "max_height": self.max_height,
            "font_family": self.font_family,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any] | None) -> "QQBotFeedCardRenderConfig":
        normalized = normalize_feed_card_render_options(data)
        return cls(**normalized)


@dataclass
class QQBotViewConfig:
    enabled: bool = True
    default_format: str = "text"
    help_menu_format: str = "image"
    image_preferred_domains: list[str] = field(default_factory=list)
    help_menu: QQBotHelpMenuRenderConfig = field(default_factory=QQBotHelpMenuRenderConfig)
    feed_card: QQBotFeedCardRenderConfig = field(default_factory=QQBotFeedCardRenderConfig)
    cache: QQBotViewCacheConfig = field(default_factory=QQBotViewCacheConfig)
    lifecycle: QQBotViewLifecycleConfig = field(default_factory=QQBotViewLifecycleConfig)
    telemetry: QQBotViewTelemetryConfig = field(default_factory=QQBotViewTelemetryConfig)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "default_format": self.default_format,
            "help_menu_format": self.help_menu_format,
            "image_preferred_domains": list(self.image_preferred_domains),
            "help_menu": self.help_menu.to_dict(),
            "feed_card": self.feed_card.to_dict(),
            "cache": self.cache.to_dict(),
            "lifecycle": self.lifecycle.to_dict(),
            "telemetry": self.telemetry.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any] | None) -> "QQBotViewConfig":
        if not isinstance(data, dict):
            return cls()
        return cls(
            enabled=_coerce_bool(data.get("enabled"), True),
            default_format=_normalize_enum_string(data.get("default_format"), {"text", "image", "mixed", "auto"}, "text"),
            help_menu_format=_normalize_enum_string(data.get("help_menu_format"), {"text", "image"}, "image"),
            image_preferred_domains=_normalize_domain_token_list(data.get("image_preferred_domains")),
            help_menu=QQBotHelpMenuRenderConfig.from_dict(data.get("help_menu")),
            feed_card=QQBotFeedCardRenderConfig.from_dict(data.get("feed_card")),
            cache=QQBotViewCacheConfig.from_dict(data.get("cache")),
            lifecycle=QQBotViewLifecycleConfig.from_dict(data.get("lifecycle")),
            telemetry=QQBotViewTelemetryConfig.from_dict(data.get("telemetry")),
        )


@dataclass
class QQBotConfig:
    enabled: bool = False
    platform: str = "napcat"
    unauthorized_dm_behavior: str = "ignore"
    access: QQBotAccessConfig = field(default_factory=QQBotAccessConfig)
    capabilities: QQBotCapabilitiesConfig = field(default_factory=QQBotCapabilitiesConfig)
    cli: QQBotCLIConfig = field(default_factory=QQBotCLIConfig)
    triggers: QQBotTriggerConfig = field(default_factory=QQBotTriggerConfig)
    runtime: QQBotRuntimeConfig = field(default_factory=QQBotRuntimeConfig)
    view: QQBotViewConfig = field(default_factory=QQBotViewConfig)
    testing: QQBotTestingConfig = field(default_factory=QQBotTestingConfig)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "platform": self.platform,
            "unauthorized_dm_behavior": self.unauthorized_dm_behavior,
            "access": self.access.to_dict(),
            "capabilities": self.capabilities.to_dict(),
            "cli": self.cli.to_dict(),
            "triggers": self.triggers.to_dict(),
            "runtime": self.runtime.to_dict(),
            "view": self.view.to_dict(),
            "testing": self.testing.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any] | None) -> "QQBotConfig":
        if not isinstance(data, dict):
            return cls()
        return cls(
            enabled=_coerce_bool(data.get("enabled"), False),
            platform=_normalize_platform(data.get("platform"), "napcat"),
            unauthorized_dm_behavior=_normalize_unauthorized_dm_behavior(
                data.get("unauthorized_dm_behavior"),
                "ignore",
            ),
            access=QQBotAccessConfig.from_dict(data.get("access")),
            capabilities=QQBotCapabilitiesConfig.from_dict(data.get("capabilities")),
            cli=QQBotCLIConfig.from_dict(data.get("cli")),
            triggers=QQBotTriggerConfig.from_dict(data.get("triggers")),
            runtime=QQBotRuntimeConfig.from_dict(data.get("runtime")),
            view=QQBotViewConfig.from_dict(data.get("view")),
            testing=QQBotTestingConfig.from_dict(data.get("testing")),
        )
