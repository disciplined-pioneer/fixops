# Контекст для анализа ошибки

Ошибка (из production-лога, trace_id=req-abc123): services/discount.py:14 в функции calculate()
```
AttributeError: 'NoneType' object has no attribute 'discount'
```

Цепочка вызовов (от входной точки до места ошибки и дальше), с реальным кодом каждой функции:

### `<entrypoint>`
уверенность источника: **runtime-confirmed**
_нет исходника в индексе (внешний код / динамический объект / точка входа)_

### `api.payment.PaymentService.pay`
уверенность источника: **runtime-confirmed**
файл: `api/payment.py` (строки 11–13)
```python
    def pay(self, order):
        discount = DiscountService().calculate(order)
        return discount
```

### `services.discount.DiscountService.calculate` ⬅ ЗДЕСЬ ПРОИЗОШЛА ОШИБКА
уверенность источника: **error-location (подтверждено логом)**
файл: `services/discount.py` (строки 11–14)
```python
    def calculate(self, order):
        repo = PromoRepository()
        promo = repo.get(order.promo)
        return promo.discount
```

### `repositories.promo.PromoRepository.__init__`
уверенность источника: **static-resolved**
_нет исходника в индексе (внешний код / динамический объект / точка входа)_

### `repositories.promo.PromoRepository.get`
уверенность источника: **static-resolved**
файл: `repositories/promo.py` (строки 15–18)
```python
    def get(self, code):
        # Баг: если промокода нет в базе, возвращается None вместо
        # понятной ошибки — это и есть настоящий источник проблемы.
        return _PROMO_DB.get(code)
```

### `_PROMO_DB.get`
уверенность источника: **static-unresolved (эвристика, могла ошибиться)**
_нет исходника в индексе (внешний код / динамический объект / точка входа)_

Эвристические кандидаты на первопричину:
- `repositories.promo.PromoRepository.__init__`
- `_PROMO_DB.get`
- `repositories.promo.PromoRepository.get`

==================================================
СТРОГИЕ ТРЕБОВАНИЯ К ОТВЕТУ:
1. Найди первопричину (Root Cause) и исправь её.
2. Верни СТРОГО ДВА БЛОКА КОДА (`fix` и `test`). Никакого текста вне блоков, заголовков, пояснений, слова 'FIX' или 'Фрагмент кода' быть НЕ ДОЛЖНО.
3. В unit-тесте тестируй зафикшенный метод НАПРЯМУЮ. Для мокинга внешних БД и зависимостей используй ТОЛЬКО `monkeypatch` (без unittest.mock). Покрой как случай с ошибкой, так и успешный сценарий.
4. Внутри блока `fix` первой строкой ОБЯЗАТЕЛЬНО укажи `FILE: <путь>`. Формат строго следующий:

```fix
FILE: <относительный_путь_к_файлу>
<<<<<<< SEARCH
<точный_оригинальный_код_без_номеров_строк_который_нужно_заменить>
=======
<новый_исправленный_код>
>>>>>>> REPLACE
```

```test
FILE: <относительный_путь_к_файлу_теста,_например_tests/test_promo.py>
import pytest
...
<полный_код_теста_на_pytest_с_использованием_monkeypatch>
```