from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from aiogram import Bot

from .config import Settings
from .db import Database
from .partner.client import PartnerClient
from .providers.cryptopay import CryptoPay
from .providers.platega import Platega
from .services.orders import OrderService
from .services.plans import PlanCatalog

if TYPE_CHECKING:
    from .notify import BotNotifier
    from .ui.nav import Navigator


@dataclass
class App:
    settings: Settings
    db: Database
    partner: PartnerClient
    plans: PlanCatalog
    orders: OrderService
    notifier: "BotNotifier"
    cryptopay: CryptoPay
    platega: Platega
    bot: Bot
    nav: "Navigator"
