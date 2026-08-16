from models.product import Product

_INVENTORY_DB = {
    "SKU-001": Product(sku="SKU-001", price=100.0),
    "SKU-002": Product(sku="SKU-002", price=250.0),
}


class InventoryRepository:
    def get(self, sku):
        # Баг: для неизвестного SKU метод возвращает None вместо понятной
        # ошибки. Ниже по стеку (в PricingService.calculate_total) это
        # всплывёт как AttributeError: 'NoneType' object has no attribute 'price'.
        return _INVENTORY_DB.get(sku)