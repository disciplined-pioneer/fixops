"""
context_builder.py — последний слой перед LLM.

Всё, что было раньше (indexer/resolver/graph/tracer/error_analyzer),
даёт КООРДИНАТЫ: qualname, file:line, resolved/unresolved.
Но LLM не чинит координаты — она чинит текст кода. Значит, для каждого
узла цепочки нужно достать РЕАЛЬНЫЙ исходный код функции, а не только
её имя.

Этот модуль:
  1. Обходит цепочку из error_analyzer.analyze_error()
  2. Для каждого узла достаёт настоящий код функции с контекстом (импорты + класс + функция)
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
import ast
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

    async def _read_smart_context(self, file: str, target_lineno: int, target_end_lineno: int) -> tuple[str, str]:
        """
        Точечный поиск контекста: извлекает импорты + класс (если есть) + функцию.

        Возвращает:
            (numbered_source, raw_source) - для отображения и для точного копирования
        """
        path = os.path.join(self.project_root, file)

        def _extract():
            with open(path, "r", encoding="utf-8") as f:
                lines = f.readlines()

            # 1. Извлекаем все импорты (до целевой строки)
            imports = []
            for i, line in enumerate(lines[:target_lineno], 1):
                stripped = line.strip()
                if stripped.startswith(('import ', 'from ')):
                    imports.append((i, line.rstrip()))

            # 2. Находим определение класса через AST (надёжнее, чем по строкам)
            class_info = None
            try:
                tree = ast.parse("".join(lines))
                for node in ast.walk(tree):
                    if isinstance(node, ast.ClassDef):
                        # Проверяем, находится ли целевая функция внутри этого класса
                        if node.lineno <= target_lineno and node.end_lineno >= target_end_lineno:
                            class_info = (node.lineno, node.name, node.end_lineno)
                            break
            except SyntaxError:
                # Если AST не парсится, пробуем по строкам
                for i in range(target_lineno - 1, -1, -1):
                    if lines[i].strip().startswith('class '):
                        class_info = (i + 1, lines[i].strip().split('(')[0].split(':')[0].split('class ')[-1], None)
                        break

            # 3. Собираем контекст
            result_lines = []
            raw_lines = []

            # Импорты
            if imports:
                result_lines.append("# === ИМПОРТЫ ФАЙЛА ===")
                raw_lines.extend([line for _, line in imports])
                for line_no, line in imports:
                    result_lines.append(f"{line_no:>4} | {line}")
                result_lines.append("")
                raw_lines.append("")

            # Класс (если функция — метод)
            if class_info:
                class_lineno, class_name, class_end = class_info
                result_lines.append(f"# === КЛАСС {class_name} (метод находится внутри) ===")

                # Читаем строки класса
                class_end_line = class_end if class_end else target_end_lineno
                class_lines = lines[class_lineno - 1:class_end_line]

                for i, line in enumerate(class_lines, class_lineno):
                    if i == target_lineno:
                        result_lines.append(f"     |     # ... (другие методы класса) ...")
                        raw_lines.append("    # ... (другие методы класса) ...\n")

                    if target_lineno <= i <= target_end_lineno:
                        # Это целевая функция
                        result_lines.append(f"{i:>4} | {line.rstrip()}")
                        raw_lines.append(line)
                    elif i == class_lineno or i == class_end_line - 1:
                        # Первая и последняя строка класса
                        result_lines.append(f"{i:>4} | {line.rstrip()}")
                        raw_lines.append(line)
                    elif i < target_lineno and i > class_lineno:
                        # Пропускаем другие методы, но показываем декораторы
                        if line.strip().startswith('@') or line.strip().startswith('def '):
                            result_lines.append(f"{i:>4} | {line.rstrip()}")
                            raw_lines.append(line)
                result_lines.append("")
            else:
                # Функция вне класса — просто читаем её
                func_lines = lines[target_lineno - 1:target_end_lineno]
                for i, line in enumerate(func_lines, target_lineno):
                    result_lines.append(f"{i:>4} | {line.rstrip()}")
                    raw_lines.append(line)

            return "\n".join(result_lines), "".join(raw_lines)

        return await asyncio.to_thread(_extract)

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

            # ТОЧЕЧНЫЙ ПОИСК: получаем импорты + класс + функцию
            source, raw_source = await self._read_smart_context(file, lineno, end_lineno)

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

        lines.append("5. УНИВЕРСАЛЬНЫЕ ПРАВИЛА ОБРАБОТКИ ИСКЛЮЧЕНИЙ И ТЕСТИРОВАНИЯ:")
        lines.append("   - Оценивай семантику: если отсутствие данных штатная ситуация, обеспечь graceful degradation (пропуск или дефолтное значение).")
        lines.append("   - Если добавляешь логирование (logger.warning), ты ОБЯЗАН добавить импорт в САМОМ НАЧАЛЕ файла (ДО всех классов и функций).")
        lines.append("   - ПРАВИЛЬНЫЙ пример патча с добавлением логгера:")
        lines.append("     ```fix")
        lines.append("     FILE: services/pricing.py")
        lines.append("     <<<<<<< SEARCH")
        lines.append("     from typing import Any")
        lines.append("     from repositories.inventory import InventoryRepository")
        lines.append("     ")
        lines.append("     class PricingService:")
        lines.append("     =======")
        lines.append("     import logging")
        lines.append("     from typing import Any")
        lines.append("     from repositories.inventory import InventoryRepository")
        lines.append("     ")
        lines.append("     logger = logging.getLogger(__name__)")
        lines.append("     ")
        lines.append("     class PricingService:")
        lines.append("     >>>>>>> REPLACE")
        lines.append("     ```")
        lines.append("   - НИКОГДА не вставляй `import` или `logger = ...` ВНУТРЬ класса или функции.")
        lines.append("   - Если добавление импорта усложняет патч, допустимо просто пропустить элемент (continue) без логирования.")
        lines.append("   - Если спецификация требует прерывания при критическом сбое, выбрасывай явное исключение.")
        lines.append("   - При выборе стратегии ориентируйся на контекст: цикл/batch → пропуск; единичная операция → fail-fast.")
        lines.append("")
        lines.append("6. ПРАВИЛА ИСПОЛЬЗОВАНИЯ MONKEYPATCH (СТРОГО СЛЕДУЙ ЭТОМУ СИНТАКСИСУ):")
        lines.append("   ✅ ПРАВИЛЬНО:")
        lines.append("      monkeypatch.setattr(ClassName, 'method_name', mock_function)")
        lines.append("      monkeypatch.setattr(InventoryRepository, 'get', mock_get)")
        lines.append("")
        lines.append("   ❌ НЕПРАВИЛЬНО (вызывает AttributeError):")
        lines.append("      monkeypatch.setattr(ClassName.__module__ + '.ClassName', 'method', mock)")
        lines.append("      monkeypatch.setattr('module.path.ClassName', 'method', mock)")
        lines.append("")
        lines.append("   Всегда передавай САМ КЛАСС (не строку с его именем) в monkeypatch.setattr.")

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
