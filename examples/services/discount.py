from repositories.promo import PromoRepository


class DiscountService:

    def calculate(self, order):
        repo = PromoRepository()
        promo = repo.get(order.promo)
        return promo.discount
