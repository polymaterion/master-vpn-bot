from __future__ import annotations

import asyncio
import logging
import os

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiohttp import web

from . import jobs
from .config import Settings
from .context import App
from .db import Database
from .handlers.admin import build_admin_router
from .handlers.middleware import ProfileMiddleware, ThrottleMiddleware
from .handlers.inline import router as inline_router
from .handlers.user import router as user_router
from .notify import BotNotifier
from .partner.client import PartnerClient
from .providers.cryptopay import CryptoPay
from .providers.platega import Platega
from .services.orders import OrderService
from .services.plans import PlanCatalog
from .ui import screens  # noqa: F401  (регистрация экранов)
from .ui.nav import Navigator
from .webhooks import build_web_app


def build_app(settings: Settings) -> App:
    bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    db = Database(settings.database_url)
    partner = PartnerClient(settings.partner_api_base, settings.partner_api_key)
    plans = PlanCatalog(partner)
    notifier = BotNotifier(bot, settings, db)
    orders = OrderService(db, partner, plans, notifier)
    app = App(settings=settings, db=db, partner=partner, plans=plans, orders=orders, notifier=notifier,
              cryptopay=CryptoPay(settings.cryptopay_token, settings.cryptopay_api_base),
              platega=Platega(settings.platega_enabled, settings.platega_merchant_id, settings.platega_secret,
                              settings.platega_api_base, settings.platega_create_path), bot=bot, nav=None)  # type: ignore[arg-type]
    app.nav = Navigator(app)
    return app


async def main() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = Settings.from_env(os.environ)  # без полной конфигурации (включая все цены) бот не стартует
    app = build_app(settings)

    dp = Dispatcher()
    dp["app"] = app
    throttle = ThrottleMiddleware(settings.throttle_seconds)
    dp.message.outer_middleware(throttle)
    dp.callback_query.outer_middleware(throttle)
    profile = ProfileMiddleware()
    dp.message.outer_middleware(profile)
    dp.callback_query.outer_middleware(profile)
    dp.include_router(build_admin_router(settings.admin_ids))
    dp.include_router(inline_router)
    dp.include_router(user_router)

    runner = web.AppRunner(build_web_app(app))
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", settings.webhook_port).start()

    warnings = await jobs.startup_checks(app)
    if warnings:
        await app.notifier.admins("⚠️ Проверка при старте:\n• " + "\n• ".join(warnings))

    tasks = [asyncio.create_task(jobs.retry_loop(app)), asyncio.create_task(jobs.reminders_loop(app)),
             asyncio.create_task(jobs.daily_loop(app))]
    if app.platega.enabled:
        tasks.append(asyncio.create_task(jobs.platega_loop(app)))
    try:
        await dp.start_polling(app.bot, handle_signals=True)
    finally:
        for t in tasks:
            t.cancel()
        await runner.cleanup()
        await app.partner.close()
        await app.cryptopay.close()
        await app.platega.close()
        await app.db.close()
        await app.bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
