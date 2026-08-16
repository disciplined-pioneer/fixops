"""
demo.py — сквозной прогон системы FixOps Code Intelligence на примере
из спеки: PaymentService.pay -> DiscountService.calculate -> PromoRepository.get,
где неизвестный промокод приводит к 'NoneType' object has no attribute 'discount'.

Запуск:  python demo.py

Логика прогона собрана в класс `DemoPipeline`: каждый шаг пайплайна —
отдельный метод, вызываемый из `run()`. Это даёт ту же последовательность
действий и тот же результат, что и процедурный вариант, но в ООП-форме.
"""

import json
import os
import sys
import traceback

ROOT = os.path.dirname(os.path.abspath(__file__))
EXAMPLES = os.path.join(ROOT, "examples")

sys.path.insert(0, ROOT)
sys.path.insert(0, EXAMPLES)

from code_intel.indexer import ProjectIndexer                   # noqa: E402
from code_intel.resolver import ProjectIndex, CallResolver      # noqa: E402
from code_intel.graph import GraphBuilder                       # noqa: E402
from code_intel.error_analyzer import ErrorAnalyzer             # noqa: E402
from code_intel.tracer import RuntimeTracer                     # noqa: E402
from code_intel.context_builder import ContextBuilder           # noqa: E402


class Order:
    def __init__(self, promo):
        self.promo = promo


class DemoPipeline:
    """Сквозной прогон всего пайплайна FixOps Code Intelligence."""

    def __init__(self, root: str, examples: str):
        self.root = root
        self.examples = examples
        self.logs = os.path.join(root, "logs")
        os.makedirs(self.logs, exist_ok=True)

    @staticmethod
    def section(title):
        print("\n" + "=" * 70)
        print(title)
        print("=" * 70)

    def step_index(self):
        """Шаг 1-3: сканирование проекта + AST-индексация."""
        self.section("ШАГ 1-3: Сканирование проекта и AST-индексация")
        indexer = ProjectIndexer()
        # core (инфраструктура логирования приложения) в граф вызовов не входит
        self.modules = indexer.scan(self.examples, ignore_dirs=ProjectIndexer.IGNORE_DIRS + ("core",))
        for m in self.modules:
            print(f"  {m.file:30s} module={m.module:25s} classes={[c.name for c in m.classes]}")

        self.idx = ProjectIndex(self.modules)

        with open(os.path.join(self.root, "logs", "index.json"), "w", encoding="utf-8") as f:
            json.dump(indexer.to_dict(self.modules), f, ensure_ascii=False, indent=2)
        print("\n  Полный индекс сохранён в logs/index.json")

    def step_build_graph(self):
        """Шаг 4: строим граф вызовов (статика)."""
        self.section("ШАГ 4: Статический граф вызовов")
        self.resolver = CallResolver(self.idx)
        self.graph_builder = GraphBuilder(self.resolver)
        self.g = self.graph_builder.build(self.idx)
        for e in self.g.edges:
            status = "OK" if e.resolved else "??"
            print(f"  [{status}] {e.source}  --({e.file}:{e.line})-->  {e.target}   ({e.reason})")

    def step_runtime_trace(self):
        """Реальный запуск с ошибкой + runtime-трейс (шаг 7.3)."""
        self.section("Runtime: воспроизводим реальный вызов и трейсим его")

        from examples.api.payment import PaymentService  # импортируем "боевой" код примера

        self.tracer = RuntimeTracer(
            project_root=self.examples,
            log_path=os.path.join(self.root, "logs", "runtime_trace.jsonl"),
            ignore_modules={"core.logging", "core.decorators"},
        )

        self.error_log = None
        with self.tracer.trace(trace_id="req-abc123") as trace_id:
            self.trace_id = trace_id
            try:
                PaymentService().pay(Order(promo="DOES_NOT_EXIST"))
            except AttributeError as e:
                tb = traceback.extract_tb(sys.exc_info()[2])
                last = tb[-1]
                self.error_log = {
                    "trace_id": trace_id,
                    "file": os.path.relpath(last.filename, self.examples).replace(os.sep, "/"),
                    "line": last.lineno,
                    "function": last.name,
                    "error": f"{type(e).__name__}: {e}",
                }
                print(f"  Поймана реальная ошибка: {self.error_log}")

        events = self.tracer.events_for(self.trace_id)
        print(f"\n  Записано {len(events)} runtime-событий вызовов, trace_id={self.trace_id}")
        for ev in events:
            print(f"    {ev['caller']} -> {ev['callee']}  ({ev['file']}:{ev['line']})")

        # переводим runtime qualname из системы координат EXAMPLES в те же
        # qualname, что использует статический граф
        self.graph_builder.merge_runtime_edges(self.g, events)

    def step_analyze_error(self):
        """Шаг 5-6: ошибка из лога -> цепочка причинности по графу."""
        self.section("ШАГ 5-6: Ошибка из лога -> цепочка причинности по графу")
        print("  Входной лог ошибки (как из production):")
        print(" ", json.dumps(self.error_log, ensure_ascii=False, indent=2))

        self.analyzer = ErrorAnalyzer(self.idx, self.g)
        self.result = self.analyzer.analyze_error(self.error_log)

        print()
        print(self.analyzer.render_chain_text(self.result))

        with open(os.path.join(self.root, "logs", "last_error_analysis.json"), "w", encoding="utf-8") as f:
            json.dump(self.result, f, ensure_ascii=False, indent=2)

        # Экспорт графа для визуализации
        with open(os.path.join(self.root, "logs", "graph.json"), "w", encoding="utf-8") as f:
            json.dump(self.g.to_dict(), f, ensure_ascii=False, indent=2)
        print("\nГраф экспортирован в logs/graph.json для визуализации.")

    def step_build_context(self):
        """Шаг 7: сборка финального контекста для LLM (реальный код, не координаты)."""
        self.section("ШАГ 7: Финальный промпт для LLM (с реальным исходным кодом)")
        self.context_builder = ContextBuilder(self.examples)
        ctx = self.context_builder.build_llm_context(self.idx, self.result)
        self.prompt = self.context_builder.render_llm_prompt(ctx)
        print(self.prompt)

        with open(os.path.join(self.root, "logs", "llm_prompt.md"), "w", encoding="utf-8") as f:
            f.write(self.prompt)
        print("\nПромпт для LLM сохранён в logs/llm_prompt.md")

    def run(self):
        self.step_index()
        self.step_build_graph()
        self.step_runtime_trace()
        self.step_analyze_error()
        self.step_build_context()


def main():
    DemoPipeline(ROOT, EXAMPLES).run()


if __name__ == "__main__":
    main()