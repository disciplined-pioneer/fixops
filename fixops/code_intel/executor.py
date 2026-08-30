
import re
import subprocess

from dataclasses import dataclass
from pathlib import Path


@dataclass
class TestResult:
    success: bool
    return_code: int
    stdout: str
    stderr: str


class FixExecutor:

    def __init__(self, project_root: str):

        self.project_root = Path(project_root)

    # Применяем Fix
    def apply_fix(
        self,
        response: str,
    ) -> tuple[str, bool]:

        match = re.search(
            r"```fix\s*(.*?)```",
            response,
            re.DOTALL,
        )

        if not match:
            raise ValueError(
                "LLM response does not contain ```fix block"
            )

        fix_block = match.group(1)

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
            raise ValueError(
                "Invalid fix format"
            )

        file_path = match.group(1).strip()

        search = match.group(2)

        replace = match.group(3)

        path = self.project_root / file_path

        if not path.exists():
            raise FileNotFoundError(
                f"File not found: {file_path}"
            )

        content = path.read_text(
            encoding="utf-8"
        )

        # Защита от неправильного SEARCH
        if search not in content:
            raise ValueError(
                f"SEARCH block not found in {file_path}"
            )

        # Очень важная проверка.
        # Нельзя молча заменить несколько одинаковых блоков.
        if content.count(search) > 1:
            raise ValueError(
                f"SEARCH block appears multiple times "
                f"in {file_path}"
            )

        new_content = content.replace(
            search,
            replace,
            1,
        )

        path.write_text(
            new_content,
            encoding="utf-8",
        )

        return file_path, True

    # Запуск тестов
    def run_tests(
        self,
        command: list[str],
    ) -> TestResult:

        process = subprocess.run(
            command,
            cwd=self.project_root,
            capture_output=True,
            text=True,
        )

        return TestResult(
            success=process.returncode == 0,
            return_code=process.returncode,
            stdout=process.stdout,
            stderr=process.stderr,
        )
