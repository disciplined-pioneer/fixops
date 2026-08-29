"""
analyze_error.py — статический анализ ошибки по графу вызовов проекта.

Пути вписываются прямо в код ниже (блок КОНФИГУРАЦИЯ): путь к корню
проекта и путь к файлу с логами. Запуск обычный:

    python analyze_error.py

Скрипт:
  - читает лог-файл проекта (<корень проекта>/logs/app.log) и из последней
    ERROR-записи достаёт ошибку в формате {file, line, function, error};
  - индексирует проект (AST, без выполнения кода);
  - строит граф вызовов;
  - находит узел ошибки и раскладывает его на цепочку callers/callees;
  - собирает финальный промпт для LLM с реальным исходным кодом функций.

Все артефакты, как в demo.py, складываются в каталог логов пайплайна (LOGS_DIR):
  - index.json               — AST-индекс проекта
  - graph.json               — граф вызовов (для визуализации)
  - last_error_analysis.json — цепочка/координаты ошибки
  - llm_prompt.md            — ИТОГОВЫЙ промпт, который уходит в LLM
"""
import os
import sys
import json

from config import settings
from code_intel.html_view import save_html_view
from code_intel.graph import GraphBuilder
from code_intel.indexer import ProjectIndexer
from code_intel.error_analyzer import ErrorAnalyzer
from code_intel.context_builder import ContextBuilder
from code_intel.resolver import ProjectIndex, CallResolver


class ErrorLoader:
    """Читает последнюю ERROR-запись из хвоста лог-файла проекта (loguru, serialize=True).

    Читаются не все строки, а только последние LOG_TAIL_LINES — ошибка почти
    всегда в конце лога. Формат строки — JSON:
    {"text": ..., "record": {"level": {...}, "extra": {...}}}.
    Из extra последней ERROR-записи (checkout failed в reproduce.py) достаются
    координаты ошибки: file / line / function / error.
    """

    @staticmethod
    def _tail(path: str, n: int) -> list[str]:
        """Читает последние n строк файла, не загружая его целиком."""
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            block = 8192
            data = b""
            while size > 0 and data.count(b"\n") <= n:
                read = min(block, size)
                size -= read
                f.seek(size)
                data = f.read(read) + data
        return [
            ln.decode("utf-8", errors="replace")
            for ln in data.splitlines()[-n:]
        ]

    @staticmethod
    def from_file(path: str, tail: int = settings.analysis.LOG_TAIL_LINES) -> dict:
        lines = ErrorLoader._tail(path, tail)
        if not lines:
            raise ValueError(f"Лог-файл пуст: {path}")

        for raw in reversed(lines):
            try:
                record = json.loads(raw).get("record", {})
            except json.JSONDecodeError:
                continue  # не-json строка (например, обрыв записи) — пропускаем
            level = (record.get("level") or {}).get("name")
            extra = record.get("extra") or {}
            if level == "ERROR" and all(extra.get(k) is not None for k in settings.analysis.REQUIRED_ERROR_KEYS):
                return {
                    "file": extra["file"],
                    "line": int(extra["line"]),
                    "function": extra["function"],
                    "error": extra["error"],
                }

        raise ValueError(
            f"В последних {tail} строках лога {path} не найдено ERROR-записи "
            f"с координатами ошибки ({', '.join(settings.analysis.REQUIRED_ERROR_KEYS)}). "
            f"Запустите python sample_app/reproduce.py или увеличьте LOG_TAIL_LINES"
        )


class AnalyzeJob:
    """Полный статический анализ одного лога ошибки в заданном проекте."""

    def __init__(self, project_root: str, error_log: dict,
                 logs_dir: str | None = None, extra_ignore_dirs: tuple = ()):
        self.project_root = os.path.abspath(project_root)
        self.error_log = error_log
        self.logs_dir = os.path.abspath(logs_dir or os.path.join(self.project_root, "logs"))
        self.extra_ignore_dirs = tuple(extra_ignore_dirs)

    def analyze(self) -> dict:
        """Возвращает результат анализа + артефакты для сохранения."""
        indexer = ProjectIndexer()
        ignore_dirs = ProjectIndexer.IGNORE_DIRS + self.extra_ignore_dirs
        modules = indexer.scan(self.project_root, ignore_dirs=ignore_dirs)

        idx = ProjectIndex(modules)
        graph = GraphBuilder(CallResolver(idx)).build(idx)

        analyzer = ErrorAnalyzer(idx, graph)
        result = analyzer.analyze_error(self.error_log)

        artifacts: dict[str, object] = {}
        artifacts = {
            "index": indexer.to_dict(modules),
            "graph": graph.to_dict(),
            "analysis": result,
        }
        if result.get("resolved_node") is None:
            return artifacts

        ctx = ContextBuilder(self.project_root).build_llm_context(idx, result)
        artifacts["prompt"] = ContextBuilder.render_llm_prompt(ctx)
        return artifacts

    @staticmethod
    def _write_json(path: str, data: dict) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _save_artifacts(self, artifacts: dict) -> None:
        os.makedirs(self.logs_dir, exist_ok=True)
        self._write_json(os.path.join(self.logs_dir, "index.json"), artifacts["index"])
        self._write_json(os.path.join(self.logs_dir, "graph.json"), artifacts["graph"])
        self._write_json(os.path.join(self.logs_dir, "last_error_analysis.json"), artifacts["analysis"])
        if artifacts.get("prompt"):
            with open(os.path.join(self.logs_dir, "llm_prompt.md"), "w", encoding="utf-8") as f:
                f.write(artifacts["prompt"])
        save_html_view(
            artifacts["graph"],
            artifacts["analysis"],
            os.path.join(self.logs_dir, "graph_view.html"),
        )

    def run(self) -> int:

        artifacts = self.analyze()
        result = artifacts["analysis"]

        self._save_artifacts(artifacts)

        if result.get("resolved_node") is None:
            print(result["message"])
            return 1

        # print(ErrorAnalyzer.render_chain_text(result))
        # print(artifacts.get("prompt", ""))

        saved = [os.path.join(self.logs_dir, name) for name in
                 ("index.json", "graph.json", "last_error_analysis.json",
                  "graph_view.html", "llm_prompt.md")]
        print("\nСохранено в " + self.logs_dir + ":")
        for path in saved:
            print(f"  - {path}")
        return 0


def main() -> int:
    # --- КОНФИГУРАЦИЯ ---
    # Укажите здесь путь к папке проекта, который нужно проанализировать
    project_path = "sample_app"
    # --------------------

    project_root = os.path.abspath(project_path)
    error_log_path = os.path.join(project_root, "logs", "app.log")
    logs_dir = os.path.join(project_root, ".fixops")

    if not os.path.isdir(project_root):
        sys.stderr.write(f"Не найдена папка проекта: {project_root}\n")
        return 2
    if not os.path.isfile(error_log_path):
        sys.stderr.write(f"Не найден лог-файл проекта: {error_log_path}\n")
        return 2

    try:
        error_log = ErrorLoader.from_file(error_log_path)
    except (OSError, ValueError) as exc:
        sys.stderr.write(f"Ошибка чтения лога: {exc}\n")
        return 2

    job = AnalyzeJob(project_root, error_log, logs_dir=logs_dir,
                     extra_ignore_dirs=settings.analysis.EXTRA_IGNORE_DIRS)
    return job.run()


if __name__ == "__main__":
    raise SystemExit(main())
