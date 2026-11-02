"""Platega.io – заглушка. Пока `implemented = False`, кнопка «Рубли» показывает «скоро будет доступно».

Как подключить: реализуйте create_invoice() и webhook-обработчик (по образцу cryptopay.py),
поставьте implemented = True и PLATEGA_ENABLED=true в .env. Остальной код менять не нужно:
оплата проходит через OrderService.process_paid(payment_id, provider_ref).
"""
from __future__ import annotations


class Platega:
    name = "platega"
    currency = "RUB"
    implemented = False

    def __init__(self, enabled_flag: bool):
        self.enabled_flag = enabled_flag

    @property
    def enabled(self) -> bool:
        return self.enabled_flag and self.implemented

    async def create_invoice(self, *args, **kwargs):  # pragma: no cover
        raise NotImplementedError("Platega ещё не подключена")
