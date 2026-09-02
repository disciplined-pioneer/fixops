
import os
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
    result_type: str  # 'SUCCESS', 'CODE_FAILURE', 'INFRA_FAILURE'


class FixExecutor:

    def __init__(self, project_root: str):

        self.project_root = Path(project_root)

    # Применяем Fix
    def apply_fix(
        self,
        response: str,
    ) -> tuple[str, bool]:

        # 1. Применяем fix
        fix_match = re.search(
            r"```fix\s*(.*?)```",
            response,
            re.DOTALL,
        )

        if not fix_match:
            raise ValueError(
                "LLM response does not contain ```fix block"
            )

        fix_block = fix_match.group(1)

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

        content = path.read_text(encoding="utf-8")

        if search not in content:
            raise ValueError(f"SEARCH block not found in {file_path}")

        if content.count(search) > 1:
            raise ValueError(f"SEARCH block appears multiple times in {file_path}")

        new_content = content.replace(search, replace, 1)
        path.write_text(new_content, encoding="utf-8")

        # 2. Применяем test (если есть)
        test_match = re.search(
            r"```test\s*(.*?)```",
            response,
            re.DOTALL,
        )
        if test_match:
            test_block = test_match.group(1)
            file_match = re.search(r"FILE:\s*(.+?)\n(.*)", test_block, re.DOTALL)
            if file_match:
                test_path = self.project_root / file_match.group(1).strip()
                test_content = file_match.group(2).strip()
                test_path.parent.mkdir(parents=True, exist_ok=True)
                test_path.write_text(test_content, encoding="utf-8")

        return file_path, True

    # Запуск тестов
    def run_tests(
        self,
        command: list[str],
    ) -> TestResult:

        # Добавляем и корень проекта, и его родителя в PYTHONPATH
        env = os.environ.copy()
        env["PYTHONPATH"] = f"{self.project_root};{self.project_root.parent}"

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
        # Проверяем stdout и stderr на наличие ошибок импорта
        elif "ModuleNotFoundError" in (process.stderr + process.stdout) or "ImportError" in (process.stderr + process.stdout):
            result_type = "INFRA_FAILURE"
            print(f"DEBUG: Infrastructure Failure detected (Import/Module Error).")
            print(f"DEBUG: Stdout: {process.stdout}")
            print(f"DEBUG: Stderr: {process.stderr}")
        elif process.returncode == 1:
            result_type = "CODE_FAILURE"
        else:
            result_type = "INFRA_FAILURE"
            print(f"DEBUG: Infrastructure Failure detected. Pytest returncode: {process.returncode}")
            print(f"DEBUG: Stdout: {process.stdout}")
            print(f"DEBUG: Stderr: {process.stderr}")

        return TestResult(
            success=process.returncode == 0,
            return_code=process.returncode,
            stdout=process.stdout,
            stderr=process.stderr,
            result_type=result_type
        )
