# Контекст для анализа ошибки

Ошибка (из production-лога, trace_id=—): services/pricing.py:14 в функции calculate_total()
```
AttributeError: 'NoneType' object has no attribute 'price'
```

Цепочка вызовов (от входной точки до места ошибки и дальше), с реальным кодом каждой функции:

### `services.pricing.PricingService.calculate_total` ⬅ ЗДЕСЬ ПРОИЗОШЛА ОШИБКА
уверенность источника: **error-location (подтверждено логом)**
файл: `services/pricing.py` (строки 8–15)
```python
    def calculate_total(self, items):
        """Рассчитывает общую стоимость переданных позиций заказа."""
        repo = InventoryRepository()
        total = 0.0
        for item in items:
            product = repo.get(item["sku"])
            total += product.price * item["qty"]
        return total
```

### `repositories.inventory.InventoryRepository.__init__`
уверенность источника: **static-resolved**
_нет исходника в индексе (внешний код / динамический объект / точка входа)_

### `repositories.inventory.InventoryRepository.get`
уверенность источника: **static-resolved**
файл: `repositories/inventory.py` (строки 14–25)
```python
    def get(self, sku):
        """Получает объект товара по его SKU."""
        # Баг: для неизвестного SKU метод возвращает None вместо понятной
        # ошибки. Ниже по стеку (в PricingService.calculate_total) это
        # всплывёт как AttributeError: 'NoneType' object has no attribute 'price'.
        log = get_logger(event="inventory.get", sku=sku)
        product = _INVENTORY_DB.get(sku)
        if product is None:
            log.warning("SKU not found, returning None")
        else:
            log.debug("SKU resolved", price=product.price)
        return product
```

### `core.logging.get_logger`
уверенность источника: **static-resolved**
файл: `core/logging.py` (строки 108–120)
```python
def get_logger(
    event: str,
    **context,
):
    """
    Создаёт logger с контекстом события.
    """

    return app_logger.bind(
        event=event,
        event_id=str(uuid.uuid4()),
        **context,
    )
```

### `app_logger.bind`
уверенность источника: **static-unresolved (эвристика, могла ошибиться)**
_нет исходника в индексе (внешний код / динамический объект / точка входа)_

### `str`
уверенность источника: **static-unresolved (эвристика, могла ошибиться)**
_нет исходника в индексе (внешний код / динамический объект / точка входа)_

### `uuid.uuid4`
уверенность источника: **static-unresolved (эвристика, могла ошибиться)**
_нет исходника в индексе (внешний код / динамический объект / точка входа)_

### `_INVENTORY_DB.get`
уверенность источника: **static-unresolved (эвристика, могла ошибиться)**
_нет исходника в индексе (внешний код / динамический объект / точка входа)_

### `log.warning`
уверенность источника: **static-unresolved (эвристика, могла ошибиться)**
_нет исходника в индексе (внешний код / динамический объект / точка входа)_

### `log.debug`
уверенность источника: **static-unresolved (эвристика, могла ошибиться)**
_нет исходника в индексе (внешний код / динамический объект / точка входа)_

Эвристические кандидаты на первопричину:
- `repositories.inventory.InventoryRepository.__init__`
- `app_logger.bind`
- `str`
- `uuid.uuid4`
- `_INVENTORY_DB.get`
- `log.warning`
- `log.debug`

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