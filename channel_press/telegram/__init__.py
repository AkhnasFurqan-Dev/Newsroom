"""Authorized Telegram MTProto integration.

The package is import-safe without Telethon installed; the live transport is
only constructed when an owner starts a session.
"""

from .service import (
    LoginState,
    TelegramService,
    mask_phone,
    reset_service_for_tests,
    telegram_service,
)
from .transport import (
    PageResult,
    TelegramAuthError,
    TelegramChannelError,
    TelegramFloodWait,
    TelegramTransportError,
    TelethonTransport,
    normalize_handle,
)

__all__ = [
    "LoginState",
    "PageResult",
    "TelegramAuthError",
    "TelegramChannelError",
    "TelegramFloodWait",
    "TelegramService",
    "TelegramTransportError",
    "TelethonTransport",
    "mask_phone",
    "normalize_handle",
    "reset_service_for_tests",
    "telegram_service",
]
