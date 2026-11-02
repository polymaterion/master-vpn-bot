from aiohttp import web
from aiohttp.test_utils import TestServer
import pytest

from app.partner.client import PartnerClient, PartnerError


async def test_retries_503_with_same_idempotency_key():
    seen: list[str] = []

    async def handler(request: web.Request):
        seen.append(request.headers.get("Idempotency-Key", ""))
        if len(seen) < 3:
            return web.json_response({"code": "unavailable"}, status=503, headers={"Retry-After": "0"})
        return web.json_response({"external_id": "tg-1", "replayed": False})

    app = web.Application()
    app.router.add_put("/api/v1/partner/users/{ext}", handler)
    async with TestServer(app) as server:
        client = PartnerClient(str(server.make_url("")), "uk_x", backoff=0.01)
        res = await client.put_user("tg-1", "plan", 30, "stars-1", max_charge_minor="10500")
        await client.close()
    assert res["external_id"] == "tg-1" and seen == ["stars-1"] * 3


async def test_business_error_not_retried_and_parsed():
    calls = 0

    async def handler(request: web.Request):
        nonlocal calls
        calls += 1
        return web.json_response({"code": "insufficient_balance", "kind": "conflict", "request_id": "req_1"}, status=409)

    app = web.Application()
    app.router.add_put("/api/v1/partner/users/{ext}", handler)
    async with TestServer(app) as server:
        client = PartnerClient(str(server.make_url("")), "uk_x", backoff=0.01)
        with pytest.raises(PartnerError) as e:
            await client.put_user("tg-1", "plan", 30, "k")
        await client.close()
    assert calls == 1 and e.value.code == "insufficient_balance" and e.value.retryable and e.value.request_id == "req_1"


async def test_network_error_fails_over_to_backup_address():
    async def handler(request: web.Request):
        return web.json_response({"ok": 1})

    app = web.Application()
    app.router.add_get("/api/v1/partner/me", handler)
    async with TestServer(app) as server:
        client = PartnerClient("http://127.0.0.1:1", "uk_x", backoff=0.01)
        client.set_bases(["http://127.0.0.1:1", str(server.make_url(""))])
        assert await client.me() == {"ok": 1}
        await client.close()


async def test_404_user_is_none():
    async def handler(request: web.Request):
        return web.json_response({"code": "not_found", "kind": "not_found"}, status=404)

    app = web.Application()
    app.router.add_get("/api/v1/partner/users/{ext}", handler)
    async with TestServer(app) as server:
        client = PartnerClient(str(server.make_url("")), "uk_x")
        assert await client.user("tg-404") is None
        await client.close()
