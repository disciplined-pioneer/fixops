class Product:
    """Модель товара."""
    def __init__(self, sku: str, price: float):
        """Инициализация товара с SKU и ценой."""
        self.sku = sku
        self.price = price