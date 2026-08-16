from core.decorators import log_execution
from services.discount import DiscountService


class PaymentService:

    @log_execution(
        event="payment.pay",
        operation="PaymentService.pay",
    )
    def pay(self, order):
        discount = DiscountService().calculate(order)
        return discount