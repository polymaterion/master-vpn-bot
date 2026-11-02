"""Прогон всего дерева экранов через Navigator (без сети Telegram)."""
from __future__ import annotations

import pytest

from app.providers.cryptopay import CryptoPay
from app.providers.platega import Platega
from app.ui import screens  # noqa: F401
from app.ui.nav import Navigator, decode


class FakeBot:
    def __init__(self):
        self.sent: list[dict] = []

    async def send_message(self, chat_id, text, reply_markup=None, **kw):
        self.sent.append({"text": text, "kb": reply_markup, "photo": False})

    async def send_invoice(self, **kw):
        self.sent.append({"invoice": kw, "kb": kw.get("reply_markup"), "text": kw.get("title"), "photo": False})

    async def send_photo(self, chat_id, photo, caption=None, reply_markup=None, **kw):
        self.sent.append({"text": caption, "kb": reply_markup, "photo": True})


class PartnerStub:
    def __init__(self, user=None, devices=0):
        self._user, self._devices = user, devices

    async def user(self, ext):
        return self._user

    async def devices(self, ext):
        return {"devices": [{"device_id": str(i)} for i in range(self._devices)]}


def make_app(db, settings, user=None, devices=0):
    bot = FakeBot()
    app = type("A", (), {})()
    app.settings, app.db, app.bot = settings, db, bot
    app.partner = PartnerStub(user, devices)
    app.cryptopay = CryptoPay("", "https://x")
    app.platega = Platega(False)
    app.nav = Navigator(app)
    return app


def buttons(msg):
    return [b for row in msg["kb"].inline_keyboard for b in row]


def paid_user(devices=2, days=20):
    from datetime import timedelta
    from app.utils import utcnow
    return {"status": "active", "expires_at": (utcnow() + timedelta(days=days)).isoformat() + "Z", "device_limit": devices,
            "subscription_url": "https://sub.example.net/sub/abc", "traffic_used_bytes": "0",
            "traffic_limit_bytes": str(1024 ** 4), "white_used_bytes": "0", "white_limit_bytes": str(20 * 1024 ** 3)}


async def test_home_without_subscription_offers_buy(db, settings):
    app = make_app(db, settings)
    await app.nav.start(5)
    texts = [b.text for b in buttons(app.bot.sent[-1])]
    assert any("Купить" in t for t in texts) and any("Поддержка" in t for t in texts)
    assert not any("Назад" in t for t in texts)               # на главном экране «Назад» нет


async def test_back_is_exactly_one_level(db, settings):
    app = make_app(db, settings)
    await app.nav.start(5)
    await app.nav.push(5, None, "buy_term")
    await app.nav.push(5, None, "buy_devices", {"months": "3"})
    await app.nav.push(5, None, "pay_method", {"kind": "buy", "months": "3", "devices": "2"})
    await app.nav.back(5, None)
    assert [e["s"] for e in await app.nav.stack(5)] == ["home", "buy_term", "buy_devices"]
    await app.nav.back(5, None)
    await app.nav.back(5, None)
    await app.nav.back(5, None)                                # на корне остаёмся на корне
    assert [e["s"] for e in await app.nav.stack(5)] == ["home"]


async def test_active_paid_user_has_renew_but_no_buy(db, settings):
    app = make_app(db, settings, user=paid_user())
    await app.db.add_payment(__import__("app.db", fromlist=["PaymentRow"]).PaymentRow(
        id="p1", tg_id=5, provider="stars", kind="buy", months=1, devices=2, amount=1, currency="STARS", status="fulfilled"))
    await app.nav.start(5)
    home = [b.text for b in buttons(app.bot.sent[-1])]
    assert not any("Купить" in t for t in home)
    await app.nav.push(5, None, "sub")
    card = app.bot.sent[-1]
    assert "Устройств: <b>1 из 2</b>" not in card["text"] and "из 2" in card["text"]
    labels = [b.text for b in buttons(card)]
    assert any("Продлить" in t for t in labels) and not any("Купить" in t for t in labels) and any("Назад" in t for t in labels)


async def test_trial_user_can_only_buy_not_renew(db, settings):
    app = make_app(db, settings, user=paid_user(days=2))       # платных заказов нет – это триал
    await app.nav.push(5, None, "sub")
    labels = [b.text for b in buttons(app.bot.sent[-1])]
    assert any("Купить" in t for t in labels) and not any("Продлить" in t for t in labels)
    await app.nav.push(5, None, "buy_devices", {"months": "1"})
    device_buttons = [b for b in buttons(app.bot.sent[-1]) if "устр." in b.text]
    assert len(device_buttons) == 1                            # у действующего триала ступень менять нельзя


async def test_excess_devices_blocks_purchase_before_payment(db, settings):
    app = make_app(db, settings, user={**paid_user(devices=4, days=-1)}, devices=3)
    await app.nav.push(5, None, "pay_method", {"kind": "buy", "months": "1", "devices": "2"})
    msg = app.bot.sent[-1]
    assert "подключено устройств" in msg["text"] and not any("Оплатить" in b.text for b in buttons(msg))


async def test_payment_methods_show_prices_and_rub_is_stub(db, settings):
    app = make_app(db, settings)
    await app.nav.push(5, None, "pay_method", {"kind": "buy", "months": "1", "devices": "2"})
    labels = [b.text for b in buttons(app.bot.sent[-1])]
    assert any("Stars" in t for t in labels) and any("USDT" in t for t in labels) and any("Рубли" in t for t in labels)
    await app.nav.push(5, None, "pay_rub", {"kind": "buy", "months": "1", "devices": "2"})
    assert "скоро будет доступна" in app.bot.sent[-1]["text"]
    await app.nav.push(5, None, "pay_usdt", {"kind": "buy", "months": "1", "devices": "2"})
    assert "временно недоступен" in app.bot.sent[-1]["text"]   # токена CryptoPay нет


async def test_qr_screen_sends_photo(db, settings):
    app = make_app(db, settings, user=paid_user())
    await app.nav.push(5, None, "sub_qr")
    assert app.bot.sent[-1]["photo"] is True


async def test_callback_data_never_exceeds_limit(db, settings):
    app = make_app(db, settings, user=paid_user())
    for name, params in [("home", {}), ("sub", {}), ("renew_term", {}), ("renew_confirm", {"months": "6"}),
                         ("buy_term", {}), ("buy_devices", {"months": "6"}),
                         ("pay_method", {"kind": "renew", "months": "6", "devices": "8"}),
                         ("download", {}), ("info", {})]:
        await app.nav.push(5, None, name, params)
        for b in buttons(app.bot.sent[-1]):
            if b.callback_data:
                assert len(b.callback_data.encode()) <= 64, (name, b.callback_data)


async def test_download_and_info_buttons(db, settings):
    from app.config import Settings
    from tests.conftest import full_env
    s = Settings.from_env(full_env(LINK_ANDROID="https://a", LINK_IOS="https://i", LINK_WINDOWS="https://w",
                                   LINK_POLICY="https://p", LINK_TERMS="https://t", LINK_CHANNEL="https://c"))
    app = make_app(db, s)
    await app.nav.push(5, None, "download")
    assert [b.text for b in buttons(app.bot.sent[-1])] == ["Android", "iOS", "Windows", "⬅️ Назад"]
    await app.nav.push(5, None, "info")
    assert [b.text for b in buttons(app.bot.sent[-1])] == ["Политика", "Условия", "⬅️ Назад"]


def test_menu_only_on_start_command():
    from app.handlers.user import router
    names = [h.callback.__name__ for h in router.message.handlers]
    assert "anything" not in names and "start" in names


async def test_device_choice_goes_straight_to_payment_method(db, settings):
    app = make_app(db, settings)
    await app.nav.push(5, None, "buy_devices", {"months": "3"})
    targets = {decode(b.callback_data)[0] for b in buttons(app.bot.sent[-1]) if b.callback_data and b.callback_data.startswith("g|")}
    assert targets == {"pay_method"}                               # без экрана подтверждения
    first = next(b for b in buttons(app.bot.sent[-1]) if "устр." in b.text)
    name, params = decode(first.callback_data)
    assert params["kind"] == "buy" and params["months"] == "3"
    await app.nav.push(5, None, name, params)
    msg = app.bot.sent[-1]
    assert "Дата окончания" in msg["text"]
    stars = next(b for b in buttons(msg) if "Stars" in b.text)
    assert stars.callback_data == "a|stars"                       # Stars – сразу счёт, не отдельный экран


class FakeMessage:
    def __init__(self):
        self.deleted = False
        self.photo = None

    async def delete(self):
        self.deleted = True


class FakeCb:
    def __init__(self, uid, data, message):
        from types import SimpleNamespace
        self.from_user, self.data, self.message = SimpleNamespace(id=uid), data, message
        self.answers: list = []

    async def answer(self, text=None, show_alert=False):
        self.answers.append((text, show_alert))


async def _stars_app(db, settings):
    from app.services.orders import OrderService
    app = make_app(db, settings)
    app.orders = OrderService(db, None, None, None)  # type: ignore[arg-type]
    await app.nav.start(5)
    await app.nav.push(5, None, "pay_method", {"kind": "buy", "months": "1", "devices": "2"})
    app.bot.sent.clear()
    return app


async def test_stars_button_sends_invoice_and_removes_screen(db, settings):
    from app.handlers.user import stars_invoice
    app = await _stars_app(db, settings)
    screen_msg = FakeMessage()
    await stars_invoice(FakeCb(5, "a|stars", screen_msg), app)
    inv = app.bot.sent[-1]["invoice"]
    assert inv["currency"] == "XTR" and inv["prices"][0].amount == 100          # цена из .env (в тестах 100)
    rows = inv["reply_markup"].inline_keyboard
    assert rows[0][0].pay is True and rows[1][0].callback_data == f"a|cancel|{inv['payload']}"
    assert screen_msg.deleted                                                   # экран способа оплаты удалён
    pay = await db.get_payment(inv["payload"])
    assert pay.status == "created" and (pay.kind, pay.months, pay.devices) == ("buy", 1, 2)


async def test_cancel_on_invoice_restores_previous_screen(db, settings):
    from app.handlers.user import cancel_invoice, stars_invoice
    app = await _stars_app(db, settings)
    await stars_invoice(FakeCb(5, "a|stars", FakeMessage()), app)
    payload = app.bot.sent[-1]["invoice"]["payload"]
    invoice_msg = FakeMessage()
    await cancel_invoice(FakeCb(5, f"a|cancel|{payload}", invoice_msg), app)
    assert invoice_msg.deleted                                                  # счёт удалён
    assert "Способ оплаты" in app.bot.sent[-1]["text"]                          # вернулся экран способа оплаты
    assert (await db.get_payment(payload)).status == "expired"                  # старый счёт оплатить нельзя


async def test_cancel_does_not_touch_already_paid_invoice(db, settings):
    from app.handlers.user import cancel_invoice, stars_invoice
    app = await _stars_app(db, settings)
    await stars_invoice(FakeCb(5, "a|stars", FakeMessage()), app)
    payload = app.bot.sent[-1]["invoice"]["payload"]
    await db.transition(payload, ("created",), "paid", provider_ref="c1", idem_key="stars-c1")
    cb, msg = FakeCb(5, f"a|cancel|{payload}", FakeMessage()), None
    await cancel_invoice(cb, app)
    assert cb.answers[-1][1] is True and not cb.message.deleted
    assert (await db.get_payment(payload)).status == "paid"
