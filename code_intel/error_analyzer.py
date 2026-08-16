"""
error_analyzer.py — превращает "плоскую" ошибку из лога в цепочку
причинности по графу вызовов (шаги 5-6 из спеки).

Вход:
    {"file": "services/discount.py", "line": 15, "function": "calculate",
     "error": "'NoneType' object has no attribute 'discount'"}

Выход: структура с:
    - узлом, где произошла ошибка
    - кто её вызвал (callers, рекурсивно вверх)
    - что она вызывает (callees, рекурсивно вниз)
    - human-readable цепочкой в духе примера из спеки
"""

from __future__ import annotations

from dataclasses import dataclass


def find_node_by_location(idx, file: str, function: str) -> str | None:
    """Находит qualname функции по файлу и имени функции из лога."""
    for m in idx.modules:
        if m.file == file or m.file.endswith(file):
            for fn in m.functions:
                if fn.name == function:
                    return fn.qualname
    return None


def _walk_callers(g, qualname: str, depth: int, max_depth: int, seen: set) -> list[dict]:
    if depth >= max_depth or qualname in seen:
        return []
    seen.add(qualname)
    chain = []
    for e in g.callers(qualname):
        chain.append({
            "qualname": e.source, "via_file": e.file, "via_line": e.line,
            "resolved": e.resolved, "from_runtime": e.from_runtime,
            "callers": _walk_callers(g, e.source, depth + 1, max_depth, seen),
        })
    return chain


def _walk_callees(g, qualname: str, depth: int, max_depth: int, seen: set) -> list[dict]:
    if depth >= max_depth or qualname in seen:
        return []
    seen.add(qualname)
    chain = []
    for e in g.callees(qualname):
        chain.append({
            "qualname": e.target, "file": e.file, "line": e.line,
            "resolved": e.resolved, "from_runtime": e.from_runtime,
            "callees": _walk_callees(g, e.target, depth + 1, max_depth, seen),
        })
    return chain


def analyze_error(idx, g, error_log: dict, max_depth: int = 4) -> dict:
    qualname = find_node_by_location(idx, error_log["file"], error_log["function"])
    if qualname is None:
        return {"error": error_log, "resolved_node": None,
                "message": "Не удалось сопоставить лог с узлом графа (файл/функция не найдены в индексе)."}

    callers = _walk_callers(g, qualname, 0, max_depth, set())
    callees = _walk_callees(g, qualname, 0, max_depth, set())

    return {
        "error": error_log,
        "resolved_node": qualname,
        "callers_chain": callers,   # кто привёл к вызову проблемной функции
        "callees_chain": callees,   # что проблемная функция вызвала дальше
        "root_cause_candidates": _guess_root_cause(callees),
    }


def _guess_root_cause(callees_chain: list[dict]) -> list[str]:
    """Простая эвристика: самые дальние листья в цепочке вызовов —
    первые кандидаты на "настоящий источник проблемы", т.к. именно
    их возвращаемое значение (например None) чаще всего всплывает
    выше по стеку в виде AttributeError/TypeError."""
    candidates = []

    def _leaves(node):
        if not node.get("callees"):
            candidates.append(node["qualname"])
        else:
            for c in node["callees"]:
                _leaves(c)

    for c in callees_chain:
        _leaves(c)
    return candidates


def render_chain_text(result: dict) -> str:
    """Печатает цепочку в формате, максимально близком к примеру из спеки."""
    if result.get("resolved_node") is None:
        return result["message"]

    lines = []
    lines.append(f"Ошибка: {result['error']['file']}:{result['error']['line']} "
                  f"в функции {result['error']['function']}")
    lines.append(f"  -> {result['error'].get('error', '')}")
    lines.append("")
    lines.append(f"Узел графа: {result['resolved_node']}")
    lines.append("")

    def render_callers(nodes, indent=""):
        for n in nodes:
            tag = "runtime" if n["from_runtime"] else ("static" if n["resolved"] else "unresolved")
            lines.append(f"{indent}{n['qualname']}  вызвал  ({n['via_file']}:{n['via_line']}, {tag})")
            render_callers(n["callers"], indent + "  ")

    def render_callees(nodes, indent=""):
        for n in nodes:
            tag = "runtime" if n["from_runtime"] else ("static" if n["resolved"] else "unresolved")
            lines.append(f"{indent}вызвал -> {n['qualname']}  ({n['file']}:{n['line']}, {tag})")
            render_callees(n["callees"], indent + "  ")

    lines.append("Контекст (кто вызвал):")
    if result["callers_chain"]:
        render_callers(result["callers_chain"])
    else:
        lines.append("  (входная точка — вызывающих не найдено)")

    lines.append("")
    lines.append("Контекст (что вызвала):")
    if result["callees_chain"]:
        render_callees(result["callees_chain"])
    else:
        lines.append("  (дальше по цепочке вызовов нет)")

    lines.append("")
    lines.append("Кандидаты на первопричину (самые дальние листья цепочки вызовов):")
    for c in result["root_cause_candidates"]:
        lines.append(f"  - {c}")

    return "\n".join(lines)
