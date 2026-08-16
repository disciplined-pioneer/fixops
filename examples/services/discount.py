from core.decorators import log_execution
from repositories.promo import PromoRepository


class DiscountService:

    @log_execution(
        event="discount.calculate",
        operation="DiscountService.calculate",
    )
    def calculate(self, order):
        repo = PromoRepository()
        promo = repo.get(order.promo)
        return promo.discount