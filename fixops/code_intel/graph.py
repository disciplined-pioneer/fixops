"""
graph.py — построение графа вызовов (call graph) поверх резолвнутого индекса.

Узел графа = qualname функции/метода (напр. "services.discount.DiscountService.calculate").
Ребро A -> B  = "A вызывает B", с метаданными: строка вызова, файл, resolved/unresolved,
                а также помечается флагом from_runtime=True, если ребро подтверждено
                трассировкой выполнения (см. tracer.py), а не только статикой.

Это именно тот граф из шага 4 ("Строим граф вызовов") и основа для
шага 5-6 (поиск цепочки при ошибке).

Построение и слияние рёбер собраны в класс `GraphBuilder`: он умеет
строить граф по индексу (используя резолвер) и дополнять его рёбрами
из runtime-трассировки. Для обратной совместимости сохранены модульные
функции-обёртки `build_graph` и `merge_runtime_edges`.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Edge:
    source: str
    target: str
    file: str
    line: int
    resolved: bool
    reason: str
    from_runtime: bool = False


class CallGraph:
    def __init__(self):
        self.edges: list[Edge] = []
        self.out_edges: dict[str, list[Edge]] = {}
        self.in_edges: dict[str, list[Edge]] = {}
        self.nodes: set[str] = set()

    def add_node(self, qualname: str):
        self.nodes.add(qualname)
        self.out_edges.setdefault(qualname, [])
        self.in_edges.setdefault(qualname, [])

    def add_edge(self, edge: Edge):
        self.add_node(edge.source)
        self.add_node(edge.target)
        self.edges.append(edge)
        self.out_edges[edge.source].append(edge)
        self.in_edges[edge.target].append(edge)

    def callers(self, qualname: str) -> list[Edge]:
        return self.in_edges.get(qualname, [])

    def callees(self, qualname: str) -> list[Edge]:
        return self.out_edges.get(qualname, [])

    def to_dict(self) -> dict:
        return {
            "nodes": sorted(self.nodes),
            "edges": [
                {
                    "source": e.source, "target": e.target, "file": e.file,
                    "line": e.line, "resolved": e.resolved, "reason": e.reason,
                    "from_runtime": e.from_runtime,
                }
                for e in self.edges
            ],
        }


class GraphBuilder:
    """Строит граф вызовов по индексy и сливает в него runtime-рёбра."""

    def __init__(self, resolver=None):
        # resolver может быть как CallResolver-объектом (интерфейс
        # resolve_call(module_obj, fn, call)), так и legacy-функцией
        # resolve_call(idx, module_obj, fn, call).
        self.resolver = resolver

    def _resolve_one(self, idx, m, fn, call_dict, resolver):
        method = getattr(resolver, "resolve_call", None)
        if method is not None:
            return method(m, fn, call_dict)
        return resolver(idx, m, fn, call_dict)

    def build(self, idx, resolver=None) -> CallGraph:
        resolver = resolver or self.resolver
        g = CallGraph()
        for m in idx.modules:
            for fn in m.functions:
                g.add_node(fn.qualname)
                for call in fn.calls:
                    call_dict = {"line": call.line, "raw": call.raw, "kind": call.kind,
                                 "base": call.base, "attr": call.attr}
                    res = self._resolve_one(idx, m, fn, call_dict, resolver)
                    g.add_edge(Edge(
                        source=fn.qualname,
                        target=res["target"],
                        file=m.file,
                        line=call.line,
                        resolved=res["resolved"],
                        reason=res["reason"],
                    ))
        return g

    def merge_runtime_edges(self, g: CallGraph, runtime_events: list[dict]) -> None:
        """Дополняет статический граф рёбрами, подтверждёнными реальным выполнением
        (шаг 7.3). Каждое событие трассировки: {"caller": qualname, "callee": qualname,
        "file": ..., "line": ..., "trace_id": ...}.

        Если такое ребро уже есть в графе (пусть даже unresolved с тем же raw-текстом),
        оно апгрейдится до resolved+from_runtime. Если ребра не было вовсе — добавляется
        новое, помеченное как обнаруженное только рантаймом."""
        for ev in runtime_events:
            caller, callee = ev["caller"], ev["callee"]
            existing = [e for e in g.out_edges.get(caller, []) if e.line == ev.get("line")]
            if existing:
                for e in existing:
                    e.target = callee
                    e.resolved = True
                    e.from_runtime = True
                    e.reason = "runtime_trace"
            else:
                g.add_edge(Edge(
                    source=caller, target=callee, file=ev.get("file", ""),
                    line=ev.get("line", 0), resolved=True, reason="runtime_trace",
                    from_runtime=True,
                ))


def build_graph(idx, resolve_call) -> CallGraph:
    """Обратно-совместимая обёртка над GraphBuilder.build (legacy-сигнатура резолвера)."""
    return GraphBuilder().build(idx, resolve_call)


def merge_runtime_edges(g: CallGraph, runtime_events: list[dict]) -> None:
    """Обратно-совместимая обёртка над GraphBuilder.merge_runtime_edges."""
    GraphBuilder().merge_runtime_edges(g, runtime_events)