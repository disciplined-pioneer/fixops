from sample_app.core.decorators import log_execution
from sample_app.core.logging import get_logger
from models.product import Product

_INVENTORY_DB = {
    "SKU-001": Product(sku="SKU-001", price=100.0),
    "SKU-002": Product(sku="SKU-002", price=250.0),
}


class InventoryRepository:
    """Репозиторий для работы с данными об инвентаре товаров."""
    @log_execution(event="inventory.get")
    def get(self, sku):
        """Получает объект товара по его SKU."""
        # Баг: для неизвестного SKU метод возвращает None вместо понятной
        # ошибки. Ниже по стеку (в PricingService.calculate_total) это
        # всплывёт как AttributeError: 'NoneType' object has no attribute 'price'.
        log = get_logger(event="inventory.get", sku=sku)
        product = _INVENTORY_DB.get(sku)
        if product is None:
            log.warning("SKU not found, returning None")
        else:
            log.debug("SKU resolved", price=product.price)
        return product