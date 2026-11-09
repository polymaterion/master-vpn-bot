"""Platega.io: оплата рублями по СБП (QR). Платёж на 15 минут, уведомление + проверка статуса."""
from __future__ import annotations

import hmac
import logging
from decimal import Decimal, InvalidOperation
from typing import Mapping

import aiohttp

log = logging.getLogger("platega")

METHOD_SBP_QR = 2


class PlategaError(Exception):
    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status


class Platega:
    name = "platega"
    currency = "RUB"

    def __init__(self, enabled_flag: bool, merchant_id: str = "", secret: str = "",
                 api_base: str = "https://app.platega.io", create_path: str = "/transaction/process",
                 session: aiohttp.ClientSession | None = None):
        self.enabled_flag, self.merchant_id, self.secret = enabled_flag, merchant_id, secret
        self.api_base, self.create_path = api_base.rstrip("/"), create_path
        self._session = session

    @property
    def enabled(self) -> bool:
        return bool(self.enabled_flag and self.merchant_id and self.secret)

    @property
    def session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def _call(self, method: str, path: str, body: dict | None = None) -> dict:
        headers = {"X-MerchantId": self.merchant_id, "X-Secret": self.secret}
        try:
            async with self.session.request(method, self.api_base + path, json=body, headers=headers,
                                            timeout=aiohttp.ClientTimeout(total=15)) as r:
                try:
                    data = await r.json(content_type=None)
                except ValueError:
                    data = {}
                if r.status >= 400:
                    trace = data.get("traceId", "") if isinstance(data, dict) else ""
                    msg = data.get("message", "") if isinstance(data, dict) else ""
                    raise PlategaError(f"{r.status} {msg} trace={trace}", r.status)
                return data if isinstance(data, dict) else {}
        except aiohttp.ClientError as e:
            raise PlategaError(str(e)) from e

    async def create_invoice(self, amount: Decimal, description: str, payload: str, metadata: dict,
                             return_url: str) -> tuple[str, str]:
        """Возвращает (transactionId, ссылка на оплату). payload – id нашего платежа."""
        body = {"paymentMethod": METHOD_SBP_QR,
                "paymentDetails": {"amount": float(amount), "currency": self.currency},
                "description": description[:200], "return": return_url, "failedUrl": return_url,
                "payload": payload, "metadata": metadata}
        try:
            res = await self._call("POST", self.create_path, body)
        except PlategaError as e:
            if e.status != 400:
                raise
            # метод с заданным способом оплаты может не принимать metadata – повторяем без неё
            log.warning("Platega отклонила запрос с metadata (%s), повторяем без неё", e)
            body.pop("metadata")
            res = await self._call("POST", self.create_path, body)
        if not res.get("transactionId") or not res.get("redirect"):
            raise PlategaError("в ответе нет transactionId/redirect")
        return str(res["transactionId"]), str(res["redirect"])

    async def status(self, transaction_id: str) -> dict:
        """{'status': PENDING|CONFIRMED|CANCELED|CHARGEBACKED, 'amount': Decimal|None, 'currency': str|None}"""
        res = await self._call("GET", f"/transaction/{transaction_id}")
        details = res.get("paymentDetails") if isinstance(res.get("paymentDetails"), dict) else {}
        try:
            amount = Decimal(str(details["amount"])) if details.get("amount") is not None else None
        except InvalidOperation:
            amount = None
        return {"status": str(res.get("status", "")), "amount": amount, "currency": details.get("currency")}

    def verify_callback(self, headers: Mapping[str, str]) -> bool:
        """Подписи нет: Platega присылает наши X-MerchantId и X-Secret."""
        if not (self.merchant_id and self.secret):
            return False
        ok_id = hmac.compare_digest(headers.get("X-MerchantId", "").encode(), self.merchant_id.encode())
        ok_secret = hmac.compare_digest(headers.get("X-Secret", "").encode(), self.secret.encode())
        return ok_id and ok_secret
