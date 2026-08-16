from models.promo_model import Promo

_PROMO_DB = {
    "SUMMER10": Promo(code="SUMMER10", discount=10),
}


class PromoRepository:

    def get(self, code):
        # Баг: если промокода нет в базе, возвращается None вместо
        # понятной ошибки — это и есть настоящий источник проблемы.
        return _PROMO_DB.get(code)
