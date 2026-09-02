import pytest
from services.pricing import PricingService

class MockProduct:
    def __init__(self, price):
        self.price = price

def test_calculate_total_success(monkeypatch):
    # Arrange: mock inventory database
    monkeypatch.setattr("repositories.inventory._INVENTORY_DB", {"ABC123": MockProduct(10.0)})
    service = PricingService()
    items = [{"sku": "ABC123", "qty": 3}]
    # Act
    total = service.calculate_total(items)
    # Assert
    assert total == 30.0

def test_calculate_total_missing_sku(monkeypatch):
    # Arrange: empty inventory database
    monkeypatch.setattr("repositories.inventory._INVENTORY_DB", {})
    service = PricingService()
    items = [{"sku": "UNKNOWN", "qty": 1}]
    # Act & Assert
    with pytest.raises(ValueError, match="Product with SKU UNKNOWN not found"):
        service.calculate_total(items)