"""Выгрузка пользователей в CSV и текст сводки по источникам."""
from __future__ import annotations

import csv
import io
from typing import Sequence

from ..db import UserRow

HEADER = ["tg_id", "username", "first_name", "last_name", "source", "started_at_utc", "paid"]


def _safe(value: object) -> str:
    """Защита от формул в Excel/Sheets: ячейка не должна начинаться с = + - @."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def build_users_csv(users: Sequence[UserRow], paid_ids: set[int]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(HEADER)
    for u in users:
        w.writerow([u.tg_id, _safe(u.username), _safe(u.first_name), _safe(u.last_name), _safe(u.source),
                    u.created_at.strftime("%Y-%m-%d %H:%M") if u.created_at else "",
                    "yes" if u.tg_id in paid_ids else "no"])
    return buf.getvalue().encode("utf-8-sig")  # BOM: Excel на телефоне/ПК не ломает кириллицу


def format_sources(rows: list[dict], bot_username: str, days: int | None) -> str:
    period = f"за {days} сут." if days else "за всё время"
    total = sum(r["users"] for r in rows)
    lines = [f"📈 Источники {period}: всего {total}"]
    for r in rows[:40]:
        name = r["source"] or "без метки (прямой заход)"
        pct = f"{r['paid'] * 100 / r['users']:.0f}%" if r["users"] else "–"
        lines.append(f"• {name}: {r['users']} польз., оплатили {r['paid']} ({pct})")
    if len(rows) > 40:
        lines.append(f"… и ещё {len(rows) - 40}")
    lines.append(f"\nСсылка с меткой: https://t.me/{bot_username}?start=МЕТКА\n"
                 "Метка: латиница, цифры, _ и -, до 64 символов, например yt_ivan или tg_chan_nov.")
    return "\n".join(lines)
