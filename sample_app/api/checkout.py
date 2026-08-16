from services.pricing import PricingService


class CheckoutService:
    def checkout(self, order):
        total = PricingService().calculate_total(order["items"])
        return {"total": total}