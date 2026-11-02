from __future__ import annotations

import logging

from ..partner.client import PartnerClient, PartnerError

log = logging.getLogger("plans")


class PlanCatalog:
    """Ступени сетки по числу устройств. На пробном проекте сетки нет – берём «Тест интеграции»."""

    def __init__(self, partner: PartnerClient):
        self.partner = partner
        self.grid: dict[int, dict] = {}
        self.developer: dict | None = None

    async def refresh(self) -> None:
        plans = (await self.partner.plans())["plans"]
        self.grid = {p["device_limit"]: p for p in plans if p["kind"] == "grid"}
        self.developer = next((p for p in plans if p["kind"] == "developer"), None)
        if not self.grid and self.developer:
            log.warning("Пробный проект: продаётся только «Тест интеграции», срок ограничен %s сут.",
                        self.developer["duration_days"])

    async def ensure(self) -> None:
        """Каталог пуст (бот стартовал на пробе, а проект уже перешёл на боевые условия) – обновляем без перезапуска."""
        if not self.grid:
            await self.refresh()

    @property
    def trial_project(self) -> bool:
        return not self.grid and self.developer is not None

    def plan_for(self, devices: int) -> dict:
        if devices in self.grid:
            return self.grid[devices]
        if self.developer is not None and not self.grid:
            return self.developer
        raise PartnerError(0, {"code": "plan_missing", "error": f"нет ступени на {devices} устройств"})

    def clamp_days(self, plan: dict, days: int) -> int:
        return min(days, plan["duration_days"]) if plan["kind"] == "developer" else days
