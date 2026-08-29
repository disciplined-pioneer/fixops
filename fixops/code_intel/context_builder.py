"""
context_builder.py — последний слой перед LLM.

Всё, что было раньше (indexer/resolver/graph/tracer/error_analyzer),
даёт КООРДИНАТЫ: qualname, file:line, resolved/unresolved.
Но LLM не чинит координаты — она чинит текст кода. Значит, для каждого
узла цепочки нужно достать РЕАЛЬНЫЙ исходный код функции, а не только
её имя.

Этот модуль:
  1. Обходит цепочку из error_analyzer.analyze_error()
  2. Для каждого узла достаёт настоящий код функции (по file + lineno/end_lineno)
  3. Помечает, насколько мы уверены в этом узле (resolved / unresolved / runtime)
  4. Собирает всё в единый текстовый контекст — то, что реально уйдёт в промпт LLM

Важно: этот модуль НЕ решает, где баг. Он решает, что именно LLM
физически увидит. Решение "здесь нужно чинить" делает сама модель,
читая код — граф лишь резко сужает, какие 4-6 функций ей вообще
показывать, вместо всего проекта.

Логика собрана в класс `ContextBuilder`: экземпляр привязан к корню
проекта (project_root), из которого читаются файлы исходников. Для
обратной совместимости сохранены модульные функции-обёртки
`build_llm_context` и `render_llm_prompt`.
"""

import os
import asyncio


class ContextBuilder:
    """Собирает реальный исходный код узлов цепочки в контекст для LLM."""

    def __init__(self, project_root: str):
        self.project_root = project_root

    def _find_function_location(self, idx, qualname: str):
        """По qualname находит (file, lineno, end_lineno) в индексе.
        Для узлов вроде '<entrypoint>' или нерезолвленных вызовов ('_PROMO_DB.get')
        вернёт None — для них исходника у нас просто нет."""
        for m in idx.modules:
            for fn in m.functions:
                if fn.qualname == qualname:
                    return m.file, fn.lineno, fn.end_lineno
        return None

    async def _read_source_slice(self, file: str, lineno: int, end_lineno: int, raw: bool = False) -> str:
        path = os.path.join(self.project_root, file)
        
        def _read_file():
            with open(path, "r", encoding="utf-8") as f:
                return f.readlines()
        
        lines = await asyncio.to_thread(_read_file)

        # lineno в AST 1-based
        slice_lines = lines[lineno - 1:end_lineno]

        # Если нужен чистый код без номеров строк (удобно для SEARCH/REPLACE)
        if raw:
            return "".join(slice_lines).rstrip()

        numbered = []
        for i, line in enumerate(slice_lines, start=lineno):
            numbered.append(f"{i:>4} | {line.rstrip()}")
        return "\n".join(numbered)

    def _collect_chain_qualnames(self, analysis: dict) -> list[str]:
        """Разворачивает вложенные callers_chain/callees_chain в плоский
        упорядоченный список: от самого верхнего вызывающего до самого
        глубокого вызываемого, с узлом ошибки посередине."""
        ordered = []

        def walk_callers(nodes):
            for n in nodes:
                walk_callers(n.get("callers", []))
                ordered.append(n["qualname"])

        walk_callers(analysis.get("callers_chain", []))
        ordered.append(analysis["resolved_node"])

        def walk_callees(nodes):
            for n in nodes:
                ordered.append(n["qualname"])
                walk_callees(n.get("callees", []))

        walk_callees(analysis.get("callees_chain", []))

        # убираем дубликаты, сохраняя порядок
        seen = set()
        result = []
        for q in ordered:
            if q not in seen:
                seen.add(q)
                result.append(q)
        return result

    def _confidence_tag(self, qualname: str, analysis: dict) -> str:
        """Ищет, каким способом узел попал в граф (для честной пометки LLM,
        чтобы она не доверяла unresolved-догадкам так же, как runtime-фактам)."""
        def scan(nodes, key_caller="callers", key_callee="callees"):
            for n in nodes:
                if n["qualname"] == qualname:
                    if n.get("from_runtime"):
                        return "runtime-confirmed"
                    if n.get("resolved"):
                        return "static-resolved"
                    return "static-unresolved (эвристика, могла ошибиться)"
                found = scan(n.get(key_caller, []) or n.get(key_callee, []))
                if found:
                    return found
            return None

        tag = scan(analysis.get("callers_chain", []))
        if tag:
            return tag
        tag = scan(analysis.get("callees_chain", []))
        if tag:
            return tag
        if qualname == analysis["resolved_node"]:
            return "error-location (подтверждено логом)"
        return "unknown"

    async def build_llm_context(self, idx, analysis: dict) -> dict:
        chain = self._collect_chain_qualnames(analysis)

        nodes_with_source = []
        for q in chain:
            loc = self._find_function_location(idx, q)
            confidence = self._confidence_tag(q, analysis)
            if loc is None:
                nodes_with_source.append({
                    "qualname": q, "file": None, "source": None,
                    "confidence": confidence,
                    "note": "нет исходника в индексе (внешний код / динамический объект / точка входа)",
                })
                continue
            file, lineno, end_lineno = loc

            # Получаем код с номерами строк для визуальной читаемости
            source = await self._read_source_slice(file, lineno, end_lineno, raw=False)
            # И чистый код без номеров, чтобы LLM копировала точный синтаксис
            raw_source = await self._read_source_slice(file, lineno, end_lineno, raw=True)

            nodes_with_source.append({
                "qualname": q, "file": file, "lineno": lineno, "end_lineno": end_lineno,
                "source": source, "raw_source": raw_source, "confidence": confidence,
                "is_error_location": q == analysis["resolved_node"],
            })

        return {
            "error": analysis["error"],
            "chain": nodes_with_source,
            "root_cause_candidates": analysis.get("root_cause_candidates", []),
        }

    @staticmethod
    def render_llm_prompt(ctx: dict) -> str:
        """Собирает финальный текст, который уходит в LLM."""
        e = ctx["error"]
        lines = []
        lines.append("# Контекст для анализа ошибки\n")
        lines.append(f"Ошибка (из production-лога, trace_id={e.get('trace_id','—')}): {e['file']}:{e['line']} в функции {e['function']}()")
        lines.append(f"```\n{e['error']}\n```\n")

        lines.append("Цепочка вызовов (от входной точки до места ошибки и дальше), с реальным кодом каждой функции:\n")

        for node in ctx["chain"]:
            marker = " ⬅ ЗДЕСЬ ПРОИЗОШЛА ОШИБКА" if node.get("is_error_location") else ""
            lines.append(f"### `{node['qualname']}`{marker}")
            lines.append(f"уверенность источника: **{node['confidence']}**")
            if node["source"] is None:
                lines.append(f"_{node['note']}_\n")
                continue
            lines.append(f"файл: `{node['file']}` (строки {node['lineno']}–{node['end_lineno']})")
            lines.append("```python")
            lines.append(node.get("raw_source") or node["source"])
            lines.append("```\n")

        lines.append("Эвристические кандидаты на первопричину:")
        for c in ctx["root_cause_candidates"]:
            lines.append(f"- `{c}`")

        # ИНСТРУКЦИЯ ДЛЯ ВЫДАЧИ ПАТЧА И ТЕСТОВ
        lines.append("\n" + "=" * 50)
        lines.append("СТРОГИЕ ТРЕБОВАНИЯ К ОТВЕТУ:")
        lines.append("1. Найди первопричину (Root Cause) и исправь её.")
        lines.append("2. Верни СТРОГО ДВА БЛОКА КОДА (`fix` и `test`). Никакого текста вне блоков, заголовков, пояснений, слова 'FIX' или 'Фрагмент кода' быть НЕ ДОЛЖНО.")
        lines.append("3. В unit-тесте тестируй зафикшенный метод НАПРЯМУЮ. Для мокинга внешних БД и зависимостей используй ТОЛЬКО `monkeypatch` (без unittest.mock). Покрой как случай с ошибкой, так и успешный сценарий.")
        lines.append("4. Внутри блока `fix` первой строкой ОБЯЗАТЕЛЬНО укажи `FILE: <путь>`. Формат строго следующий:\n")

        lines.append("```fix")
        lines.append("FILE: <относительный_путь_к_файлу>")
        lines.append("<<<<<<< SEARCH")
        lines.append("<точный_оригинальный_код_без_номеров_строк_который_нужно_заменить>")
        lines.append("=======")
        lines.append("<новый_исправленный_код>")
        lines.append(">>>>>>> REPLACE")
        lines.append("```\n")

        lines.append("```test")
        lines.append("FILE: <относительный_путь_к_файлу_теста,_например_tests/test_promo.py>")
        lines.append("import pytest")
        lines.append("...")
        lines.append("<полный_код_теста_на_pytest_с_использованием_monkeypatch>")
        lines.append("```")
        lines.append("```")

        return "\n".join(lines)


async def build_llm_context(idx, project_root: str, analysis: dict) -> dict:
    """Обратно-совместимая обёртка над ContextBuilder.build_llm_context."""
    return await ContextBuilder(project_root).build_llm_context(idx, analysis)


def render_llm_prompt(ctx: dict) -> str:
    """Обратно-совместимая обёртка над ContextBuilder.render_llm_prompt."""
    return ContextBuilder.render_llm_prompt(ctx)
