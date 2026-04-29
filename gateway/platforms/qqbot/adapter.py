"""Branch-specific qqbot adapter export layer.

On this branch, the public `qqbot` platform slot is preserved but the active
transport is NapCat / OneBot 11 instead of the official QQ Bot API.
"""

from .napcat_compat import (  # noqa: F401
    AIOHTTP_AVAILABLE,
    HTTPX_AVAILABLE,
    QQAdapter,
    QQCloseError,
    _coerce_list,
    _ssrf_redirect_guard,
    check_qq_requirements,
)
