from __future__ import annotations

import os
import pathlib
import re

import pytest

from app.config import CURRENCIES, DEVICES, MONTHS, Settings, price_var
from app.db import Database
from app.partner.client import PartnerError
from app.services.orders import OrderService
from app.services.plans import PlanCatalog


def full_env(**over: str) -> dict[str, str]:
    env = {"BOT_TOKEN": "1:x", "PARTNER_API_KEY": "uk_test", "DATABASE_URL": "sqlite+aiosqlite://",
           "ADMIN_IDS": "1", "SUPPORT_URL": "https://t.me/s", "PARTNER_WEBHOOK_SECRET": "sekret"}
    for cur in CURRENCIES:
        for m in MONTHS:
            for d in DEVICES:
                env[price_var(cur, m, d)] = "1.50" if cur == "USDT" else "100"
    env.update(over)
    return env


@pytest.fixture
def settings() -> Settings:
    return Settings.from_env(full_env())


@pytest.fixture
async def db(tmp_path):
    database = Database(f"sqlite+aiosqlite:///{tmp_path}/t.db")
    await database.create_all()
    yield database
    await database.close()


class FakePartner:
    """Минимальная имитация Partner API: идемпотентность по ключу, кошелёк, сбои по сценарию."""

    def __init__(self, balance: int = 10_000_000):
        self.balance = balance
        self.orders: dict[str, dict] = {}
        self.calls: list[tuple] = []
        self.fail: list[PartnerError] = []   # очередь ошибок для ближайших заказов
        self.users: dict[str, dict] = {}

    PLANS = {"plans": [
        {"id": f"plan-{d}", "kind": "grid", "device_limit": d, "duration_days": 30} for d in (1, 2, 4, 8)]}

    async def plans(self):
        return self.PLANS

    async def quote(self, plan_id, days, *, attempts=None):
        return {"amount_minor": str(10500 * days // 30), "orders_enabled": True}

    async def balance_(self):
        return {"balance_minor": str(self.balance)}

    async def user(self, ext):
        return self.users.get(ext)

    def _order(self, ext, key, days, device_limit):
        if key in self.orders:
            return {**self.orders[key], "replayed": True, "charged_minor": "0"}
        if self.fail:
            raise self.fail.pop(0)
        price = 10500 * days // 30
        if price > self.balance:
            raise PartnerError(409, {"code": "insufficient_balance", "request_id": "req_x"})
        self.balance -= price
        user = {"external_id": ext, "status": "active", "expires_at": "2027-01-01T00:00:00Z",
                "device_limit": device_limit, "subscription_url": f"https://sub/{ext}",
                "replayed": False, "charged_minor": str(price)}
        self.orders[key] = user
        self.users[ext] = user
        return user

    async def put_user(self, ext, plan_id, days, key, *, max_charge_minor=None, metadata=None):
        self.calls.append(("PUT", ext, plan_id, days, key, max_charge_minor))
        return self._order(ext, key, days, int(plan_id.split("-")[1]))

    async def extend(self, ext, days, key, *, max_charge_minor=None):
        self.calls.append(("EXTEND", ext, days, key))
        return self._order(ext, key, days, self.users[ext]["device_limit"])


class FakeNotifier:
    def __init__(self):
        self.events: list[tuple] = []

    async def paid_ok(self, payment, user):
        self.events.append(("ok", payment.tg_id))

    async def paid_pending(self, payment, error):
        self.events.append(("pending", payment.tg_id, error.code))

    async def paid_failed(self, payment, error):
        self.events.append(("failed", payment.tg_id, error.code))


@pytest.fixture
async def shop(db):
    partner, notifier = FakePartner(), FakeNotifier()
    plans = PlanCatalog(partner)  # type: ignore[arg-type]
    await plans.refresh()
    orders = OrderService(db, partner, plans, notifier)  # type: ignore[arg-type]
    return type("Shop", (), {"db": db, "partner": partner, "notifier": notifier, "orders": orders})()
