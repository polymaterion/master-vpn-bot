"""Platega: провайдер (HTTP), проверка и выдача, колбэк, экран «Рубли»."""
from __future__ import annotations

import asyncio
from decimal import Decimal
from types import SimpleNamespace

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from app.providers.platega import Platega, PlategaError
from app.services.payments import check_platega, platega_payment
from app.ui import screens  # noqa: F401
from app.ui.nav import Navigator
from app.webhooks import build_web_app, handle_platega_callback

import pytest


# --- провайдер по HTTP ---------------------------------------------------------------
async def test_create_invoice_sends_keys_method_and_metadata():
    seen: dict = {}

    async def handler(request: web.Request):
        seen.update(headers=dict(request.headers), body=await request.json())
        return web.json_response({"transactionId": "tx-1", "redirect": "https://pay.platega.io?x", "status": "PENDING"})

    srv = web.Application()
    srv.router.add_post("/transaction/process", handler)
    async with TestServer(srv) as server:
        p = Platega(True, "mid", "sec", str(server.make_url("")))
        ref, url = await p.create_invoice(Decimal("199"), "Master VPN", "pay-1", {"tg_id": "5"}, "https://t.me/bot")
        await p.close()
    assert (ref, url) == ("tx-1", "https://pay.platega.io?x")
    assert seen["headers"]["X-MerchantId"] == "mid" and seen["headers"]["X-Secret"] == "sec"
    b = seen["body"]
    assert b["paymentMethod"] == 2 and b["paymentDetails"] == {"amount": 199.0, "currency": "RUB"}
    assert b["payload"] == "pay-1" and b["metadata"] == {"tg_id": "5"}


async def test_create_invoice_retries_without_metadata_on_400():
    bodies: list[dict] = []

    async def handler(request: web.Request):
        body = await request.json()
        bodies.append(body)
        if "metadata" in body:
            return web.json_response({"code": "x", "message": "unknown field"}, status=400)
        return web.json_response({"transactionId": "tx-2", "redirect": "https://pay", "status": "PENDING"})

    srv = web.Application()
    srv.router.add_post("/transaction/process", handler)
    async with TestServer(srv) as server:
        p = Platega(True, "mid", "sec", str(server.make_url("")))
        ref, _ = await p.create_invoice(Decimal("100"), "d", "p", {"tg_id": "1"}, "https://t.me/b")
        await p.close()
    assert ref == "tx-2" and len(bodies) == 2 and "metadata" not in bodies[1]


async def test_auth_error_raises_platega_error():
    async def handler(request: web.Request):
        return web.json_response({"code": "Auth:GEN_1001", "message": "Access denied.", "traceId": "t1"}, status=401)

    srv = web.Application()
    srv.router.add_get("/transaction/{id}", handler)
    async with TestServer(srv) as server:
        p = Platega(True, "mid", "bad", str(server.make_url("")))
        with pytest.raises(PlategaError) as e:
            await p.status("tx")
        await p.close()
    assert e.value.status == 401 and "t1" in str(e.value)


def test_verify_callback_checks_merchant_and_secret():
    p = Platega(True, "mid", "sec")
    assert p.verify_callback({"X-MerchantId": "mid", "X-Secret": "sec"})
    assert not p.verify_callback({"X-MerchantId": "mid", "X-Secret": "other"})
    assert not p.verify_callback({"X-MerchantId": "x", "X-Secret": "sec"})
    assert not p.verify_callback({})
    assert not Platega(True, "", "").verify_callback({"X-MerchantId": "", "X-Secret": ""})


def test_enabled_needs_flag_and_both_keys():
    assert Platega(True, "a", "b").enabled
    assert not Platega(False, "a", "b").enabled and not Platega(True, "", "b").enabled and not Platega(True, "a", "").enabled


# --- проверка и выдача -----------------------------------------------------------------
class FakePlategaApi:
    enabled = True

    def __init__(self, status="PENDING", amount=Decimal("100"), currency="RUB"):
        self.st = {"status": status, "amount": amount, "currency": currency}
        self.created: list = []

    async def status(self, ref):
        return dict(self.st)

    async def create_invoice(self, amount, description, payload, metadata, return_url):
        self.created.append((amount, payload, metadata, return_url))
        return f"tx-{len(self.created)}", "https://pay.platega.io/x"

    def verify_callback(self, headers):
        return headers.get("X-Secret") == "sec"


class Notes:
    def __init__(self):
        self.texts: list[str] = []

    async def admins(self, text, kb=None):
        self.texts.append(text)


def make_app(shop, api):
    notes = Notes()
    bot = SimpleNamespace()

    async def me():
        return SimpleNamespace(id=777, username="mybot")

    async def get_chat(tg_id):
        return SimpleNamespace(username="buyer")

    bot.me, bot.get_chat = me, get_chat
    app = SimpleNamespace(db=shop.db, orders=shop.orders, platega=api, notifier=notes, bot=bot, partner=shop.partner)
    return app, notes


async def _invoice(app, tg_id=7):
    return await platega_payment(app, tg_id, "buy", 1, 2, Decimal("100"), "Master VPN")


async def test_invoice_carries_metadata_for_platega(shop):
    api = FakePlategaApi()
    app, _ = make_app(shop, api)
    pay = await _invoice(app)
    amount, payload, meta, ret = api.created[0]
    assert payload == pay.id and ret == "https://t.me/mybot"
    assert meta == {"tg_bot_id": "777", "tg_bot_username": "@mybot", "tg_id": "7", "tg_username": "@buyer"}
    assert pay.provider_ref == "tx-1" and pay.currency == "RUB" and pay.status == "created"


async def test_same_open_invoice_is_reused(shop):
    api = FakePlategaApi()
    app, _ = make_app(shop, api)
    a, b = await _invoice(app), await _invoice(app)
    assert a.id == b.id and len(api.created) == 1


async def test_confirmed_fulfils_once_even_if_checked_twice(shop):
    api = FakePlategaApi("CONFIRMED")
    app, _ = make_app(shop, api)
    pay = await _invoice(app)
    assert await check_platega(app, pay) == "paid"
    assert await check_platega(app, pay) == "paid"          # повторный колбэк / проверка
    assert shop.notifier.events == [("ok", 7)]
    assert [c for c in shop.partner.calls if c[0] == "PUT"][0][4] == f"platega-{pay.provider_ref}"
    assert len([c for c in shop.partner.calls if c[0] == "PUT"]) == 1


async def test_pending_waits_and_canceled_expires(shop):
    api = FakePlategaApi("PENDING")
    app, _ = make_app(shop, api)
    pay = await _invoice(app)
    assert await check_platega(app, pay) == "pending" and shop.notifier.events == []
    api.st["status"] = "CANCELED"
    assert await check_platega(app, pay) == "expired"
    assert (await shop.db.get_payment(pay.id)).status == "expired"


async def test_amount_mismatch_is_not_fulfilled_and_admin_alerted_once(shop):
    api = FakePlategaApi("CONFIRMED", amount=Decimal("1"))
    app, notes = make_app(shop, api)
    pay = await _invoice(app)
    assert await check_platega(app, pay) == "pending"
    assert await check_platega(app, pay) == "pending"
    assert shop.notifier.events == [] and len(notes.texts) == 1 and "сумма не совпала" in notes.texts[0]


# --- колбэк ------------------------------------------------------------------------------
async def test_callback_rejects_wrong_secret_and_confirms_payment(shop):
    api = FakePlategaApi("CONFIRMED")
    app, notes = make_app(shop, api)
    pay = await _invoice(app)
    async with TestClient(TestServer(build_web_app(app))) as client:  # type: ignore[arg-type]
        body = {"id": pay.provider_ref, "amount": 100, "currency": "RUB", "status": "CONFIRMED",
                "paymentMethod": 2, "payload": pay.id}
        bad = await client.post("/platega-callback", json=body, headers={"X-Secret": "nope"})
        assert bad.status == 401
        ok = await client.post("/platega-callback", json=body, headers={"X-Secret": "sec"})
        assert ok.status == 200
        broken = await client.post("/platega-callback", data=b"not json", headers={"X-Secret": "sec"})
        assert broken.status == 400
        await asyncio.sleep(0.2)
    assert shop.notifier.events == [("ok", 7)]


async def test_callback_does_not_trust_body_status(shop):
    api = FakePlategaApi("PENDING")                          # в теле CONFIRMED, но Platega говорит PENDING
    app, _ = make_app(shop, api)
    pay = await _invoice(app)
    await handle_platega_callback(app, {"id": pay.provider_ref, "status": "CONFIRMED"})
    assert shop.notifier.events == [] and (await shop.db.get_payment(pay.id)).status == "created"


async def test_chargeback_and_dispute_only_alert_admins_once(shop):
    api = FakePlategaApi("CONFIRMED")
    app, notes = make_app(shop, api)
    pay = await _invoice(app)
    for _ in range(2):
        await handle_platega_callback(app, {"id": pay.provider_ref, "status": "CHARGEBACKED"})
    assert len(notes.texts) == 1 and "CHARGEBACKED" in notes.texts[0]
    assert shop.notifier.events == []


async def test_callback_for_unknown_payment_is_ignored(shop):
    app, notes = make_app(shop, FakePlategaApi("CONFIRMED"))
    await handle_platega_callback(app, {"id": "unknown", "status": "CONFIRMED"})
    assert notes.texts == [] and shop.notifier.events == []


# --- экран ---------------------------------------------------------------------------------
class ScreenBot:
    def __init__(self, base):
        self.sent: list[dict] = []
        self.me, self.get_chat = base.bot.me, base.bot.get_chat

    async def send_message(self, chat_id, text, reply_markup=None, **kw):
        self.sent.append({"text": text, "kb": reply_markup})


async def test_rub_screen_shows_sbp_link_and_check_button(shop, settings):
    from app.providers.cryptopay import CryptoPay
    api = FakePlategaApi()
    base, _ = make_app(shop, api)
    app = SimpleNamespace(settings=settings, db=shop.db, bot=ScreenBot(base), partner=shop.partner,
                          cryptopay=CryptoPay("", "https://x"), platega=api, orders=shop.orders, notifier=base.notifier)
    app.nav = Navigator(app)
    await app.nav.push(5, None, "pay_rub", {"kind": "buy", "months": "1", "devices": "2"})
    msg = app.bot.sent[-1]
    buttons = [b for row in msg["kb"].inline_keyboard for b in row]
    assert "100 ₽" in msg["text"] and "Platega test" not in msg["text"]
    assert any(b.url == "https://pay.platega.io/x" for b in buttons)
    assert any(b.callback_data == "a|checkr" for b in buttons)


async def test_rub_screen_is_stub_when_disabled(shop, settings):
    from app.providers.cryptopay import CryptoPay
    api = FakePlategaApi()
    api.enabled = False
    base, _ = make_app(shop, api)
    app = SimpleNamespace(settings=settings, db=shop.db, bot=ScreenBot(base), partner=shop.partner,
                          cryptopay=CryptoPay("", "https://x"), platega=api, orders=shop.orders, notifier=base.notifier)
    app.nav = Navigator(app)
    await app.nav.push(5, None, "pay_rub", {"kind": "buy", "months": "1", "devices": "2"})
    assert "скоро будет доступна" in app.bot.sent[-1]["text"] and api.created == []
