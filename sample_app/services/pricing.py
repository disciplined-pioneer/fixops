from repositories.inventory import InventoryRepository


class PricingService:
    def calculate_total(self, items):
        repo = InventoryRepository()
        total = 0.0
        for item in items:
            product = repo.get(item["sku"])
            total += product.price * item["qty"]
        return total