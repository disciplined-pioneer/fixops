from sample_app.core.decorators import log_execution
from services.pricing import PricingService


class CheckoutService:
    @log_execution(event="checkout.create")
    def checkout(self, order):
        total = PricingService().calculate_total(order["items"])
        return {"total": total}