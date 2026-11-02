import hashlib
import hmac
import json
import time

from aiohttp.test_utils import TestClient, TestServer

from app.providers.cryptopay import CryptoPay
from app.webhooks import build_web_app, verify_signature

SECRET = "sekret"


def sign(raw: bytes, ts: str, secret: str = SECRET) -> str:
    return hmac.new(secret.encode(), ts.encode() + b"." + raw, hashlib.sha256).hexdigest()


def test_verify_signature_ok_bad_and_stale():
    raw, ts = b'{"a": 1}', str(int(time.time()))
    assert verify_signature(SECRET, raw, ts, sign(raw, ts))
    assert not verify_signature(SECRET, raw + b" ", ts, sign(raw, ts))          # тело изменено
    assert not verify_signature("other", raw, ts, sign(raw, ts))
    old = str(int(time.time()) - 3600)
    assert not verify_signature(SECRET, raw, old, sign(raw, old))               # вне окна ±5 минут
    assert not verify_signature(SECRET, raw, "abc", "x") and not verify_signature("", raw, ts, sign(raw, ts, ""))


class StubApp:
    def __init__(self, db, settings):
        self.db, self.settings = db, settings
        self.cryptopay = CryptoPay("tok", "https://x")
        self.notified: list = []

        class N:
            async def expired(_, tg): self.notified.append(("expired", tg))
            async def traffic(_, tg, white): self.notified.append(("traffic", tg, white))
            async def admins(_, text, kb=None): self.notified.append(("admins", text))
        self.notifier = N()


async def test_partner_webhook_dedup_and_signature(db, settings):
    import asyncio
    app = StubApp(db, settings)
    async with TestClient(TestServer(build_web_app(app))) as client:  # type: ignore[arg-type]
        body = json.dumps({"seq": "1", "id": "evt-1", "type": "user.expired",
                           "data": {"external_id": "tg-42", "status": "expired"}}).encode()
        ts = str(int(time.time()))
        headers = {"X-Webhook-Timestamp": ts, "X-Webhook-Signature": sign(body, ts)}
        r = await client.post("/partner-webhook", data=body, headers=headers)
        assert r.status == 204
        r = await client.post("/partner-webhook", data=body, headers=headers)  # повтор доставки
        assert r.status == 200
        bad = await client.post("/partner-webhook", data=body, headers={**headers, "X-Webhook-Signature": "0" * 64})
        assert bad.status == 401
        await asyncio.sleep(0.05)
    assert app.notified == [("expired", 42)]


async def test_unknown_event_type_is_acknowledged(db, settings):
    app = StubApp(db, settings)
    async with TestClient(TestServer(build_web_app(app))) as client:  # type: ignore[arg-type]
        body = json.dumps({"seq": "2", "id": "evt-2", "type": "something.new", "data": {}}).encode()
        ts = str(int(time.time()))
        r = await client.post("/partner-webhook", data=body,
                              headers={"X-Webhook-Timestamp": ts, "X-Webhook-Signature": sign(body, ts)})
        assert r.status == 204
    assert app.notified == []


def test_cryptopay_signature():
    cp = CryptoPay("123:token", "https://x")
    raw = b'{"update_type":"invoice_paid"}'
    good = hmac.new(hashlib.sha256(b"123:token").digest(), raw, hashlib.sha256).hexdigest()
    assert cp.verify_webhook(raw, good) and not cp.verify_webhook(raw, "00") and not CryptoPay("", "x").verify_webhook(raw, good)
