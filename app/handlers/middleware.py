from __future__ import annotations

import time
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from .. import texts as T


class ThrottleMiddleware(BaseMiddleware):
    """Не больше одного действия за `interval` секунд от одного пользователя."""

    def __init__(self, interval: float = 1.0):
        self.interval = interval
        self._last: dict[int, float] = {}

    async def __call__(self, handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
                       event: TelegramObject, data: dict[str, Any]) -> Any:
        user = getattr(event, "from_user", None)
        # платёжные события не троттлим: потерять оплату нельзя
        if user is None or getattr(event, "successful_payment", None) is not None:
            return await handler(event, data)
        now = time.monotonic()
        if now - self._last.get(user.id, 0.0) < self.interval:
            if isinstance(event, CallbackQuery):
                await event.answer(T.ALERT_FAST)
            return None
        self._last[user.id] = now
        if len(self._last) > 10000:
            cutoff = now - 60
            self._last = {k: v for k, v in self._last.items() if v > cutoff}
        return await handler(event, data)
