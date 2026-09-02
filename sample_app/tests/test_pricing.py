import pytest
from services.pricing import PricingService
from repositories import inventory as inv

class DummyProduct:
    def __init__(self, price):
        self.price = price

@pytest.fixture
def pricing_service():
    return PricingService()

def test_calculate_total_with_missing_sku(monkeypatch, pricing_service):
    # Mock database to return None for any SKU
    monkeypatch.setattr(inv, "_INVENTORY_DB", {})
    items = [{"sku": "UNKNOWN", "qty": 1}]
    with pytest.raises(ValueError, match="SKU UNKNOWN not found"):
        pricing_service.calculate_total(items)

def test_calculate_total_success(monkeypatch, pricing_service):
    # Mock database with one product
    product = DummyProduct(price=10.0)
    monkeypatch.setattr(inv, "_INVENTORY_DB", {"SKU123": product})
    items = [{"sku": "SKU123", "qty": 3}]
    total = pricing_service.calculate_total(items)
    assert total == 30.0