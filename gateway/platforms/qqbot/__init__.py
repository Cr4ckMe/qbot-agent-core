"""QQBot platform package.

This branch preserves the public `gateway.platforms.qqbot` import path but the
active transport implementation is NapCat-backed rather than the official QQ
Bot API.
"""

from .adapter import (  # noqa: F401
    QQAdapter,
    QQCloseError,
    check_qq_requirements,
    _coerce_list,
    _ssrf_redirect_guard,
)

__all__ = [
    'QQAdapter',
    'QQCloseError',
    'check_qq_requirements',
    '_coerce_list',
    '_ssrf_redirect_guard',
]
