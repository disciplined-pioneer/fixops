"""
demo.py — сквозной прогон системы FixOps Code Intelligence на примере
из спеки: PaymentService.pay -> DiscountService.calculate -> PromoRepository.get,
где неизвестный промокод приводит к 'NoneType' object has no attribute 'discount'.

Запуск:  python demo.py
"""

import json
import os
import sys
import traceback

ROOT = os.path.dirname(os.path.abspath(__file__))
EXAMPLES = os.path.join(ROOT, "examples")

sys.path.insert(0, ROOT)
sys.path.insert(0, EXAMPLES)

from code_intel.indexer import scan_project, to_dict           # noqa: E402
from code_intel.resolver import ProjectIndex, resolve_call      # noqa: E402
from code_intel.graph import build_graph, merge_runtime_edges   # noqa: E402
from code_intel.error_analyzer import analyze_error, render_chain_text  # noqa: E402
from code_intel.tracer import RuntimeTracer                     # noqa: E402
from code_intel.context_builder import build_llm_context, render_llm_prompt  # noqa: E402


def section(title):
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


def main():
    # ---- Шаг 1-3: сканирование проекта + AST-индекс -----------------
    section("ШАГ 1-3: Сканирование проекта и AST-индексация")
    modules = scan_project(EXAMPLES)
    for m in modules:
        print(f"  {m.file:30s} module={m.module:25s} classes={[c.name for c in m.classes]}")

    idx = ProjectIndex(modules)

    with open(os.path.join(ROOT, "logs", "index.json"), "w", encoding="utf-8") as f:
        json.dump(to_dict(modules), f, ensure_ascii=False, indent=2)
    print("\n  Полный индекс сохранён в logs/index.json")

    # ---- Шаг 4: строим граф вызовов (статика) ------------------------
    section("ШАГ 4: Статический граф вызовов")
    g = build_graph(idx, resolve_call)
    for e in g.edges:
        status = "OK" if e.resolved else "??"
        print(f"  [{status}] {e.source}  --({e.file}:{e.line})-->  {e.target}   ({e.reason})")

    # ---- Реальный запуск с ошибкой + runtime-трейс (шаг 7.3) ---------
    section("Runtime: воспроизводим реальный вызов и трейсим его")

    from examples.api.payment import PaymentService  # импортируем "боевой" код примера

    class Order:
        def __init__(self, promo):
            self.promo = promo

    tracer = RuntimeTracer(project_root=EXAMPLES, log_path=os.path.join(ROOT, "logs", "runtime_trace.jsonl"))

    error_log = None
    with tracer.trace(trace_id="req-abc123") as trace_id:
        try:
            PaymentService().pay(Order(promo="DOES_NOT_EXIST"))
        except AttributeError as e:
            tb = traceback.extract_tb(sys.exc_info()[2])
            last = tb[-1]
            error_log = {
                "trace_id": trace_id,
                "file": os.path.relpath(last.filename, EXAMPLES).replace(os.sep, "/"),
                "line": last.lineno,
                "function": last.name,
                "error": f"{type(e).__name__}: {e}",
            }
            print(f"  Поймана реальная ошибка: {error_log}")

    print(f"\n  Записано {len(tracer.events_for(trace_id))} runtime-событий вызовов, trace_id={trace_id}")
    for ev in tracer.events_for(trace_id):
        print(f"    {ev['caller']} -> {ev['callee']}  ({ev['file']}:{ev['line']})")

    # переводим runtime qualname (services.discount.DiscountService.calculate)
    # из системы координат EXAMPLES в те же qualname, что использует статический граф
    merge_runtime_edges(g, tracer.events_for(trace_id))

    # ---- Шаг 5-6: приходит ошибка -> строим цепочку по графу ---------
    section("ШАГ 5-6: Ошибка из лога -> цепочка причинности по графу")
    print("  Входной лог ошибки (как из production):")
    print(" ", json.dumps(error_log, ensure_ascii=False, indent=2))

    result = analyze_error(idx, g, error_log)

    print()
    print(render_chain_text(result))

    with open(os.path.join(ROOT, "logs", "last_error_analysis.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    # ---- Экспорт графа для визуализации -------------------------------
    with open(os.path.join(ROOT, "logs", "graph.json"), "w", encoding="utf-8") as f:
        json.dump(g.to_dict(), f, ensure_ascii=False, indent=2)
    print("\nГраф экспортирован в logs/graph.json для визуализации.")

    # ---- Шаг 7: сборка финального контекста для LLM (реальный код, не координаты) ----
    section("ШАГ 7: Финальный промпт для LLM (с реальным исходным кодом)")
    ctx = build_llm_context(idx, EXAMPLES, result)
    prompt = render_llm_prompt(ctx)
    print(prompt)

    with open(os.path.join(ROOT, "logs", "llm_prompt.md"), "w", encoding="utf-8") as f:
        f.write(prompt)
    print("\nПромпт для LLM сохранён в logs/llm_prompt.md")


if __name__ == "__main__":
    main()
