"""Bounded Telegram preflight against a fake transport, to prove session ownership.

Uses the same TelegramService ownership gate as the worker, but no network and
no real session, so the only variable is which process holds the session lock.
"""
import json
import sys

from channel_press.config import settings
from channel_press.telegram.service import TelegramService
from channel_press.telegram.transport import PageResult, TelegramTransportError


class FakeTransport:
    def __init__(self):
        self.connected = False

    async def connect(self):
        self.connected = True

    async def disconnect(self):
        self.connected = False

    async def is_authorized(self):
        return self.connected

    async def resolve(self, handle):
        return {"id": -1001234567890, "username": "fixture_desk", "title": "Fixture Desk"}

    async def page(self, entity, *, limit, offset_id=0, offset_date=None):
        return PageResult(messages=[], oldest_id=None)


service = TelegramService(
    session_path=settings.data_dir / "telegram.session",
    factory=FakeTransport,
)
try:
    info = service.preflight("fixture_desk")
    print(json.dumps({"status": "ready", "channel": info.get("channel")}))
except TelegramTransportError as exc:
    print(json.dumps({"status": "blocked", "detail": str(exc)}))
finally:
    service.close()
sys.exit(0)
