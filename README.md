# FixOps Code Intelligence — прототип

Рабочая реализация слоя, описанного в задаче: AST-индексация → резолвинг
вызовов → граф зависимостей → runtime-трассировка → сборка цепочки
причинности по ошибке из лога.

## Структура

```
code_intel/
  indexer.py           # Шаг 1-3: сканирование проекта + AST-разбор файлов
  resolver.py          # Резолвинг вызовов (импорты, self, локальные типы)
  graph.py             # Шаг 4: граф вызовов + слияние с runtime-рёбрами
  tracer.py            # Runtime-трассировка (sys.settrace) с trace_id
  error_analyzer.py    # Шаг 5-6: лог ошибки -> цепочка callers/callees + root cause (координаты)
  context_builder.py   # Шаг 7: координаты -> РЕАЛЬНЫЙ КОД + финальный промпт для LLM
  html_view.py         # Визуализация графа (граф + подсветка цепочки ошибки)
  graph_view.template.html

sample_app/            # Исследуемый проект (модель реального сервиса)
  core/                # Инфраструктура логирования ПРОЕКТА
    logging.py         #   настроенный loguru: app_logger / get_logger(event, **ctx)
    decorators.py      #   @log_execution — логирует старт/успех/ошибку функции
    middleware.py      #   FastAPI-мидлваря с request_id (contextvars)
  api/checkout.py      #   CheckoutService.checkout
  services/pricing.py  #   PricingService.calculate_total
  repositories/inventory.py  # InventoryRepository.get (место бага)
  models/product.py    #   Product
  reproduce.py         #   воспроизводит баг: checkout(SKU-999) -> ошибка
  logs/app.log         #   runtime-лог проекта (JSON, loguru) — источник ошибки

analyze_error.py       # Точка входа анализа: read лог -> граф -> промпт LLM
logs/                  # Артефакты анализа (index/graph/analysis/prompt/view)
```

**Важно про слои:** `error_analyzer.py` даёт только координаты (qualname, file:line,
resolved/unresolved) — этого недостаточно, чтобы LLM что-то починила. `context_builder.py`
дотягивает до каждой координаты реальный исходный код функции и собирает готовый markdown-промпт
с меткой доверия на каждом узле (`runtime-confirmed` / `static-resolved` / `static-unresolved`).
Финальное решение "где чинить" всегда принимает LLM, читая код — граф лишь сужает область
поиска с "всего проекта" до 4-6 функций, реально стоящих в цепочке вызовов.

## Логирование (sample_app/core)

Проект логирует через собственный `core` (loguru):

- `get_logger(event, **context)` — логгер, привязанный к событию: добавляет
  `event_id`, `service`, `environment` и произвольный контекст.
- `@log_execution(event=...)` — декоратор для методов: на старте пишет DEBUG,
  при успехе INFO (со `status`, `duration_ms`), при падении — ERROR с
  traceback, санитайзнутыми аргументами и типом/сообщением исключения
  (чувствительные ключи `password`, `token` и т.п. маскируются).
- Файловый sink — `sample_app/logs/app.log` (JSON), консольный — зависит от
  `APP_ENV`: `production` = JSON в stdout, иначе цветной лог для разработчика.

## Запуск

```bash
python sample_app/reproduce.py     # 1. воспроизвести баг, пишет логи в sample_app/logs/app.log
python analyze_error.py            # 2. прочитать ошибку из лога и построить анализ
```

Скрипты:
1. `reproduce.py` реально запускает `CheckoutService().checkout(...)` с неизвестным SKU,
   ловит настоящий `AttributeError` и логирует его через `core.logging`:
   декораторы `@log_execution` пишут всю цепочку `checkout.create ->
   pricing.calculate_total -> inventory.get`, а финальная ERROR-запись
   (`Checkout failed`) содержит координаты ошибки: `file`, `line`, `function`, `error`.
2. `analyze_error.py` читает **хвост** `sample_app/logs/app.log` (последние
   `LOG_TAIL_LINES = 50` строк, без загрузки файла целиком) и из последней
   ERROR-записи с координатами достаёт ошибку в формате
   `{"file": "services/pricing.py", "line": 12, "function": "calculate_total",
    "error": "AttributeError: 'NoneType' object has no attribute 'price'"}`.
3. Затем индексирует проект (AST, без выполнения кода), строит статический граф
   вызовов и раскладывает ошибку на цепочку: кто вызвал → сама функция → что она
   вызвала → кандидаты на первопричину (самые глубокие листья графа вызовов).

Итог печатается в консоль и сохраняется в `logs/last_error_analysis.json`,
`logs/index.json`, `logs/graph.json` и `logs/llm_prompt.md`.

## Визуализация

`logs/graph_view.html` — самодостаточный файл, можно открыть в браузере.
Показывает граф вызовов, подсвечивает цепочку от точки входа до узла ошибки
и её дальнейшие вызовы, различает рёбра по происхождению:
статика / не резолвлено / подтверждено runtime-трейсом.

## Как использовать на своём проекте

```python
from code_intel.indexer import scan_project
from code_intel.resolver import ProjectIndex, resolve_call
from code_intel.graph import build_graph
from code_intel.error_analyzer import analyze_error, render_chain_text

modules = scan_project("/path/to/project")
idx = ProjectIndex(modules)
g = build_graph(idx, resolve_call)

error_log = {"file": "services/discount.py", "line": 45,
             "function": "calculate_discount",
             "error": "..."}

result = analyze_error(idx, g, error_log)
print(render_chain_text(result))
```

Если у проекта есть логирование (loguru, JSON-строки) — ошибку можно брать прямо
из лог-файла, как в `analyze_error.py` (`ErrorLoader`): последние `LOG_TAIL_LINES`
строк, последняя ERROR-запись с координатами.

Runtime-трассировку подключайте в staging/тестах (не в проде — `sys.settrace`
даёт заметный оверхед), либо замените `tracer.py` на APM-трейсинг
(OpenTelemetry spans), сопоставляя `trace_id` из логов с реальными вызовами —
принцип тот же.

## Известные ограничения

- Динамический полиморфизм (`service.run()`, где `service` — параметр функции
  без известного типа) резолвится не всегда: резолвер использует эвристики
  импортов и локальных присваиваний `x = ClassName()`, но не полноценный
  тайпчекер. Для точного резолвинга — интеграция с `mypy --output json`/
  `pyright --outputjson` даст типы параметров и полей.
- Цепные вызовы вида `ClassName().method()` статически распадаются на два
  отдельных вызова (конструктор + метод без известного базового объекта) —
  именно поэтому в демо нужен runtime-трейс, чтобы получить точное ребро
  `CheckoutService.pay -> DiscountService.calculate`.
- Читаются только последние `LOG_TAIL_LINES = 50` строк лога: если между
  ошибкой и концом лога много записей (например, длинный retry-цикл после
  падения), поднимите эту константу в `analyze_error.py`.