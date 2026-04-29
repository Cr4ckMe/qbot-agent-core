# qbot-agent-core

A sanitized core-code snapshot of a QQ/NapCat AI Agent bot runtime built on top of Hermes Agent.

This repository is intentionally limited to the core QQ bot implementation, command routing, Lute skill orchestration, NapCat compatibility adapter, and focused regression tests. Runtime secrets, local configuration, logs, caches, downloaded media, private skill data, and deployment files are not included.

## What this project demonstrates

- QQ/NapCat gateway adapter and message normalization.
- Central command routing for `/lute ...` style bot capabilities.
- Skill/tool backend abstraction for media, RSS, Bangumi, Pixiv/JM, movie search, and other agent workflows.
- Access-control and group-admin policy layers.
- Agent-style orchestration: request parsing -> policy checks -> backend/tool dispatch -> result formatting -> QQ-compatible response.
- Focused tests for command routing, access control, NapCat compatibility, runtime overrides, and Lute integrations.

## Core pain points addressed

1. Reduces fragmented workflows in QQ groups by turning web lookups, media search, RSS monitoring, image-related operations, and content summarization into bot commands.
2. Provides a maintainable gateway/runtime layer instead of one-off scripts.
3. Keeps platform-specific QQ/NapCat handling separate from backend skills so more tools or LLM providers can be added without rewriting the bot.

## Privacy and security note

This is a sanitized source snapshot. It should not contain real tokens, QQ group IDs, user IDs, logs, `.env` files, caches, downloaded content, or private runtime data. Test identifiers are placeholders.

## Repository scope

Included:

- `gateway/qqbot*.py`
- `gateway/platforms/qqbot/*.py`
- selected gateway/command integration files
- `tests/gateway/test_qqbot*.py`
- `tests/gateway/test_*lute*.py`

Excluded:

- local `.env` and credentials
- logs, databases, sessions, caches
- full Hermes upstream source tree
- downloaded media or private skill data
