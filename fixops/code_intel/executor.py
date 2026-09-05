import os
import re
import ast
import difflib
import subprocess

from dataclasses import dataclass
from pathlib import Path


@dataclass
class TestResult:
    success: bool
    return_code: int
    stdout: str
    stderr: str
    result_type: str  # 'SUCCESS', 'CODE_FAILURE', 'INFRA_FAILURE'


class FixExecutor:

    def __init__(self, project_root: str):
        self.project_root = Path(project_root)

    @staticmethod
    def _remove_duplicate_returns(content: str) -> str:
        """Удаляет дублированные return statements в функциях."""
        try:
            tree = ast.parse(content)
        except SyntaxError:
            return content  # Если синтаксис сломан, не трогаем

        lines = content.split("\n")
        lines_to_remove = set()

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                returns = []
                for child in ast.walk(node):
                    if isinstance(child, ast.Return):
                        returns.append(child.lineno)

                # Если есть дублированные return на разных строках
                if len(returns) > 1:
                    # Проверяем, идут ли они подряд
                    for i in range(len(returns) - 1):
                        if returns[i + 1] == returns[i] + 1:
                            # Второй return — дубль, помечаем для удаления
                            lines_to_remove.add(returns[i + 1] - 1)  # 0-based

        # Удаляем помеченные строки
        if lines_to_remove:
            new_lines = [
                line for idx, line in enumerate(lines)
                if idx not in lines_to_remove
            ]
            return "\n".join(new_lines)

        return content

    @staticmethod
    def _normalize(text: str) -> str:
        """Нормализует текст для сравнения: унифицирует окончания строк,
        убирает концевые пробелы на каждой строке."""
        # Унифицируем CRLF -> LF
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        # Убираем trailing whitespace на каждой строке
        lines = [line.rstrip() for line in text.split("\n")]
        # Убираем пустые строки в конце
        while lines and lines[-1] == "":
            lines.pop()
        return "\n".join(lines)

    @staticmethod
    def _find_best_match(search: str, content: str) -> tuple[str | None, float]:
        """Ищет наилучшее совпадение search в content с помощью difflib.
        Возвращает (найденный фрагмент, коэффициент схожести)."""
        search_norm = FixExecutor._normalize(search)
        content_norm = FixExecutor._normalize(content)

        # Сначала точное совпадение
        if search_norm in content_norm:
            return search, 1.0

        # Ищем построчно через SequenceMatcher
        search_lines = search_norm.split("\n")
        content_lines = content_norm.split("\n")

        best_ratio = 0.0
        best_start = -1
        best_end = -1

        n_search = len(search_lines)
        # Скользящее окно по content
        for i in range(len(content_lines) - n_search + 1):
            window = content_lines[i:i + n_search]
            matcher = difflib.SequenceMatcher(None, search_lines, window)
            ratio = matcher.ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_start = i
                best_end = i + n_search

        if best_ratio >= 0.85 and best_start >= 0:
            # Возвращаем оригинальные строки (с оригинальными окончаниями)
            original_lines = content.split("\n")
            matched = "\n".join(original_lines[best_start:best_end])
            return matched, best_ratio

        return None, best_ratio

    def apply_fix(self, response: str) -> tuple[str | None, bool]:
        # Ищем ВСЕ блоки fix
        fix_blocks = re.findall(r"```fix\s*(.*?)```", response, re.DOTALL)

        if not fix_blocks:
            raise ValueError("LLM response does not contain ```fix block")

        any_changed = False
        last_file_path = None

        for idx, fix_block in enumerate(fix_blocks, 1):
            match = re.search(
                r"FILE:\s*(.+?)\n"
                r"<<<<<<< SEARCH\n"
                r"(.*?)"
                r"\n=======\n"
                r"(.*?)"
                r"\n>>>>>>> REPLACE",
                fix_block,
                re.DOTALL,
            )

            if not match:
                print(f"[fix #{idx}] Warning: invalid fix block format, skipping")
                continue

            file_path = match.group(1).strip()
            search = match.group(2)
            replace = match.group(3)

            path = self.project_root / file_path

            if not path.exists():
                print(f"[fix #{idx}] Warning: File not found, skipping: {file_path}")
                continue

            content = path.read_text(encoding="utf-8")

            # === ДЕТАЛЬНОЕ ЛОГИРОВАНИЕ ===
            print(f"\n{'='*70}")
            print(f"[fix #{idx}] Applying fix to: {file_path}")
            print(f"[fix #{idx}] SEARCH length: {len(search)} chars, "
                  f"{len(search.splitlines())} lines")
            print(f"[fix #{idx}] REPLACE length: {len(replace)} chars, "
                  f"{len(replace.splitlines())} lines")
            print(f"[fix #{idx}] SEARCH (repr, first 300 chars):")
            print(repr(search[:300]))
            print(f"\n[fix #{idx}] File content (repr, first 300 chars):")
            print(repr(content[:300]))

            # === ПОИСК СОВПАДЕНИЯ ===
            matched_fragment = None
            match_type = None

            # 1. Точное совпадение
            if search in content:
                matched_fragment = search
                match_type = "exact"
            else:
                # 2. Нормализованное совпадение (пробелы/окончания строк)
                search_norm = self._normalize(search)
                content_norm = self._normalize(content)
                if search_norm in content_norm:
                    matched_fragment = search
                    match_type = "normalized"
                else:
                    # 3. Fuzzy matching
                    fuzzy_match, ratio = self._find_best_match(search, content)
                    if fuzzy_match is not None:
                        matched_fragment = fuzzy_match
                        match_type = f"fuzzy ({ratio:.2%})"
                        print(f"[fix #{idx}] ⚠️ Using fuzzy match "
                              f"(similarity: {ratio:.2%})")
                        print(f"[fix #{idx}] Fuzzy matched fragment (repr):")
                        print(repr(fuzzy_match[:300]))

            if matched_fragment is None:
                print(f"\n[fix #{idx}] ❌ SEARCH block NOT FOUND in {file_path}")
                print(f"[fix #{idx}] Full SEARCH block:")
                print(search)
                print(f"\n[fix #{idx}] Full file content:")
                print(content)
                print(f"{'='*70}\n")
                continue

            # Применяем замену
            print(f"[fix #{idx}] ✅ Match type: {match_type}")
            new_content = content.replace(matched_fragment, replace, 1)
            path.write_text(new_content, encoding="utf-8")
            any_changed = True
            last_file_path = file_path
            print(f"{'='*70}\n")

        # Обработка тестов
        test_blocks = re.findall(r"```test\s*(.*?)```", response, re.DOTALL)
        for idx, test_block in enumerate(test_blocks, 1):
            file_match = re.search(r"FILE:\s*(.+?)\n(.*)", test_block, re.DOTALL)
            if file_match:
                test_path = self.project_root / file_match.group(1).strip()
                test_content = file_match.group(2).strip()
                test_path.parent.mkdir(parents=True, exist_ok=True)
                test_path.write_text(test_content, encoding="utf-8")
                any_changed = True
                print(f"[test #{idx}] ✅ Written: {test_path}")

        return last_file_path, any_changed

    def run_tests(self, command: list[str]) -> TestResult:
        env = os.environ.copy()
        env["PYTHONPATH"] = f"{self.project_root};{self.project_root.parent}"
        env["LOG_DIR"] = str(self.project_root / "logs")
        env["APP_LOG_DIR"] = str(self.project_root / "logs")

        process = subprocess.run(
            command,
            cwd=self.project_root,
            env=env,
            capture_output=True,
            text=True,
        )

        # Классификация результата
        if process.returncode == 0:
            result_type = "SUCCESS"
        elif process.returncode == 5:
            # pytest exit code 5 = no tests collected
            result_type = "CODE_FAILURE"  # ← это ошибка кода, а не инфраструктуры!
            print(f"DEBUG: No tests collected (pytest exit code 5).")
        elif ("ModuleNotFoundError" in (process.stderr + process.stdout)
              or "ImportError" in (process.stderr + process.stdout)):
            result_type = "INFRA_FAILURE"
        elif process.returncode == 1:
            result_type = "CODE_FAILURE"
        else:
            result_type = "INFRA_FAILURE"
            print(f"DEBUG: Infrastructure failure. Pytest returncode: {process.returncode}")

        return TestResult(
            success=process.returncode == 0,
            return_code=process.returncode,
            stdout=process.stdout,
            stderr=process.stderr,
            result_type=result_type,
        )
