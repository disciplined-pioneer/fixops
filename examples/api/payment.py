from services.discount import DiscountService


class PaymentService:

    def pay(self, order):
        discount = DiscountService().calculate(order)
        return discount
