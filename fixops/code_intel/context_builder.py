"""
context_builder.py — последний слой перед LLM.

Стратегия точечного поиска:
  - source: форматированный код с номерами строк и комментариями
    для визуального понимания структуры (ИИ читает его глазами)
  - raw_source: ТОЧНЫЙ код из файла — импорты + docstring класса +
    декораторы + целевая функция (без комментариев, без других методов класса).
    Это то, что ИИ копирует в SEARCH-блок.

Ключевое: raw_source включает ВСЁ от первого импорта до конца целевой функции,
включая docstring класса и все декораторы. Это гарантирует ТОЧНОЕ совпадение
SEARCH-блока с реальным файлом (100% вместо 93%).
"""

import os
import ast
import asyncio


class ContextBuilder:
    """Собирает реальный исходный код узлов цепочки в контекст для LLM."""

    def __init__(self, project_root: str):
        self.project_root = project_root

    def _find_function_location(self, idx, qualname: str):
        """По qualname находит (file, lineno, end_lineno) в индексе."""
        for m in idx.modules:
            for fn in m.functions:
                if fn.qualname == qualname:
                    return m.file, fn.lineno, fn.end_lineno
        return None

    async def _read_smart_context(
        self, file: str, target_lineno: int, target_end_lineno: int
    ) -> tuple[str, str]:
        """
        Точечный поиск: импорты + docstring класса + декораторы + функция.

        Возвращает (source, raw_source):
          - source: для чтения (с номерами и комментариями)
          - raw_source: ТОЧНЫЙ код из файла — для копирования в SEARCH

        КРИТИЧЕСКИ ВАЖНО: raw_source включает ВСЁ от первого импорта до конца
        целевой функции, включая docstring класса. Это гарантирует точное
        совпадение SEARCH-блока с реальным файлом.
        """
        path = os.path.join(self.project_root, file)

        def _extract():
            with open(path, "r", encoding="utf-8") as f:
                lines = f.readlines()

            # 1. Находим все импорты (до целевой функции)
            import_indices_0based = []
            for i in range(min(target_lineno - 1, len(lines))):
                stripped = lines[i].strip()
                if stripped.startswith(("import ", "from ")):
                    import_indices_0based.append(i)

            # 2. Находим класс через AST
            class_node = None
            try:
                tree = ast.parse("".join(lines))
                for node in ast.walk(tree):
                    if isinstance(node, ast.ClassDef):
                        if (node.lineno <= target_lineno
                                and node.end_lineno >= target_end_lineno):
                            class_node = node
                            break
            except SyntaxError:
                pass

            # 3. Определяем строки для raw_source
            func_start_0based = target_lineno - 1
            func_end_0based = target_end_lineno  # exclusive

            # 4. Строим raw_source: ТОЧНЫЙ код из файла
            #    Порядок: импорты → (пустые строки) → class def + docstring → декораторы → функция
            raw_lines = []

            # Импорты — точные строки
            for idx in import_indices_0based:
                raw_lines.append(lines[idx])

            # Пустые строки между последним импортом и классом/функцией
            if import_indices_0based:
                last_import = import_indices_0based[-1]
                next_meaningful = (
                    class_node.lineno - 1 if class_node else func_start_0based
                )
                for idx in range(last_import + 1, next_meaningful):
                    raw_lines.append(lines[idx])

            # КРИТИЧЕСКОЕ ИСПРАВЛЕНИЕ: включаем ВСЁ от class def до целевой функции
            # Это включает docstring класса, пустые строки, декораторы
            if class_node is not None:
                class_def_idx = class_node.lineno - 1
                # Берём ВСЕ строки от class def до начала целевой функции
                for idx in range(class_def_idx, func_start_0based):
                    raw_lines.append(lines[idx])

            # Целевая функция — точные строки
            for idx in range(func_start_0based, func_end_0based):
                raw_lines.append(lines[idx])

            raw_source = "".join(raw_lines).rstrip()

            # 5. Строим source: форматированный для чтения
            numbered = []

            if import_indices_0based:
                numbered.append("# --- импорты файла ---")
                for idx in import_indices_0based:
                    numbered.append(f"{idx + 1:>4} | {lines[idx].rstrip()}")
                numbered.append("")

            if class_node is not None:
                numbered.append(
                    f"# --- класс {class_node.name} "
                    f"(целевой метод внутри) ---"
                )
                class_def_idx = class_node.lineno - 1
                # Показываем class def + docstring
                for idx in range(class_def_idx, func_start_0based):
                    if lines[idx].strip().startswith("@"):
                        numbered.append(f"{idx + 1:>4} | {lines[idx].rstrip()}")
                    elif lines[idx].strip().startswith("def "):
                        numbered.append(f"{idx + 1:>4} | {lines[idx].rstrip()}")
                    elif lines[idx].strip().startswith('"""') or lines[idx].strip().startswith("'''"):
                        numbered.append(f"{idx + 1:>4} | {lines[idx].rstrip()}")
                    elif lines[idx].strip() == "":
                        numbered.append(f"{idx + 1:>4} |")
                    else:
                        numbered.append(f"{idx + 1:>4} | {lines[idx].rstrip()}")

                # Показываем сигнатуры других методов (без тела)
                for item in class_node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        item_lineno = item.lineno
                        if (item_lineno >= target_lineno
                                and item_lineno <= target_end_lineno):
                            continue
                        for dec in item.decorator_list:
                            dec_idx = dec.lineno - 1
                            numbered.append(
                                f"{dec_idx + 1:>4} | {lines[dec_idx].rstrip()}"
                            )
                        sig_idx = item_lineno - 1
                        numbered.append(
                            f"{sig_idx + 1:>4} | {lines[sig_idx].rstrip()}"
                        )
                        numbered.append("     |     ...")

                numbered.append("")

            # Целевая функция
            if class_node is None:
                numbered.append("# --- функция ---")
            for idx in range(func_start_0based, func_end_0based):
                numbered.append(f"{idx + 1:>4} | {lines[idx].rstrip()}")

            source = "\n".join(numbered)

            return source, raw_source

        return await asyncio.to_thread(_extract)

    def _collect_chain_qualnames(self, analysis: dict) -> list[str]:
        """Разворачивает callers_chain/callees_chain в плоский список."""
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

        seen = set()
        result = []
        for q in ordered:
            if q not in seen:
                seen.add(q)
                result.append(q)
        return result

    def _confidence_tag(self, qualname: str, analysis: dict) -> str:
        """Помечает уверенность источника."""
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
                    "note": "нет исходника в индексе "
                            "(внешний код / динамический объект / точка входа)",
                })
                continue
            file, lineno, end_lineno = loc

            source, raw_source = await self._read_smart_context(
                file, lineno, end_lineno
            )

            nodes_with_source.append({
                "qualname": q,
                "file": file,
                "lineno": lineno,
                "end_lineno": end_lineno,
                "source": source,
                "raw_source": raw_source,
                "confidence": confidence,
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
        lines.append(
            f"Ошибка (из production-лога, trace_id={e.get('trace_id', '—')}): "
            f"{e['file']}:{e['line']} в функции {e['function']}()"
        )
        lines.append(f"```\n{e['error']}\n```\n")

        lines.append(
            "Цепочка вызовов (от входной точки до места ошибки и дальше), "
            "с реальным кодом каждой функции:\n"
        )

        for node in ctx["chain"]:
            marker = (
                " ⬅ ЗДЕСЬ ПРОИЗОШЛА ОШИБКА"
                if node.get("is_error_location")
                else ""
            )

            lines.append(f"### `{node['qualname']}`{marker}")
            lines.append(
                f"уверенность источника: **{node['confidence']}**"
            )

            if node["source"] is None:
                lines.append(f"_{node['note']}_\n")
                continue

            lines.append(
                f"файл: `{node['file']}` "
                f"(строки {node['lineno']}–{node['end_lineno']})"
            )

            # Показываем source с комментариями для понимания структуры
            lines.append("```python")
            lines.append(node["source"])
            lines.append("```\n")

            # ВАЖНО: raw_source используется для точного SEARCH
            lines.append(
                "**ТОЧНЫЙ код для SEARCH-блока** "
                "(копируй отсюда символ в символ, "
                "включая docstring класса и все декораторы):"
            )
            lines.append("```python")
            lines.append(node["raw_source"])
            lines.append("```\n")

        lines.append("Эвристические кандидаты на первопричину:")
        for c in ctx["root_cause_candidates"]:
            lines.append(f"- `{c}`")

        # ============================================================
        # ИНСТРУКЦИИ ДЛЯ LLM
        # ============================================================

        lines.append("\n" + "=" * 50)
        lines.append("СТРОГИЕ ТРЕБОВАНИЯ К ОТВЕТУ:")

        lines.append("1. Найди первопричину (Root Cause) и исправь её.")

        lines.append(
            "2. Верни СТРОГО ДВА блока кода (`fix` и `test`). "
            "Никакого текста вне этих блоков."
        )

        lines.append(
            "3. В unit-тесте тестируй зафикшенный метод НАПРЯМУЮ. "
            "Для мокинга используй ТОЛЬКО `monkeypatch`."
        )

        lines.append(
            "4. Тест должен проверять ИМЕННО поведение, которое "
            "исправляет patch, а не просто увеличивать code coverage."
        )

        lines.append(
            "5. Тест ОБЯЗАН проходить на исправленной реализации "
            "и падать на исходной ошибочной реализации."
        )

        lines.append(
            "6. Перед написанием теста определи контракт поведения: "
            "что должно происходить при нормальном сценарии и что "
            "должно происходить при ошибочном/граничном сценарии."
        )

        lines.append("7. ПРАВИЛА MONKEYPATCH (КРИТИЧЕСКИ ВАЖНО):")
        lines.append("")
        lines.append("   При мокинге МЕТОДОВ класса (def method(self, ...)):")
        lines.append("   ✅ ПРАВИЛЬНО:")
        lines.append("      def mock_get(self, sku):  # ← self ОБЯЗАТЕЛЕН!")
        lines.append("          return DummyProduct(price=10.0)")
        lines.append("      monkeypatch.setattr(InventoryRepository, 'get', mock_get)")
        lines.append("")
        lines.append("   ❌ НЕПРАВИЛЬНО (вызывает TypeError):")
        lines.append("      def mock_get(sku):  # ← забыл self!")
        lines.append("          return DummyProduct(price=10.0)")
        lines.append("      monkeypatch.setattr(InventoryRepository, 'get', mock_get)")
        lines.append("")
        lines.append("   Правило: если оригинальный метод имеет сигнатуру `def method(self, arg1, arg2)`,")
        lines.append("   то мок-функция ДОЛЖНА иметь сигнатуру `def mock_method(self, arg1, arg2)`.")

        # ============================================================
        # FIX
        # ============================================================

        lines.append("")
        lines.append("8. Внутри блока `fix` первой строкой укажи `FILE: <путь>`.")

        lines.append("")
        lines.append("9. КРИТИЧЕСКОЕ ПРАВИЛО ДЛЯ SEARCH-БЛОКА:")
        lines.append(
            "   SEARCH-блок должен начинаться с ПЕРВОГО ИМПОРТА файла "
            "и заканчиваться КОНЦОМ целевой функции."
        )
        lines.append(
            "   Копируй SEARCH ТОЛЬКО из секции '**ТОЧНЫЙ код для "
            "SEARCH-блока**' выше — символ в символ, ВКЛЮЧАЯ docstring класса."
        )
        lines.append(
            "   НЕ включай в SEARCH комментарии вроде "
            "'# --- импорты файла ---' — их НЕТ в реальном файле."
        )
        lines.append(
            "   Если нужно добавить новый импорт, добавь его в REPLACE "
            "в начало блока импортов — НЕ создавай отдельный fix-блок."
        )

        # ============================================================
        # EXCEPTIONS - УСИЛЕННЫЕ ПРАВИЛА
        # ============================================================

        lines.append("")
        lines.append("10. БИЗНЕС-ЛОГИКА И ОБРАБОТКА ИСКЛЮЧЕНИЙ (КРИТИЧЕСКИ ВАЖНО):")
        lines.append("")
        lines.append("   ПРАВИЛО ДЛЯ ЦИКЛОВ (for item in items):")
        lines.append("   - Если функция обрабатывает коллекцию и элемент не найден:")
        lines.append("     → ТЫ ОБЯЗАН залоггировать пропуск И продолжить цикл.")
        lines.append("     → `continue` БЕЗ логирования — ЗАПРЕЩЁН.")
        lines.append("     → `raise` внутри цикла — ЗАПРЕЩЁН (прервёт всю операцию).")
        lines.append("")
        lines.append("   ✅ ЕДИНСТВЕННЫЙ ПРАВИЛЬНЫЙ ПАТТЕРН:")
        lines.append("     for item in items:")
        lines.append("         product = repo.get(item['sku'])")
        lines.append("         if product is None:")
        lines.append("             logger.warning(f'Product {item[\"sku\"]} not found, skipping')")
        lines.append("             continue")
        lines.append("         total += product.price * item['qty']")
        lines.append("     return total")
        lines.append("")
        lines.append("   ❌ ЗАПРЕЩЁННЫЕ ПАТТЕРНЫ:")
        lines.append("     if product is None:")
        lines.append("         continue  # ← ЗАПРЕЩЕНО! Нет логирования!")
        lines.append("")
        lines.append("     if product is None:")
        lines.append("         raise ValueError(...)  # ← ЗАПРЕЩЕНО в цикле!")
        lines.append("")
        lines.append("   КАК ДОБАВИТЬ ЛОГГИРОВАНИЕ:")
        lines.append("   - Проверь цепочку вызовов выше. Если в проекте уже есть логгер")
        lines.append("     (например, `get_logger` из `core.logging`), используй его.")
        lines.append("   - Добавь импорт логгера в REPLACE-блок вместе с остальными импортами.")
        lines.append("   - Пример: если в SEARCH импорты такие:")
        lines.append("       from typing import Any")
        lines.append("       from repositories.inventory import InventoryRepository")
        lines.append("     То в REPLACE добавь импорт логгера:")
        lines.append("       from typing import Any")
        lines.append("       from repositories.inventory import InventoryRepository")
        lines.append("       from core.logging import get_logger  # ← добавлено")
        lines.append("")
        lines.append("   ПРАВИЛО ДЛЯ ЕДИНИЧНЫХ ОПЕРАЦИЙ (get_user, fetch_order):")
        lines.append("   - Если функция возвращает ОДИН объект и он не найден:")
        lines.append("     → Используй fail-fast с явным исключением (raise ValueError/KeyError).")
        lines.append("     → Тест должен проверять это через `with pytest.raises(...)`.")
        lines.append("")
        lines.append("   ОБЩИЕ ЗАПРЕТЫ:")
        lines.append("   - НИКОГДА не вставляй `import` или `logger = ...` внутрь класса или функции.")
        lines.append("   - НИКОГДА не создавай дублированный return statement.")
        lines.append("   - НИКОГДА не делай `continue` без логирования в цикле.")

        # ============================================================
        # MONKEYPATCH
        # ============================================================

        lines.append("")
        lines.append("11. ПРАВИЛА MONKEYPATCH:")
        lines.append("   ✅ ПРАВИЛЬНО:")
        lines.append("      monkeypatch.setattr(ClassName, 'method_name', mock_func)")
        lines.append("   ❌ НЕПРАВИЛЬНО (вызывает AttributeError):")
        lines.append("      monkeypatch.setattr('module.ClassName', 'method', mock)")
        lines.append("   Всегда передавай САМ КЛАСС, не строку с его именем.")

        lines.append("")
        lines.append("12. ПРАВИЛА ПАТЧА:")
        lines.append("   ⚠️ ПАТЧ ДОЛЖЕН БЫТЬ МАКСИМАЛЬНО МАЛЕНЬКИМ.")
        lines.append("   Для изменения одной строки SEARCH должен содержать одну строку или минимально необходимый контекст.")
        lines.append("   Например, если нужно изменить только условие цикла, НЕ включай всю функцию.")
        lines.append("   ❌ НЕПРАВИЛЬНО:")
        lines.append("      SEARCH содержит всю функцию из 15-20 строк.")
        lines.append("   ✅ ПРАВИЛЬНО:")
        lines.append("      SEARCH содержит только строку, которую нужно изменить.")
        lines.append("   SEARCH обязан дословно существовать в текущем файле.")

        # ============================================================
        # ФОРМАТ ОТВЕТА
        # ============================================================

        lines.append("\n```fix")
        lines.append("FILE: <относительный_путь_к_файлу>")
        lines.append("<<<<<<< SEARCH")
        lines.append(
            "<ТОЧНЫЙ код из секции '**ТОЧНЫЙ код для SEARCH-блока**' — "
            "начинается с импортов, включает docstring класса, заканчивается функцией>"
        )
        lines.append("=======")
        lines.append(
            "<исправленный код: новые импорты + импорты + класс с docstring + "
            "исправленная функция. ТОЛЬКО ОДИН return statement в конце функции.>"
        )
        lines.append(">>>>>>> REPLACE")
        lines.append("```\n")

        lines.append("```test")
        lines.append(
            "FILE: <путь_к_файлу_теста, например tests/test_pricing.py>"
        )
        lines.append("import pytest")
        lines.append("...")
        lines.append(
            "<полный regression-тест на pytest с использованием "
            "monkeypatch, который проверяет исправленное поведение>"
        )
        lines.append("```")

        return "\n".join(lines)


async def build_llm_context(idx, project_root: str, analysis: dict) -> dict:
    """Обратно-совместимая обёртка."""
    return await ContextBuilder(project_root).build_llm_context(idx, analysis)


def render_llm_prompt(ctx: dict) -> str:
    """Обратно-совместимая обёртка."""
    return ContextBuilder.render_llm_prompt(ctx)
