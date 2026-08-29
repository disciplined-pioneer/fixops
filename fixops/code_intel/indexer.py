"""
indexer.py — AST-индексатор проекта.

Проходит по всем .py файлам проекта и для каждого извлекает через
модуль `ast` (без выполнения кода):

  - импорты (import X / from X import Y as Z)
  - классы и их методы
  - функции верхнего уровня
  - все вызовы (Call-узлы) внутри каждой функции/метода, с номером строки
  - для каждого вызова — простую эвристику "что это может быть":
      Name(...)                -> вызов функции/конструктора по имени
      self.method(...)         -> вызов метода текущего класса
      obj.method(...)          -> вызов метода на объекте (тип неизвестен
                                   без резолвера)
      Module.Class.method(...) -> вызов через явный путь модуля/класса
  - локальные присваивания вида `x = ClassName(...)` внутри функции —
    это нужно резолверу, чтобы понять тип переменной obj в obj.method()

Результат — плоский индекс в JSON-совместимой структуре, который
дальше используется resolver.py и graph.py.

Логика собрана в класс `ProjectIndexer`: один экземпляр индексирует
проект (сканирование + AST-разбор + экспорт в dict). Для обратной
совместимости сохранены модульные функции-обёртки `scan_project`,
`index_file` и `to_dict`.
"""

from __future__ import annotations

import ast
import os
from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class CallSite:
    line: int
    raw: str                 # текстовое представление вызова, напр. "self.repo.get"
    kind: str                # "name" | "self_attr" | "attr" | "dotted"
    base: Optional[str]      # то, на чём вызывается метод: "self", "promo", "PromoRepository"
    attr: Optional[str]      # имя метода/функции: "get"


@dataclass
class FunctionInfo:
    name: str
    qualname: str             # module.Class.method  либо  module.function
    lineno: int
    end_lineno: int
    is_method: bool
    class_name: Optional[str]
    calls: list = field(default_factory=list)          # list[CallSite]
    local_types: dict = field(default_factory=dict)     # var_name -> ClassName (из x = ClassName())


@dataclass
class ClassInfo:
    name: str
    lineno: int
    bases: list = field(default_factory=list)
    methods: list = field(default_factory=list)  # list[str] method names


@dataclass
class ModuleInfo:
    file: str            # относительный путь, напр. "services/discount.py"
    module: str           # dotted-путь модуля, напр. "services.discount"
    imports: list = field(default_factory=list)         # list[ImportInfo-like dict]
    classes: list = field(default_factory=list)          # list[ClassInfo]
    functions: list = field(default_factory=list)        # list[FunctionInfo] (top-level и методы)


class ProjectIndexer:
    """AST-индексатор проекта: сканирует каталог и строит список ModuleInfo."""

    IGNORE_DIRS = (".git", "__pycache__", "venv", ".venv", "node_modules")

    @staticmethod
    def _file_to_module(root: str, path: str) -> str:
        rel = os.path.relpath(path, root)
        rel = rel[:-3] if rel.endswith(".py") else rel
        return rel.replace(os.sep, ".")

    @staticmethod
    def _dotted_call_name(node: ast.AST) -> tuple[str, Optional[str], Optional[str]]:
        """Возвращает (raw_text, base, attr) для узла вызываемого выражения."""
        if isinstance(node, ast.Name):
            return node.id, None, node.id
        if isinstance(node, ast.Attribute):
            parts = []
            cur = node
            while isinstance(cur, ast.Attribute):
                parts.append(cur.attr)
                cur = cur.value
            if isinstance(cur, ast.Name):
                parts.append(cur.id)
            parts.reverse()
            raw = ".".join(parts)
            base = ".".join(parts[:-1]) if len(parts) > 1 else None
            attr = parts[-1]
            return raw, base, attr
        return ast.dump(node), None, None

    def scan(self, root: str, ignore_dirs: tuple = IGNORE_DIRS) -> list[ModuleInfo]:
        """Проходит по проекту и индексирует каждый .py файл. Это и есть шаг 1 —
        построение индекса без чтения всего проекта в контекст модели целиком."""
        modules = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in ignore_dirs]
            for fn in filenames:
                if fn.endswith(".py"):
                    full = os.path.join(dirpath, fn)
                    try:
                        modules.append(self.index_file(root, full))
                    except SyntaxError:
                        continue
        return modules

    def index_file(self, root: str, path: str) -> ModuleInfo:
        with open(path, "r", encoding="utf-8") as f:
            source = f.read()
        tree = ast.parse(source, filename=path)

        mod = ModuleInfo(
            file=os.path.relpath(path, root).replace(os.sep, "/"),
            module=self._file_to_module(root, path),
        )

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    mod.imports.append({"module": alias.name, "asname": alias.asname, "kind": "import"})
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                for alias in node.names:
                    mod.imports.append({
                        "module": module,
                        "name": alias.name,
                        "asname": alias.asname,
                        "kind": "from",
                    })

        # top-level functions
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                mod.functions.append(self._index_function(node, mod.module, is_method=False, class_name=None))
            elif isinstance(node, ast.ClassDef):
                cls = ClassInfo(name=node.name, lineno=node.lineno,
                                 bases=[b.id for b in node.bases if isinstance(b, ast.Name)])
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        cls.methods.append(item.name)
                        mod.functions.append(
                            self._index_function(item, mod.module, is_method=True, class_name=node.name)
                        )
                mod.classes.append(cls)

        return mod

    def _index_function(self, node, module: str, is_method: bool, class_name: Optional[str]) -> FunctionInfo:
        qualname = f"{module}.{class_name}.{node.name}" if class_name else f"{module}.{node.name}"
        fv = _FunctionVisitor()
        for stmt in node.body:
            fv.visit(stmt)
        return FunctionInfo(
            name=node.name,
            qualname=qualname,
            lineno=node.lineno,
            end_lineno=getattr(node, "end_lineno", node.lineno),
            is_method=is_method,
            class_name=class_name,
            calls=fv.calls,
            local_types=fv.local_types,
        )

    def to_dict(self, modules: list[ModuleInfo]) -> dict:
        return {
            "modules": [
                {
                    "file": m.file,
                    "module": m.module,
                    "imports": m.imports,
                    "classes": [asdict(c) for c in m.classes],
                    "functions": [asdict(fn) | {"calls": [asdict(c) for c in fn.calls]} for fn in m.functions],
                }
                for m in modules
            ]
        }


class _FunctionVisitor(ast.NodeVisitor):
    """Собирает вызовы и локальные присваивания типов внутри одной функции."""

    def __init__(self):
        self.calls: list[CallSite] = []
        self.local_types: dict[str, str] = {}

    def visit_Assign(self, node: ast.Assign):
        # x = ClassName(...)  -> запоминаем предполагаемый тип x
        if isinstance(node.value, ast.Call):
            raw, base, attr = ProjectIndexer._dotted_call_name(node.value.func)
            if attr and attr[0].isupper():  # эвристика: похоже на конструктор класса
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        self.local_types[target.id] = attr
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call):
        raw, base, attr = ProjectIndexer._dotted_call_name(node.func)
        if base == "self":
            kind = "self_attr"
        elif base is None and attr is not None and raw == attr:
            kind = "name"
        elif base and "." in base:
            kind = "dotted"
        else:
            kind = "attr"
        self.calls.append(CallSite(line=node.lineno, raw=raw, kind=kind, base=base, attr=attr))
        self.generic_visit(node)


def scan_project(root: str, ignore_dirs: tuple = ProjectIndexer.IGNORE_DIRS) -> list[ModuleInfo]:
    """Обратно-совместимая обёртка над ProjectIndexer.scan."""
    return ProjectIndexer().scan(root, ignore_dirs)


def index_file(root: str, path: str) -> ModuleInfo:
    """Обратно-совместимая обёртка над ProjectIndexer.index_file."""
    return ProjectIndexer().index_file(root, path)


def to_dict(modules: list[ModuleInfo]) -> dict:
    """Обратно-совместимая обёртка над ProjectIndexer.to_dict."""
    return ProjectIndexer().to_dict(modules)