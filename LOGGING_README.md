# Логирование в проекте (core/logging.py + core/decorators.py)

Документ описывает, как устроена и как использовать инфраструктуру логирования,
которая лежит в папке `core/` в обоих проектах репозитория:

- `sample_app/core/` — FastAPI-приложение (checkout → pricing → inventory);
- `generator_report/core/` — скрипт генерации отчёта по продажам.

Код в обеих папках `core/` **идентичен** (отличается только путь импорта:
`from core.logging import ...` в `generator_report` и
`from sample_app.core.logging import ...` в `sample_app`). Поэтому вся логика
ниже описана один раз и относится к обоим проектам.

Стек: [loguru](https://github.com/Delgan/loguru). Формат логов — структурированный
JSON, что позволяет системам вроде FixOps, ELK, Grafana Loki, Sentry и т.п.
парсить логи без regex'ов.

---

## 1. Что лежит в `core/`

| Файл | Назначение |
|---|---|
| `logging.py` | Инициализация loguru: куда пишем логи, в каком формате, какой уровень. Даёт `app_logger` и `get_logger(...)`. |
| `decorators.py` | Декоратор `@log_execution(...)`, который автоматически логирует вызов функции (старт/успех/ошибку) + функция `sanitize()`, скрывающая чувствительные поля. |
| `middleware.py` | FastAPI middleware, которая генерирует `request_id` на каждый HTTP-запрос (только в `sample_app`, в `generator_report` HTTP нет, но файл там тоже есть — не используется). |

---

## 2. `core/logging.py` — как настроена запись логов

При **самом первом импорте** этого модуля (в любом месте проекта) происходит следующее:

1. **Вычисляется корень проекта** — `PROJECT_ROOT = Path(__file__).resolve().parents[1]`
   (папка на уровень выше `core/`).
2. **Читаются переменные окружения:**
   - `APP_SERVICE_NAME` — имя сервиса, попадает в каждую запись лога (по умолчанию `app-service`);
   - `APP_ENV` — `production` (по умолчанию) или что угодно другое → включает "dev"-режим;
   - `LOG_DIR` — куда писать файл логов (по умолчанию `<корень проекта>/logs`).
3. **Создаётся папка логов**, если её ещё нет (`os.makedirs(..., exist_ok=True)`).
4. **Удаляются дефолтные обработчики loguru** (`logger.remove()`), чтобы не было двойного вывода.
5. **Регистрируются 2 sink'а** (то есть 2 места, куда одновременно летит каждая запись):

### Sink 1 — консоль (stdout)

- **`APP_ENV=production`** (или переменная не задана — это значение по умолчанию):
  вывод в stdout как **JSON** (`serialize=True`), уровень `INFO+`.
  Именно этот режим нужен, когда контейнер работает под Docker/Kubernetes/FixOps —
  такие системы читают stdout контейнера и ожидают структурированный формат.
- **Любое другое значение `APP_ENV`** (например `development`):
  человекочитаемый цветной вывод с уровнем `DEBUG+`, где сразу видно
  `event`, `модуль:функция:строка` и сообщение.

### Sink 2 — файл (`<LOG_DIR>/app.log`)

Пишется **всегда**, независимо от `APP_ENV`, тоже в формате JSON:

- ротация — новый файл после **10 МБ**;
- хранение — старые файлы удаляются через **7 дней**;
- сжатие — прошлые файлы архивируются в `.gz`;
- `enqueue=True` — запись идёт через отдельный поток/очередь, чтобы логирование
  не тормозило основной код и не терялось при многопоточности/асинхронности;
- `backtrace=True` — при исключении в лог попадает полный traceback.

### `app_logger` и `get_logger()`

```python
app_logger = logger.bind(service=SERVICE_NAME, environment=ENV)
```

`app_logger` — это "базовый" логгер с уже прикреплёнными полями `service` и
`environment`. Он вызывается напрямую, когда не нужен доп. контекст:

```python
app_logger.info("Запуск формирования отчёта по продажам")
```

`get_logger(event, **context)` — фабрика, которая берёт `app_logger` и добавляет:

- `event` — машиночитаемое имя события (например `"inventory.get"`);
- `event_id` — случайный `uuid4`, уникальный для каждого вызова `get_logger()`
  (по нему легко найти в логах именно эту операцию, даже если функция
  вызывается параллельно много раз);
- любые дополнительные `**context`-поля, которые вы передадите.

```python
log = get_logger(event="inventory.get", sku=sku)
log.debug("SKU resolved", price=product.price)
```

Итоговая JSON-запись в файле будет содержать: `service`, `environment`, `event`,
`event_id`, `sku`, время, уровень, сообщение, модуль/функцию/строку кода и т.д.

---

## 3. `core/decorators.py` — декоратор `@log_execution`

Это главный инструмент логирования в проекте: вместо того чтобы вручную
писать `log.info(...)` в начале и в конце каждой функции, вешаем декоратор —
и получаем полный цикл автоматически.

### Сигнатура

```python
@log_execution(event="pricing.calculate_total")
def calculate_total(self, items):
    ...
```

- `event` (обязателен) — имя события для этого вызова, попадает в каждую запись лога.
- `operation` (опционален) — человеческое имя операции; если не передать,
  берётся `func.__name__` (имя функции).

### Что декоратор делает при каждом вызове функции

Декоратор поддерживает **и синхронные, и `async`-функции**
(проверка через `inspect.iscoroutinefunction(func)`, дальше — два похожих
обработчика: `sync_wrapper` и `async_wrapper`).

Шаг за шагом:

1. Засекает время старта: `start = time.perf_counter()`.
2. Создаёт логгер через `get_logger(event=..., operation=..., function=func.__qualname__, module=func.__module__)`
   — то есть уже на этом шаге в контекст лога попадают имя функции и модуль.
3. Пишет **DEBUG**-запись `"Function started"` — функция начала выполняться.
4. Вызывает саму функцию (`func(*args, **kwargs)` или `await func(...)`).
5. **Если функция отработала без ошибок:**
   - считает длительность в мс (`duration_ms`);
   - пишет **INFO**-запись `"Function completed"` с полями
     `status="success"`, `severity="INFO"`, `duration_ms`.
6. **Если функция выбросила исключение:**
   - тоже считает `duration_ms`;
   - собирает `error = {"type": <класс исключения>, "message": <текст>}`;
   - собирает `arguments = {"args": ..., "kwargs": ...}` — аргументы, с которыми
     функция была вызвана (см. защиту данных ниже);
   - пишет **ERROR**-запись `"Function failed"` через `log.exception(...)`
     (это специальный метод loguru/logging, который автоматически прикладывает
     полный traceback);
   - **пробрасывает исключение дальше** (`raise`) — декоратор ничего не "проглатывает",
     он только логирует и передаёт ошибку выше по стеку вызовов.

### Защита чувствительных данных — `sanitize()`

Перед тем как записать `args`/`kwargs` упавшей функции в лог, они проходят через
`sanitize()`:

- рекурсивно проходит по `dict`/`list`/`tuple`;
- если ключ словаря — один из `password`, `token`, `access_token`,
  `refresh_token`, `authorization`, `api_key`, `secret` (без учёта регистра),
  значение заменяется на `"***"`;
- простые типы (`str`, `int`, `float`, `bool`, `None`) остаются как есть;
- любой сложный/неизвестный объект превращается в строку вида `"<ClassName>"`,
  чтобы не пытаться сериализовать в JSON что попало и не раздувать лог.

Это защита на случай, если, например, в `checkout()` придёт заказ с полем
`"api_key"` — оно не утечёт в файл логов при падении функции.

---

## 4. Как это применяется в `sample_app` — сквозной пример

Цепочка вызовов при оформлении заказа:

```
CheckoutService.checkout()          @log_execution(event="checkout.create")
  → PricingService.calculate_total()  @log_execution(event="pricing.calculate_total")
      → InventoryRepository.get()      @log_execution(event="inventory.get")
                                        + внутри ещё вручную:
                                          get_logger(event="inventory.get", sku=...)
```

### `api/checkout.py`

```python
@log_execution(event="checkout.create")
def checkout(self, order):
    total = PricingService().calculate_total(order["items"])
    return {"total": total}
```

Один декоратор — и в логах появляются "Function started"/"Function completed"
(или "Function failed") для каждого оформления заказа, с точным временем
выполнения.

### `services/pricing.py`

Аналогично, но для расчёта суммы:

```python
@log_execution(event="pricing.calculate_total")
def calculate_total(self, items):
    ...
```

### `repositories/inventory.py` — декоратор + ручной лог внутри функции

Здесь декоратор используется **вместе** с прямым вызовом `get_logger()` внутри
тела функции — это нормальный паттерн, когда помимо факта
"функция выполнилась/упала" нужно залогировать ещё и бизнес-детали по ходу
выполнения:

```python
@log_execution(event="inventory.get")
def get(self, sku):
    log = get_logger(event="inventory.get", sku=sku)
    product = _INVENTORY_DB.get(sku)
    if product is None:
        log.warning("SKU not found, returning None")
    else:
        log.debug("SKU resolved", price=product.price)
    return product
```

То есть декоратор отвечает за "обёртку" (старт/успех/ошибка/длительность),
а ручной `get_logger()` внутри — за содержательные события по ходу работы
самой функции (нашли товар / не нашли).

### Что происходит, если передать несуществующий SKU

Если `sku` не найден, `InventoryRepository.get()` возвращает `None`
(с WARNING-логом), но `pricing.calculate_total()` не проверяет это и падает на
`product.price` с `AttributeError: 'NoneType' object has no attribute 'price'`.
Именно эту ошибку и ловит декоратор `@log_execution` на уровне
`calculate_total`, `checkout` и т.д. — она "всплывает" по всей цепочке, и
на каждом уровне декоратор пишет свою ERROR-запись с `traceback`, пока
исключение не будет обработано (или процесс не упадёт).

### `reproduce.py` — как воспроизвести и посмотреть ошибку вживую

Скрипт специально вызывает `checkout()` с несуществующим SKU (`SKU-999`),
чтобы гарантированно получить ошибку и продемонстрировать логи:

```bash
python sample_app/reproduce.py
```

Что делает скрипт:

1. Выставляет `LOG_DIR=sample_app/logs` **до** импорта `core.logging`
   (важно — конфигурация logging применяется один раз при импорте модуля,
   поэтому переменные окружения нужно выставить заранее).
2. Логирует `"Reproducing error"` с деталями заказа.
3. Вызывает `CheckoutService().checkout(order)`.
4. Ловит исключение, достаёт из `traceback` файл/строку/функцию, где реально
   упал код, и пишет ERROR-запись через `LOG.bind(**error_log).error(...)`.
5. Дополнительно печатает эту же информацию в консоль (`print(...)`) — для
   удобства, чтобы не лезть в файл лога руками.

После запуска смотрите файл `sample_app/logs/app.log` — там будет полная
цепочка JSON-записей: от `reproduce.run` до всех промежуточных `checkout.create`
/ `pricing.calculate_total` / `inventory.get`, и в конце — ERROR с traceback.

---

## 5. `core/middleware.py` — request_id (только `sample_app`, FastAPI)

```python
request_id: ContextVar[str | None] = ContextVar("request_id", default=None)

async def request_id_middleware(request, call_next):
    request_id_value = str(uuid.uuid4())
    request_id.set(request_id_value)
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id_value
    return response
```

На каждый входящий HTTP-запрос генерируется уникальный `request_id`,
кладётся в `ContextVar` (то есть доступен из любого места кода в рамках
обработки этого запроса, без явной передачи параметром) и возвращается
клиенту в заголовке `X-Request-ID`.

**Важно:** на данный момент этот `request_id` **не подмешивается**
автоматически в записи `get_logger()`/`log_execution` — он существует
отдельно от системы логирования. Если нужно связать логи одного HTTP-запроса
между собой, стоит доработать `get_logger()`, чтобы он подтягивал
`request_id.get()` в `context`, например:

```python
def get_logger(event: str, **context):
    return app_logger.bind(
        event=event,
        event_id=str(uuid.uuid4()),
        request_id=request_id.get(),
        **context,
    )
```

(это не сделано в текущем коде — просто рекомендация, если понадобится
трассировка запроса целиком).

---

## 6. `generator_report` — та же инфраструктура, другой стиль использования

`generator_report/core/` — точная копия `sample_app/core/`, но в самом
скрипте (`main.py`, `report_generator.py`) декоратор `@log_execution`
**не используется** — там логируют напрямую через `app_logger`:

```python
app_logger.info("Загрузка данных из %s", path)
```

⚠️ **На заметку:** это `%s`-форматирование — синтаксис стандартного модуля
`logging`, а не loguru. Loguru не подставляет `%s` автоматически, поэтому
такие сообщения попадут в лог буквально как `"Загрузка данных из %s"` без
подстановки пути. Правильный вариант для loguru — `{}`-плейсхолдеры:

```python
app_logger.info("Загрузка данных из {}", path)
```

Ошибка воспроизводится похожим образом: `main.py` оборачивает вызов
`build_report()` в `try/except`, вручную достаёт `file`/`function`/`line` из
`traceback` и пишет `app_logger.bind(...).exception(...)`. Баг в самом отчёте —
`IndexError` на последней итерации `calculate_growth()` (обращение к
несуществующему `days[i + 1]`) — специально оставлен для тренировки чтения
traceback'ов.

---

## 7. Куда смотреть за логами и что там искать

| Что нужно | Где искать |
|---|---|
| Все логи проекта | `<корень_проекта>/logs/app.log` (JSON, по одной записи на строку) |
| Только ошибки | фильтр по `"level":{"name":"ERROR"}` в JSON |
| Логи конкретной операции | фильтр по `"event":"..."` (например `inventory.get`) |
| Логи одного конкретного вызова | фильтр по `"event_id":"..."` (уникален на вызов `get_logger`) |
| Время выполнения функции | поле `duration_ms` в записи `"Function completed"`/`"Function failed"` |
| В докере / под FixOps | stdout контейнера (JSON, `APP_ENV=production` по умолчанию) — в `docker-compose.yml` за это отвечают метки `fixops.enabled=true` и `fixops.project_folder` |

---

## 8. Как добавить логирование в новую функцию — шпаргалка

```python
from core.decorators import log_execution
from core.logging import get_logger

@log_execution(event="orders.cancel")          # 1. авто-лог старта/успеха/ошибки + время
def cancel_order(order_id: str):
    log = get_logger(event="orders.cancel", order_id=order_id)  # 2. свой лог с контекстом
    ...
    log.info("Order cancelled")                # 3. содержательное событие внутри функции
```

- Всегда указывайте `event` в `snake.case`-нотации вида `<домен>.<действие>` —
  так удобнее фильтровать логи по модулям системы.
- Декоратор сам ловит и логирует исключения — **не** оборачивайте тело функции
  в свой `try/except` только ради логирования ошибки, если не нужно её
  обрабатывать (декоратор уже пробрасывает её дальше).
- Не кладите пароли/токены в аргументы функции без необходимости — но даже
  если положите, `sanitize()` в `decorators.py` их замаскирует при падении.
- Используйте `{}`-плейсхолдеры (loguru), а не `%s` (обычный `logging`).
