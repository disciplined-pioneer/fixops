from typing import Any
from sample_app.core.decorators import log_execution
from repositories.inventory import InventoryRepository


class PricingService:
    """Сервис для расчета итоговой стоимости заказа."""
    @log_execution(event="pricing.calculate_total")
    def calculate_total(self, items):
        """Рассчитывает общую стоимость переданных позиций заказа."""
        repo = InventoryRepository()
        total = 0.0
        for item in items:
            product: Any = repo.get(item["sku"])
            total += product.price * item["qty"]
        return total
