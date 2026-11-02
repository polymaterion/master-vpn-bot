"""Регистрирует вебхук Partner API и печатает секрет.

Каждый запуск ВЫПУСКАЕТ НОВЫЙ секрет – прежний перестаёт подходить. Скопируйте напечатанный секрет
в PARTNER_WEBHOOK_SECRET в .env и перезапустите бота.
"""
from __future__ import annotations

import asyncio
import os

from ..partner.client import PartnerClient


async def run() -> None:
    base = (os.environ.get("PUBLIC_BASE_URL") or "").rstrip("/")
    if not base.startswith("https://"):
        raise SystemExit("Задайте PUBLIC_BASE_URL=https://ваш-домен в .env")
    client = PartnerClient(os.environ.get("PARTNER_API_BASE") or "https://api.subkeyapi.com", os.environ["PARTNER_API_KEY"])
    try:
        res = await client.set_webhook(base + "/partner-webhook")
        print("PARTNER_WEBHOOK_SECRET=" + res["secret"])
        print("Скопируйте строку выше в .env и перезапустите бота, затем проверьте доставку: "
              "python -m app.scripts.set_webhook --test")
    finally:
        await client.close()


async def test() -> None:
    client = PartnerClient(os.environ.get("PARTNER_API_BASE") or "https://api.subkeyapi.com", os.environ["PARTNER_API_KEY"])
    try:
        print(await client.test_webhook())
    finally:
        await client.close()


if __name__ == "__main__":
    import sys
    asyncio.run(test() if "--test" in sys.argv else run())
