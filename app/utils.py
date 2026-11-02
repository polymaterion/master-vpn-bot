from __future__ import annotations

import html
import io
from datetime import datetime, timezone
from decimal import Decimal

GIB = 1024 ** 3


def utcnow() -> datetime:
    """Наивное время UTC: так хранится в БД и сравнивается везде."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt


def fmt_date(dt: datetime | None) -> str:
    return dt.strftime("%d.%m.%Y") if dt else "–"


def fmt_gib(value: str | int | None) -> str:
    gib = int(value or 0) / GIB
    text = f"{gib:.1f}".rstrip("0").rstrip(".") if gib < 100 else f"{gib:.0f}"
    return f"{text or '0'} ГиБ"


def fmt_money(currency: str, amount: Decimal | int | float) -> str:
    amount = Decimal(amount)
    if currency == "RUB":
        return f"{int(amount)} ₽"
    if currency == "STARS":
        return f"{int(amount)} ⭐"
    return f"{amount:.2f} USDT"


def esc(value: object) -> str:
    return html.escape(str(value), quote=False)


def make_qr_png(data: str) -> bytes:
    import qrcode

    buf = io.BytesIO()
    qrcode.make(data).save(buf, "PNG")
    return buf.getvalue()
