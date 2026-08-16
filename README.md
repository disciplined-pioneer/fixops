# FixOps Code Intelligence — прототип

Рабочая реализация слоя, описанного в задаче: AST-индексация → резолвинг
вызовов → граф зависимостей → runtime-трассировка → сборка цепочки
причинности по ошибке из лога.

## Структура

```
code_intel/
  indexer.py           # Шаг 1-3: сканирование проекта + AST-разбор файлов
  resolver.py          # Резолвинг вызовов (импорты, self, локальные типы)
  graph.py              # Шаг 4: граф вызовов + слияние с runtime-рёбрами
  tracer.py             # Runtime-трассировка (sys.settrace) с trace_id
  error_analyzer.py     # Шаг 5-6: лог ошибки -> цепочка callers/callees + root cause (координаты)
  context_builder.py    # Шаг 7: координаты -> РЕАЛЬНЫЙ КОД + финальный промпт для LLM
examples/                # Пример проекта из задачи (payment -> discount -> promo)
demo.py                  # Сквозной прогон всего пайплайна, включая генерацию промпта
graph_view.html           # Интерактивная визуализация графа с подсветкой цепочки ошибки
logs/
  index.json              # AST-индекс всего проекта
  graph.json               # Граф вызовов (для визуализации)
  runtime_trace.jsonl       # Реальные вызовы, записанные трейсером
  last_error_analysis.json  # Координаты цепочки (промежуточный результат)
  llm_prompt.md             # ИТОГОВЫЙ текст, который реально уходит в LLM
```

**Важно про слои:** `error_analyzer.py` даёт только координаты (qualname, file:line,
resolved/unresolved) — этого недостаточно, чтобы LLM что-то починила. `context_builder.py`
дотягивает до каждой координаты реальный исходный код функции и собирает готовый markdown-промпт
с меткой доверия на каждом узле (`runtime-confirmed` / `static-resolved` / `static-unresolved`).
Финальное решение "где чинить" всегда принимает LLM, читая код — граф лишь сужает область
поиска с "всего проекта" до 4-6 функций, реально стоящих в цепочке вызовов.

## Запуск

```bash
python3 demo.py
```

Скрипт:
1. Индексирует `examples/` через AST (без выполнения кода).
2. Строит статический граф вызовов, резолвя `self.x()`, `Class.method()`,
   `var = Class(); var.method()` и вызовы конструкторов.
3. Реально выполняет `PaymentService().pay(...)` с несуществующим промокодом,
   ловит настоящий `AttributeError`, параллельно записывая runtime-трассировку
   вызовов с `trace_id`.
4. Дополняет статический граф рёбрами из трассировки (там, где статика не
   смогла однозначно резолвить вызов — например `DiscountService().calculate()`,
   вызов метода на результате цепочки конструктор+метод).
5. Берёт "плоскую" ошибку в формате из лога:
   ```json
   {"file": "services/discount.py", "line": 9, "function": "calculate",
    "error": "AttributeError: 'NoneType' object has no attribute 'discount'"}
   ```
   и строит полную цепочку: кто вызвал → сама функция → что она вызвала →
   кандидаты на первопричину (самые глубокие листья графа вызовов).

Результат печатается в консоль и сохраняется в `logs/last_error_analysis.json`
и `logs/graph.json`.

## Визуализация

`graph_view.html` — самодостаточный файл (данные уже встроены), можно открыть
в браузере. Показывает граф вызовов, подсвечивает цепочку от точки входа
до узла ошибки и её дальнейшие вызовы, различает рёбра по происхождению:
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

Runtime-трассировку подключайте в staging/тестах (не в проде — `sys.settrace`
даёт заметный оверхед), либо замените `tracer.py` на APM-трейсинг
(OpenTelemetry spans), сопоставляя `trace_id` из логов с реальными вызовами —
принцип тот же.

## Известные ограничения (то, что описано в п.6 задачи)

- Динамический полиморфизм (`service.run()`, где `service` — параметр функции
  без известного типа) резолвится не всегда: резолвер использует эвристики
  импортов и локальных присваиваний `x = ClassName()`, но не полноценный
  тайпчекер. Для точного резолвинга — интеграция с `mypy --output json`/
  `pyright --outputjson` даст типы параметров и полей.
- Цепные вызовы вида `ClassName().method()` статически распадаются на два
  отдельных вызова (конструктор + метод без известного базового объекта) —
  именно поэтому в демо нужен runtime-трейс, чтобы получить точное ребро
  `PaymentService.pay -> DiscountService.calculate`.
