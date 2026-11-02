"""CryptoPay (@CryptoBot): счёт в USDT на 60 минут, вебхук + ручная проверка."""
from __future__ import annotations

import hashlib
import hmac
from decimal import Decimal

import aiohttp

INVOICE_LIFETIME = 3600


class CryptoPayError(Exception):
    pass


class CryptoPay:
    name = "cryptopay"
    currency = "USDT"

    def __init__(self, token: str, api_base: str, session: aiohttp.ClientSession | None = None):
        self.token, self.api_base, self._session = token, api_base.rstrip("/"), session

    @property
    def enabled(self) -> bool:
        return bool(self.token)

    @property
    def session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def _call(self, method: str, params: dict) -> dict:
        try:
            async with self.session.post(f"{self.api_base}/{method}", json=params,
                                         headers={"Crypto-Pay-API-Token": self.token},
                                         timeout=aiohttp.ClientTimeout(total=15)) as r:
                data = await r.json()
        except (aiohttp.ClientError, ValueError) as e:
            raise CryptoPayError(str(e)) from e
        if not data.get("ok"):
            raise CryptoPayError(str(data.get("error")))
        return data["result"]

    async def create_invoice(self, amount: Decimal, description: str, payload: str) -> tuple[str, str]:
        res = await self._call("createInvoice", {
            "currency_type": "crypto", "asset": "USDT", "amount": f"{amount:.2f}",
            "description": description[:1000], "payload": payload, "expires_in": INVOICE_LIFETIME,
            "allow_comments": False, "allow_anonymous": False})
        return str(res["invoice_id"]), res.get("bot_invoice_url") or res.get("pay_url")

    async def status(self, invoice_id: str) -> str:
        """active | paid | expired"""
        res = await self._call("getInvoices", {"invoice_ids": str(invoice_id)})
        items = res["items"] if isinstance(res, dict) else res
        return items[0]["status"] if items else "expired"

    async def delete_invoice(self, invoice_id: str) -> None:
        try:
            await self._call("deleteInvoice", {"invoice_id": int(invoice_id)})
        except (CryptoPayError, ValueError):
            pass

    def verify_webhook(self, raw: bytes, signature: str) -> bool:
        if not self.token:
            return False
        secret = hashlib.sha256(self.token.encode()).digest()
        expected = hmac.new(secret, raw, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature or "")
