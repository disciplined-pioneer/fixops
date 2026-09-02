import os
import sys
import pytest
from pathlib import Path
from code_intel.executor import FixExecutor

# Создадим фиктивный проект для теста
@pytest.fixture
def temp_project(tmp_path):
    project_root = tmp_path / "my_project"
    project_root.mkdir()
    return str(project_root)

def test_run_tests_success(temp_project, monkeypatch):
    # Создаем тест, который проходит
    test_file = Path(temp_project) / "test_ok.py"
    test_file.write_text("def test_ok(): assert True")
    
    executor = FixExecutor(temp_project)
    # Используем sys.executable для запуска pytest, чтобы быть уверенным в окружении
    result = executor.run_tests([sys.executable, "-m", "pytest", str(test_file)])
    
    assert result.success is True
    assert result.result_type == "SUCCESS"

def test_run_tests_code_failure(temp_project, monkeypatch):
    # Создаем тест, который падает
    test_file = Path(temp_project) / "test_fail.py"
    test_file.write_text("def test_fail(): assert False")
    
    executor = FixExecutor(temp_project)
    result = executor.run_tests([sys.executable, "-m", "pytest", str(test_file)])
    
    assert result.success is False
    assert result.result_type == "CODE_FAILURE"

def test_run_tests_infra_failure(temp_project, monkeypatch):
    # Запускаем pytest с несуществующим файлом, что вызывает ошибку collection (код 4)
    executor = FixExecutor(temp_project)
    result = executor.run_tests([sys.executable, "-m", "pytest", "non_existent_file.py"])
    
    assert result.success is False
    assert result.result_type == "INFRA_FAILURE"
