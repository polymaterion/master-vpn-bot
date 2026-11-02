"""Настройки из .env. Цены задаются отдельными переменными PRICE_<ВАЛЮТА>_<N>M_<N>D."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Mapping

MONTHS = (1, 3, 6)
DEVICES = (1, 2, 4, 8)
CURRENCIES = ("RUB", "STARS", "USDT")
DAYS_PER_MONTH = 30
PLACEHOLDER = "CHANGE_ME"

LINK_ENV = {
    "android": "LINK_ANDROID",
    "ios": "LINK_IOS",
    "windows": "LINK_WINDOWS",
    "policy": "LINK_POLICY",
    "terms": "LINK_TERMS",
}
# Прежние имена переменных продолжают работать, чтобы не ломать уже заполненный .env
LEGACY_LINK_ENV = {"android": "LINK_GOOGLE_PLAY", "ios": "LINK_APP_STORE", "windows": "LINK_APK"}


class ConfigError(RuntimeError):
    pass


def price_var(currency: str, months: int, devices: int) -> str:
    return f"PRICE_{currency}_{months}M_{devices}D"


def _parse_price(currency: str, raw: str) -> Decimal:
    raw = raw.strip()
    if currency in ("RUB", "STARS"):
        if not raw.isdigit() or int(raw) <= 0:
            raise ValueError("нужно целое число больше нуля")
        return Decimal(int(raw))
    try:
        value = Decimal(raw)
    except InvalidOperation:
        raise ValueError("нужно число, например 4.99") from None
    if value <= 0 or value.as_tuple().exponent < -2:  # type: ignore[operator]
        raise ValueError("нужно число больше нуля не больше чем с двумя знаками после точки")
    return value


@dataclass(frozen=True)
class Settings:
    bot_token: str
    partner_api_key: str
    partner_api_base: str
    partner_webhook_secret: str
    public_base_url: str
    database_url: str
    admin_ids: tuple[int, ...]
    brand_name: str
    support_url: str
    web_url: str
    links: dict[str, str]
    cryptopay_token: str
    cryptopay_api_base: str
    platega_enabled: bool
    prices: dict[tuple[str, int, int], Decimal]
    webhook_port: int = 8080
    throttle_seconds: float = 1.0
    reminder_days: int = 3
    reminder_hours_utc: tuple[int, int] = (6, 17)

    def price(self, currency: str, months: int, devices: int) -> Decimal:
        return self.prices[(currency, months, devices)]

    def unfilled_links(self) -> list[str]:
        items = {"SUPPORT_URL": self.support_url, "WEB_URL": self.web_url}
        items.update({LINK_ENV[k]: v for k, v in self.links.items()})
        return [name for name, value in items.items() if not value or PLACEHOLDER in value]

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "Settings":
        problems: list[str] = []

        def need(name: str) -> str:
            value = (env.get(name) or "").strip()
            if not value:
                problems.append(f"{name}: не задано")
            return value

        prices: dict[tuple[str, int, int], Decimal] = {}
        for cur in CURRENCIES:
            for m in MONTHS:
                for d in DEVICES:
                    name = price_var(cur, m, d)
                    raw = (env.get(name) or "").strip()
                    if not raw:
                        problems.append(f"{name}: не задано")
                        continue
                    try:
                        prices[(cur, m, d)] = _parse_price(cur, raw)
                    except ValueError as e:
                        problems.append(f"{name}={raw}: {e}")

        bot_token = need("BOT_TOKEN")
        api_key = need("PARTNER_API_KEY")
        db_url = need("DATABASE_URL")
        try:
            admins = tuple(int(x) for x in (env.get("ADMIN_IDS") or "").replace(" ", "").split(",") if x)
        except ValueError:
            admins = ()
            problems.append("ADMIN_IDS: нужен список чисел через запятую")
        if not admins:
            problems.append("ADMIN_IDS: укажите хотя бы одного администратора")
        if problems:
            raise ConfigError("Неверная конфигурация .env:\n  " + "\n  ".join(problems))

        try:
            h1, h2 = (env.get("REMINDER_HOURS_UTC") or "6-17").split("-")
            hours = (int(h1), int(h2))
        except ValueError:
            hours = (6, 17)

        return cls(
            bot_token=bot_token,
            partner_api_key=api_key,
            partner_api_base=(env.get("PARTNER_API_BASE") or "https://api.subkeyapi.com").rstrip("/"),
            partner_webhook_secret=(env.get("PARTNER_WEBHOOK_SECRET") or "").strip(),
            public_base_url=(env.get("PUBLIC_BASE_URL") or "").rstrip("/"),
            database_url=db_url,
            admin_ids=admins,
            brand_name=(env.get("BRAND_NAME") or "Master VPN").strip(),
            support_url=(env.get("SUPPORT_URL") or "").strip(),
            web_url=(env.get("WEB_URL") or "").strip(),
            links={k: (env.get(v) or env.get(LEGACY_LINK_ENV.get(k, "")) or "").strip() for k, v in LINK_ENV.items()},
            cryptopay_token=(env.get("CRYPTOPAY_TOKEN") or "").strip(),
            cryptopay_api_base=(env.get("CRYPTOPAY_API_BASE") or "https://pay.crypt.bot/api").rstrip("/"),
            platega_enabled=(env.get("PLATEGA_ENABLED") or "false").strip().lower() in ("1", "true", "yes"),
            prices=prices,
            webhook_port=int(env.get("WEBHOOK_PORT") or 8080),
            throttle_seconds=float(env.get("THROTTLE_SECONDS") or 1.0),
            reminder_days=int(env.get("REMINDER_DAYS") or 3),
            reminder_hours_utc=hours,
        )
