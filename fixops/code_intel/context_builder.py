"""
context_builder.py — последний слой перед LLM.

Стратегия точечного поиска:
  - source: форматированный код с номерами строк и комментариями
    для визуального понимания структуры (ИИ читает его глазами)
  - raw_source: ТОЧНЫЙ код из файла — импорты + определение класса +
    целевая функция (без комментариев, без других методов класса).
    Это то, что ИИ копирует в SEARCH-блок.

Такой подход позволяет ИИ в одном блоке fix:
  - добавить новый импорт (например, logging)
  - изменить функцию внутри класса
  - и SEARCH будет точно совпадать с реальным файлом
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
        Точечный поиск: импорты + класс + функция.

        Возвращает (source, raw_source):
          - source: для чтения (с номерами и комментариями)
          - raw_source: ТОЧНЫЙ код из файла — для копирования в SEARCH
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
            #    Порядок: импорты → (пустые строки) → class def → функция
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
                    if lines[idx].strip() == "":
                        raw_lines.append(lines[idx])
                    else:
                        break

            # Определение класса (одна строка)
            if class_node is not None:
                class_def_idx = class_node.lineno - 1
                raw_lines.append(lines[class_def_idx])

                # Пустая строка после class def (если есть)
                if class_def_idx + 1 < func_start_0based:
                    for idx in range(class_def_idx + 1, func_start_0based):
                        if lines[idx].strip() == "":
                            raw_lines.append(lines[idx])
                        else:
                            # Это декоратор целевой функции — включаем
                            break

                # Декораторы целевой функции
                for idx in range(class_def_idx + 1, func_start_0based):
                    if lines[idx].strip().startswith("@"):
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
                numbered.append(
                    f"{class_def_idx + 1:>4} | "
                    f"{lines[class_def_idx].rstrip()}"
                )

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
                "без комментариев выше):"
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

        lines.append(
            "1. Найди первопричину (Root Cause) и исправь её."
        )

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

        lines.append(
            "7. Если исправление связано с изменением управления потоком "
            "(`raise`, `continue`, `return`, `break`, `if/else`), "
            "тест должен проверять именно изменение поведения."
        )

        lines.append(
            "8. Если ожидается graceful degradation внутри цикла или batch, "
            "тест должен содержать проблемный элемент И хотя бы один "
            "валидный элемент и проверять итоговый результат всей операции."
        )

        lines.append(
            "9. Для graceful degradation тест должен обнаруживать мутацию "
            "`continue` → `raise`: если `continue` заменить на `raise`, "
            "тест должен упасть."
        )

        lines.append(
            "10. Если правильное поведение — fail-fast с исключением, "
            "тест должен использовать `pytest.raises` и проверять "
            "ожидаемый тип исключения."
        )

        lines.append(
            "11. Для fail-fast тест должен обнаруживать мутацию "
            "`raise` → `continue`: если `raise` заменить на `continue`, "
            "тест должен упасть."
        )

        lines.append(
            "12. НЕ ожидай исключение только потому, что оно присутствует "
            "в текущем production-коде. Исключение должно быть частью "
            "ожидаемого контракта поведения."
        )

        lines.append(
            "13. Не создавай тесты только ради покрытия отдельных строк. "
            "Каждый тест должен защищать конкретное поведение."
        )

        lines.append(
            "14. После написания теста выполни мысленную mutation-проверку: "
            "представь минимальное изменение исправленного участка обратно "
            "в ошибочный вариант и проверь, что тест его обнаружит."
        )

        lines.append(
            "15. Если тест проходит и на исправленной, и на ошибочной "
            "реализации — тест недостаточно сильный и его необходимо "
            "переписать."
        )

        lines.append(
            "16. Если тест падает и на исправленной, и на ошибочной "
            "реализации — тест некорректен и его необходимо переписать."
        )

        lines.append(
            "17. Для изменения `raise` → `continue` или `continue` → `raise` "
            "особенно важно проверять не только отсутствие/наличие "
            "исключения, но и конечный результат операции."
        )

        lines.append(
            "18. Если обработка выполняется в цикле, при необходимости "
            "проверяй, что обработка последующих элементов действительно "
            "происходит после проблемного элемента."
        )

        lines.append(
            "19. Тест должен быть минимальным и детерминированным: "
            "не добавляй проверки, которые не относятся к исправлению."
        )

        lines.append(
            "20. Если правильное поведение невозможно однозначно определить "
            "из production-кода, ошибки, существующих тестов и доступного "
            "контекста, НЕ придумывай контракт. Выбери наиболее обоснованный "
            "вариант только при наличии достаточных доказательств."
        )

        lines.append("")

        # ============================================================
        # FIX
        # ============================================================

        lines.append(
            "21. Внутри блока `fix` первой строкой укажи "
            "`FILE: <путь>`."
        )

        lines.append("")

        lines.append("22. КРИТИЧЕСКОЕ ПРАВИЛО ДЛЯ SEARCH-БЛОКА:")

        lines.append(
            "   SEARCH-блок должен начинаться с ПЕРВОГО ИМПОРТА файла "
            "и заканчиваться КОНЦОМ целевой функции."
        )

        lines.append(
            "   Копируй SEARCH ТОЛЬКО из секции '**ТОЧНЫЙ код для "
            "SEARCH-блока**' выше — символ в символ."
        )

        lines.append(
            "   НЕ включай в SEARCH комментарии вроде "
            "'# --- импорты файла ---' — их НЕТ в реальном файле."
        )

        lines.append(
            "   Если нужно добавить новый импорт, добавь его в REPLACE "
            "в начало блока импортов — НЕ создавай отдельный fix-блок."
        )

        lines.append("")

        # ============================================================
        # EXCEPTIONS
        # ============================================================

        lines.append(
            "23. УНИВЕРСАЛЬНЫЕ ПРАВИЛА ОБРАБОТКИ ИСКЛЮЧЕНИЙ:"
        )

        lines.append(
            "   - Оценивай семантику операции, а не только наличие "
            "исключения в текущем коде."
        )

        lines.append(
            "   - Если отсутствие данных является штатной ситуацией "
            "для конкретной операции, обеспечь graceful degradation "
            "(например, пропуск элемента с логом)."
        )

        lines.append(
            "   - Если отсутствие данных означает невозможность "
            "корректно продолжить операцию, используй fail-fast "
            "с явным исключением."
        )

        lines.append(
            "   - Контекст `цикл/batch` сам по себе НЕ является достаточным "
            "основанием для `continue`. Определи ожидаемую семантику "
            "операции из всего доступного контекста."
        )

        lines.append(
            "   - Контекст `единичная операция` сам по себе НЕ является "
            "достаточным основанием для `raise`. Также определи контракт."
        )

        lines.append(
            "   - Не меняй `raise` на `continue` или наоборот только "
            "для того, чтобы тесты начали проходить."
        )

        lines.append(
            "   - Исправление должно соответствовать бизнес-смыслу "
            "операции и существующим ожиданиям тестов."
        )

        lines.append(
            "   - Если добавляешь логирование, добавь `import logging` "
            "и `logger = logging.getLogger(__name__)` в начало файла "
            "(в блок импортов, ДО класса)."
        )

        lines.append(
            "   - НИКОГДА не вставляй `import` или `logger = ...` "
            "внутрь класса или функции."
        )

        lines.append("")

        # ============================================================
        # MONKEYPATCH
        # ============================================================

        lines.append("24. ПРАВИЛА MONKEYPATCH:")

        lines.append("   ✅ ПРАВИЛЬНО:")
        lines.append(
            "      monkeypatch.setattr(ClassName, 'method_name', mock_func)"
        )

        lines.append("   ❌ НЕПРАВИЛЬНО (вызывает AttributeError):")
        lines.append(
            "      monkeypatch.setattr('module.ClassName', 'method', mock)"
        )

        lines.append(
            "   Всегда передавай САМ КЛАСС, не строку с его именем."
        )

        # ============================================================
        # ФОРМАТ ОТВЕТА
        # ============================================================

        lines.append("\n```fix")
        lines.append("FILE: <относительный_путь_к_файлу>")
        lines.append("<<<<<<< SEARCH")
        lines.append(
            "<ТОЧНЫЙ код из секции '**ТОЧНЫЙ код для SEARCH-блока**' — "
            "начинается с импортов, заканчивается функцией>"
        )
        lines.append("=======")
        lines.append(
            "<исправленный код: новые импорты + импорты + класс + "
            "исправленная функция>"
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
            "monkeypatch, который проверяет исправленное поведение "
            "и обнаруживает исходную ошибку>"
        )
        lines.append("```")

        return "\n".join(lines)


async def build_llm_context(idx, project_root: str, analysis: dict) -> dict:
    """Обратно-совместимая обёртка."""
    return await ContextBuilder(project_root).build_llm_context(idx, analysis)


def render_llm_prompt(ctx: dict) -> str:
    """Обратно-совместимая обёртка."""
    return ContextBuilder.render_llm_prompt(ctx)
