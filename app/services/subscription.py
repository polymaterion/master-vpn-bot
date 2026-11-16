from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from ..partner.client import PartnerError
from ..utils import parse_iso, utcnow

if TYPE_CHECKING:
    from ..context import App


@dataclass
class Sub:
    user: dict | None
    has_paid: bool
    now: datetime

    @property
    def expires_at(self) -> datetime | None:
        return parse_iso(self.user.get("expires_at")) if self.user else None

    @property
    def exists(self) -> bool:
        return self.expires_at is not None

    @property
    def running(self) -> bool:
        return self.exists and self.expires_at > self.now  # type: ignore[operator]

    @property
    def paused(self) -> bool:
        return self.running and (self.user or {}).get("status") == "disabled"

    @property
    def expired(self) -> bool:
        return self.exists and not self.running

    @property
    def device_limit(self) -> int:
        return int((self.user or {}).get("device_limit") or 0)

    @property
    def can_renew(self) -> bool:
        """Продлить можно клиенту с платным заказом; после триала – только «Купить»."""
        return self.exists and self.has_paid

    @property
    def can_buy(self) -> bool:
        """При действующей платной подписке «Купить» скрыта: другая ступень API не продаёт."""
        return not (self.running and self.has_paid)


async def load_sub(app: "App", tg_id: int) -> Sub:
    user = await app.partner.user(f"tg-{tg_id}")
    return Sub(user=user, has_paid=await app.db.has_paid(tg_id), now=utcnow())


async def reset_devices(app: "App", tg_id: int) -> int:
    """Удаляет все устройства клиента (ссылка остаётся прежней). Возвращает, сколько удалено."""
    ext = f"tg-{tg_id}"
    removed = 0
    for d in (await app.partner.devices(ext)).get("devices", []):
        try:
            await app.partner.delete_device(ext, d["device_id"])
            removed += 1
        except PartnerError as e:
            if e.status != 404:  # уже удалено – не страшно
                raise
    return removed


async def refresh_link(app: "App", tg_id: int) -> dict:
    """Сбрасывает все устройства клиента и выпускает новую ссылку-подписку (старая перестаёт работать)."""
    await reset_devices(app, tg_id)   # сначала слоты: если выпуск ссылки сорвётся, старая ссылка ещё рабочая
    return await app.partner.rotate(f"tg-{tg_id}")
