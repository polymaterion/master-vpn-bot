"""Применяет бренд из .env к приложению клиента (PATCH /branding). Запускается при деплое."""
from __future__ import annotations

import asyncio
import logging
import os

from ..config import PLACEHOLDER
from ..partner.client import PartnerClient, PartnerError


def _clean(value: str) -> str | None:
    value = (value or "").strip()
    return None if not value or PLACEHOLDER in value else value


async def run() -> None:
    brand = (os.environ.get("BRAND_NAME") or "Master VPN").strip()
    body = {
        "brand_name": brand[:64],
        "profile_title": brand[:25],  # приложения обрезают имя профиля длиннее 25 символов
        "announce": (os.environ.get("BRAND_ANNOUNCE") or "Поддержка в Telegram")[:200],
        "support_url": _clean(os.environ.get("SUPPORT_URL", "")),
        "web_page_url": _clean(os.environ.get("WEB_URL", "")),
    }
    client = PartnerClient(os.environ.get("PARTNER_API_BASE") or "https://api.subkeyapi.com", os.environ["PARTNER_API_KEY"])
    try:
        res = await client.patch_branding(body)
        print("Бренд применён:", {k: res.get(k) for k in ("brand_name", "profile_title", "support_url", "web_page_url")})
    except PartnerError as e:
        print(f"Не удалось применить бренд: {e}")
    finally:
        await client.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run())
