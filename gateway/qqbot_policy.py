"""QQ bot access and capability policy helpers."""

from gateway.config import Platform
from gateway.qqbot_config import QQBotConfig
from gateway.qqbot_lute_types import LuteDomainSpec, LuteVerbSpec
from gateway.session import SessionSource


class QQBotPolicy:
    def __init__(self, config: QQBotConfig):
        self.config = config
        self._admins = set(config.access.admins)
        self._allow_users = set(config.access.allow_users)
        self._allow_groups = set(config.access.allow_groups)
        self._group_user_allowlist = {
            str(group_id): set(user_ids)
            for group_id, user_ids in (config.access.group_user_allowlist or {}).items()
        }
        self._llm_users = set(config.capabilities.llm_users)

    def is_enabled_for(self, source: SessionSource) -> bool:
        if source is None:
            return False
        if not self.config.enabled:
            return False
        source_platform = getattr(getattr(source, 'platform', None), 'value', None)
        if source_platform == self.config.platform:
            return True
        if self.config.platform in {'napcat', 'qqbot'} and source_platform == Platform.QQBOT.value:
            return True
        return False

    def is_admin(self, user_id: str | None) -> bool:
        normalized = self._normalize_id(user_id)
        if normalized is None:
            return False
        return normalized in self._admins

    def is_allowed_dm_user(self, user_id: str | None) -> bool:
        if self.is_admin(user_id):
            return True
        normalized = self._normalize_id(user_id)
        if normalized is None:
            return False
        return normalized in self._allow_users

    def is_allowed_group(self, chat_id: str | None) -> bool:
        normalized = self._normalize_id(chat_id)
        if normalized is None:
            return False
        return normalized in self._allow_groups

    def is_allowed_group_user(self, chat_id: str | None, user_id: str | None) -> bool:
        if self.is_admin(user_id):
            return True
        normalized_group = self._normalize_id(chat_id)
        normalized_user = self._normalize_id(user_id)
        if normalized_group is None or normalized_user is None:
            return False
        if normalized_group not in self._allow_groups:
            return False
        return normalized_user in self._group_user_allowlist.get(normalized_group, set())

    def can_use_predefined_commands(self, user_id: str | None) -> bool:
        if self.is_admin(user_id):
            return True
        return self._has_identity(user_id) and self.config.capabilities.predefined_commands_for_users

    def can_use_llm(self, user_id: str | None) -> bool:
        if self.is_admin(user_id):
            return True
        normalized = self._normalize_id(user_id)
        if normalized is None:
            return False
        return normalized in self._llm_users

    def can_use_admin_slash(self, user_id: str | None) -> bool:
        return self._can_use_reserved_admin_capability(
            user_id,
            self.config.capabilities.admin_slash_admin_only,
        )

    def can_modify_state(self, user_id: str | None) -> bool:
        return self._can_use_reserved_admin_capability(
            user_id,
            self.config.capabilities.modify_state_admin_only,
        )

    def can_access_lute_access_level(self, access: str, user_id: str | None) -> bool:
        if access == "admin":
            return self.is_admin(user_id)
        return self.can_use_predefined_commands(user_id)

    def can_access_lute_domain(self, domain_spec: LuteDomainSpec, user_id: str | None) -> bool:
        return self.can_access_lute_access_level(domain_spec.access, user_id)

    def can_view_lute_domain_help(self, domain_spec: LuteDomainSpec, user_id: str | None) -> bool:
        if self.is_admin(user_id):
            return True
        if domain_spec.access == "admin" or not domain_spec.visible_in_help:
            return False
        return self.can_access_lute_domain(domain_spec, user_id)

    def can_access_lute_verb(
        self,
        domain_spec: LuteDomainSpec,
        verb_spec: LuteVerbSpec,
        user_id: str | None,
    ) -> bool:
        required_access = "admin" if "admin" in {domain_spec.access, verb_spec.access} else domain_spec.access
        return self.can_access_lute_access_level(required_access, user_id)

    def _can_use_reserved_admin_capability(
        self,
        user_id: str | None,
        _configured_admin_only: bool,
    ) -> bool:
        """Keep Task 2 admin-only while preserving future-facing config flags."""
        return self.is_admin(user_id)

    @staticmethod
    def _normalize_id(value: str | None) -> str | None:
        if value is None:
            return None
        candidate = str(value).strip()
        if not candidate:
            return None
        return candidate

    def _has_identity(self, value: str | None) -> bool:
        return self._normalize_id(value) is not None
