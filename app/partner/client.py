"""Клиент Partner API «Мастерская VPN».

Сеть, 429 и 5xx повторяются с тем же Idempotency-Key. На запасной адрес
переключаемся только при сетевой ошибке (api_base_urls из GET /me).
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any
from urllib.parse import quote

import aiohttp

log = logging.getLogger("partner")

PREFIX = "/api/v1/partner"
RETRY_STATUSES = (429, 500, 502, 503, 504)
# Коды, после которых заказ нужно повторить позже с тем же ключом.
RETRYABLE_CODES = {
    "insufficient_balance", "rate_limited", "unavailable", "internal",
    "economics_not_ready", "price_changed", "network",
}


class PartnerError(Exception):
    def __init__(self, status: int, body: Any = None, request_id: str = ""):
        body = body if isinstance(body, dict) else {}
        self.status = status
        self.code: str = body.get("code") or ""
        self.kind: str = body.get("kind") or ""
        self.detail: str = body.get("error") or ""
        self.request_id: str = body.get("request_id") or request_id
        super().__init__(f"{status} {self.code}: {self.detail}")

    @property
    def retryable(self) -> bool:
        return self.code in RETRYABLE_CODES or self.status in (0,) + RETRY_STATUSES


class PartnerClient:
    def __init__(self, base_url: str, api_key: str, *, attempts: int = 4, backoff: float = 1.0,
                 session: aiohttp.ClientSession | None = None):
        self.bases = [base_url.rstrip("/")]
        self.api_key = api_key
        self.attempts = attempts
        self.backoff = backoff
        self._idx = 0
        self._session = session

    @property
    def session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    def set_bases(self, urls: list[str]) -> None:
        urls = [u.rstrip("/") for u in urls if u]
        if urls:
            self.bases = urls
            self._idx = 0

    async def call(self, method: str, path: str, body: dict | None = None, *, key: str | None = None,
                   params: dict | None = None, attempts: int | None = None) -> Any:
        attempts = attempts or self.attempts
        headers = {"Authorization": f"Bearer {self.api_key}"}
        if key:
            headers["Idempotency-Key"] = key
        last: Exception | None = None
        for attempt in range(attempts):
            base = self.bases[self._idx % len(self.bases)]
            try:
                async with self.session.request(
                    method, base + PREFIX + path, json=body, params=params, headers=headers,
                    timeout=aiohttp.ClientTimeout(total=20),
                ) as r:
                    text = await r.text()
                    rid = r.headers.get("X-Request-Id", "")
                    if r.status in RETRY_STATUSES and attempt < attempts - 1:
                        retry_after = int(r.headers.get("Retry-After") or 0) if (r.headers.get("Retry-After") or "").isdigit() else 0
                        log.warning("%s %s -> %s, повтор (%s)", method, path, r.status, rid)
                        await asyncio.sleep(max(self.backoff * 2 ** attempt, retry_after))
                        continue
                    try:
                        data = json.loads(text) if text else None
                    except ValueError:
                        data = None
                    if r.status >= 400:
                        raise PartnerError(r.status, data, rid)
                    return data
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                last = e
                self._idx += 1  # сетевая ошибка: пробуем запасной адрес
                log.warning("%s %s: сетевая ошибка %r", method, path, e)
                if attempt < attempts - 1:
                    await asyncio.sleep(self.backoff * 2 ** attempt)
        raise PartnerError(0, {"code": "network", "error": str(last)})

    # --- справочники и аккаунт -------------------------------------------------
    async def me(self) -> dict:
        return await self.call("GET", "/me")

    async def plans(self) -> dict:
        return await self.call("GET", "/plans")

    async def quote(self, plan_id: str, days: int, *, attempts: int | None = None) -> dict:
        return await self.call("POST", "/quotes", {"plan_id": plan_id, "days": days}, attempts=attempts)

    async def balance(self, *, attempts: int | None = None) -> dict:
        return await self.call("GET", "/balance", attempts=attempts)

    async def stats(self, days: int = 7) -> dict:
        return await self.call("GET", "/stats", params={"days": days})

    async def patch_branding(self, body: dict) -> dict:
        return await self.call("PATCH", "/branding", body)

    async def set_webhook(self, url: str) -> dict:
        return await self.call("PUT", "/webhook", {"url": url})

    async def test_webhook(self) -> dict:
        return await self.call("POST", "/webhook/test", {})

    # --- клиенты ---------------------------------------------------------------
    @staticmethod
    def _u(external_id: str) -> str:
        return "/users/" + quote(external_id, safe="")

    async def user(self, external_id: str) -> dict | None:
        try:
            return await self.call("GET", self._u(external_id))
        except PartnerError as e:
            if e.status == 404:
                return None
            raise

    async def users(self, **params: Any) -> dict:
        return await self.call("GET", "/users", params=params)

    async def devices(self, external_id: str) -> dict:
        return await self.call("GET", self._u(external_id) + "/devices")

    async def delete_device(self, external_id: str, device_id: str) -> None:
        await self.call("DELETE", self._u(external_id) + "/devices/" + quote(device_id, safe=""))

    async def pause(self, external_id: str) -> None:
        await self.call("DELETE", self._u(external_id))

    async def resume(self, external_id: str) -> dict:
        return await self.call("POST", self._u(external_id) + "/resume", {})

    async def rotate(self, external_id: str) -> dict:
        return await self.call("POST", self._u(external_id) + "/subscription/rotate", {})

    # --- заказы ----------------------------------------------------------------
    async def put_user(self, external_id: str, plan_id: str, days: int, key: str, *,
                       max_charge_minor: str | None = None, metadata: dict | None = None) -> dict:
        body: dict[str, Any] = {"plan_id": plan_id, "days": days}
        if max_charge_minor is not None:
            body["max_charge_minor"] = str(max_charge_minor)
        if metadata:
            body["metadata"] = metadata
        return await self.call("PUT", self._u(external_id), body, key=key)

    async def extend(self, external_id: str, days: int, key: str, *, max_charge_minor: str | None = None) -> dict:
        body: dict[str, Any] = {"days": days}
        if max_charge_minor is not None:
            body["max_charge_minor"] = str(max_charge_minor)
        return await self.call("POST", self._u(external_id) + "/extend", body, key=key)

    async def trial(self, external_id: str, key: str, metadata: dict | None = None) -> dict:
        return await self.call("POST", self._u(external_id) + "/trial", {"metadata": metadata} if metadata else {}, key=key)
